"""Pure deterministic linker from Analyzer AST/external nodes to typed edges."""
from __future__ import annotations

from dataclasses import dataclass

from graph.schema.graph_edges import CallsSql, ReadsWrites, ReferTo, UsesConfig
from graph.schema.graph_nodes import CodeNode, Constant, DocumentNode, ExternalChunk, Module, Table, UnresolvedRef, Variable
from graph.schema.node_ids import unresolved_ref_id
from pipeline.steps.step5_linking.link_external.const_propagation import immutable_symbols, referenced_immutable_keys
from pipeline.steps.step5_linking.link_external.key_index import dynamic_key_candidates, match_keys
from pipeline.steps.step5_linking.link_external.sql_tables import TableRef, extract_table_refs
from pipeline.steps.step3_load_external.chunking import (
    ExternalChunkIntegrityError,
    reconstruct_external_chunk,
)
from pipeline.steps.step5_linking.link_external.document_mentions import link_exact_document_mentions


class ExternalLinkContractError(ValueError):
    pass


@dataclass(frozen=True)
class LinkOutcome:
    external_edges: list[object]
    reads_writes_edges: list[ReadsWrites]
    unresolved_refs: list[UnresolvedRef]


def _edge_identity(edge: object) -> tuple[type, str, str]:
    return type(edge), getattr(edge, "source_id"), getattr(edge, "target_id")


def _direct_edge(code: CodeNode, chunk: ExternalChunk, line: int, via_const: str = "") -> object:
    evidence_line = max(0, line)
    if chunk.kind == "sql":
        return CallsSql(code.id, chunk.id, code.file_path, evidence_line, via_const)
    if chunk.kind in ("config", "custom"):
        return UsesConfig(code.id, chunk.id, code.file_path, evidence_line, via_const)
    if chunk.kind == "artifact":
        return ReferTo(code.id, chunk.id, code.file_path, evidence_line, "")
    raise ExternalLinkContractError(f"unsupported external anchor kind: {chunk.kind!r}")


def _module_fqn(module: Module) -> str:
    value = module.id.removeprefix("code:")
    return value.replace(":", ".")


def _table_lookup(tables: list[Table], ref: TableRef) -> tuple[Table | None, str]:
    candidates = [
        table for table in tables
        if table.name.upper() == ref.name and (not ref.schema or table.schema.upper() == ref.schema)
    ]
    if len(candidates) == 1:
        return candidates[0], ""
    if not candidates:
        return None, "not present in DDL"
    return None, "ambiguous across DDL schemas"


def link_external(
    *,
    code_nodes: list[CodeNode],
    modules: list[Module],
    variables: list[Variable],
    constants: list[Constant],
    chunks: list[ExternalChunk],
    tables: list[Table],
    routine_ids: set[str],
    document_nodes: list[DocumentNode] | None = None,
) -> LinkOutcome:
    """Link exact code observations, FQNs and SQL table references without LLM."""
    anchors = sorted(
        (chunk for chunk in chunks if chunk.part_index == 0 and chunk.key),
        key=lambda chunk: (chunk.key, chunk.id),
    )
    parts_by_anchor: dict[str, list[ExternalChunk]] = {}
    anchor_ids = {anchor.id for anchor in anchors}
    for chunk in chunks:
        if chunk.part_index:
            if chunk.anchor_id not in anchor_ids:
                raise ExternalLinkContractError(f"orphan external part: {chunk.id}")
            parts_by_anchor.setdefault(chunk.anchor_id, []).append(chunk)
    anchors_by_key: dict[str, list[ExternalChunk]] = {}
    for anchor in anchors:
        anchors_by_key.setdefault(anchor.key, []).append(anchor)

    routines = sorted(
        (node for node in code_nodes if node.id in routine_ids),
        key=lambda node: node.id,
    )
    external_edges: list[object] = []
    edge_seen: set[tuple[type, str, str]] = set()
    matched_anchors: set[str] = set()

    def add_external(edge: object) -> None:
        identity = _edge_identity(edge)
        if identity not in edge_seen:
            edge_seen.add(identity)
            external_edges.append(edge)

    for edge in link_exact_document_mentions(document_nodes or [], code_nodes, routine_ids):
        add_external(edge)

    # Direct literal use: key-index lookup has the same result as exhaustive
    # matching but does not multiply every routine by every external key.
    for code in routines:
        for key, relative_line in match_keys(code.code_text, anchors_by_key):
            evidence_line = code.start_line + relative_line - 1 if code.start_line else relative_line
            for anchor in anchors_by_key[key]:
                add_external(_direct_edge(code, anchor, evidence_line))
                matched_anchors.add(anchor.id)

    # One-step immutable propagation consumes step2 Constant/Variable facts.
    symbols = immutable_symbols(variables, constants, set(anchors_by_key))
    for code in routines:
        for key, symbol_name, evidence_line in referenced_immutable_keys(code, symbols):
            for anchor in anchors_by_key[key]:
                add_external(_direct_edge(code, anchor, evidence_line, symbol_name))
                matched_anchors.add(anchor.id)

    # Exact artifact class FQN → Analyzer Module. Module is the Analyzer-native
    # class/file entity; inventing a synthetic class CodeNode would corrupt AST truth.
    modules_by_fqn: dict[str, list[Module]] = {}
    for module in modules:
        modules_by_fqn.setdefault(_module_fqn(module), []).append(module)
    for anchor in anchors:
        if anchor.kind != "artifact":
            continue
        for fqn in anchor.related_fqns:
            targets = modules_by_fqn.get(fqn, [])
            if len(targets) != 1:
                continue
            add_external(ReferTo(
                anchor.id,
                targets[0].id,
                anchor.file_path,
                anchor.start_line,
                fqn,
            ))
            matched_anchors.add(anchor.id)

    unresolved: list[UnresolvedRef] = []
    reads_writes: list[ReadsWrites] = []
    rw_seen: set[tuple[str, str, str]] = set()
    sql_table_map: dict[str, list[tuple[Table, TableRef]]] = {}

    def add_rw(edge: ReadsWrites) -> None:
        identity = (edge.source_id, edge.target_id, edge.relation)
        if identity not in rw_seen:
            rw_seen.add(identity)
            reads_writes.append(edge)

    # SQL anchor → DDL table facts. Hash validation precedes extraction so a
    # damaged part sequence cannot silently produce a partial SQL graph.
    for anchor in anchors:
        if anchor.kind != "sql":
            continue
        try:
            full_text = reconstruct_external_chunk(anchor, parts_by_anchor.get(anchor.id, []))
        except ExternalChunkIntegrityError as error:
            raise ExternalLinkContractError(str(error)) from error
        for ref in extract_table_refs(full_text):
            table, detail = _table_lookup(tables, ref)
            if table is None:
                key = f"{ref.schema + '.' if ref.schema else ''}{ref.name}"
                unresolved.append(UnresolvedRef(
                    id=unresolved_ref_id("table", anchor.id, key, ref.op),
                    name=key,
                    target_type="TABLE",
                    key=key,
                    reason="table_not_in_ddl",
                    file_path=anchor.file_path,
                    detail=f"sql_key={anchor.key} op={ref.op}: {detail}",
                ))
                continue
            add_rw(ReadsWrites(
                source_id=anchor.id,
                target_id=table.id,
                relation=ref.relation,
                dml_type=ref.op,
                evidence_file=anchor.file_path,
                evidence_line=anchor.start_line,
            ))
            sql_table_map.setdefault(anchor.id, []).append((table, ref))

    # Code→SQL→TABLE derived facts use the same ReadsWrites writer/model.
    for edge in external_edges:
        if not isinstance(edge, CallsSql):
            continue
        for table, ref in sql_table_map.get(edge.target_id, []):
            add_rw(ReadsWrites(
                source_id=edge.source_id,
                target_id=table.id,
                relation=ref.relation,
                dml_type=ref.op,
                evidence_file=edge.evidence_file,
                evidence_line=edge.evidence_line,
            ))

    for anchor in anchors:
        if anchor.id not in matched_anchors:
            unresolved.append(UnresolvedRef(
                id=unresolved_ref_id("key", anchor.id),
                name=anchor.key,
                target_type=anchor.kind,
                key=anchor.key,
                reason="key_not_found_in_code",
                file_path=anchor.file_path,
            ))
    for code in routines:
        for key in dynamic_key_candidates(code.code_text, anchors_by_key):
            unresolved.append(UnresolvedRef(
                id=unresolved_ref_id("dynamic", code.id, key),
                name=key,
                target_type="external_key",
                key=key,
                reason="dynamic_key_suspected",
                file_path=code.file_path,
                detail=f"routine={code.id}; literal concatenation prevents exact key proof",
            ))

    unresolved_by_id = {item.id: item for item in unresolved}
    return LinkOutcome(
        external_edges=external_edges,
        reads_writes_edges=reads_writes,
        unresolved_refs=[unresolved_by_id[key] for key in sorted(unresolved_by_id)],
    )
