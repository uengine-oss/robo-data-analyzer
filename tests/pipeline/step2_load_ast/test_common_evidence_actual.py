from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import tracemalloc
import uuid
from collections import Counter
from pathlib import Path

import pytest
from neo4j import AsyncGraphDatabase

from agents.skills.catalog import load_skill_contract
from graph.generation import publish_generation
from graph.nodes.call_occurrence import upsert_call_occurrences
from graph.nodes.import_occurrence import upsert_import_occurrences
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.constraints import ensure_schema
from pipeline.models.call_resolution import (
    call_resolution_from_storage,
    normalize_call_resolution_subset,
    summarize_call_resolution_ledger,
)
from pipeline.models.import_resolution import (
    build_import_resolution_ledger,
    import_resolution_from_storage,
    normalize_import_resolution_ledger,
    normalize_import_resolution_subset,
    summarize_import_resolution_ledger,
)
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
)
from search.corpus.node_detail import build_gwt_detail, build_node_detail
from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.source_ast_closure import verify_source_ast_closure
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache


FIXTURE = os.getenv("ROBO_SPEC131_CALL_FIXTURE", "")
REPORT = os.getenv("ROBO_SPEC131_CALL_REPORT", "")
PARSER_REPORT = os.getenv("ROBO_SPEC131_PARSER_REPORT", "")


def _ledger_hash(rows: list[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _allowed_report(path: Path) -> bool:
    normalized = path.resolve().as_posix()
    return "/specs/131-cross-node-semantic-grounding/_runs/framework/" in normalized


def _source_free_test_frame(node_id: str) -> str:
    target = StructuralTarget(
        id=node_id, type="FUNCTION", owner_id="", file="(rollback-proof)",
        start_line=1, end_line=1,
    )
    target_ref = evidence_id(node_id, "TARGET", 0)
    profile = StructuralProfile(
        target=target,
        evidence={target_ref: EvidenceFact(
            id=target_ref, kind="TARGET", owner_id=node_id,
            file=target.file, start_line=1, end_line=1,
            attributes={
                "type": "FUNCTION", "signature": "", "name": node_id,
                "params": "", "return_type": "",
            },
        )},
    )
    return SemanticFrame(target=target, profile=profile, slots={}).canonical_json()


class _SharedTransactionClient:
    def __init__(self, transaction, generation_id: str):
        self.transaction = transaction
        self.generation_id = generation_id

    async def execute_write(self, callback):
        return await callback(self.transaction)


class _FrameworkDatabaseSchemaClient:
    def __init__(self, driver):
        self.driver = driver

    async def run_query(self, query: str, params: dict | None = None):
        async with self.driver.session(database="framework") as session:
            result = await session.run(query, **(params or {}))
            return await result.consume()

    async def run_read_query(
        self, query: str, params: dict | None = None,
    ) -> list[dict]:
        async with self.driver.session(database="framework") as session:
            result = await session.run(query, **(params or {}))
            return await result.data()


async def _prove_actual_occurrence_graph_rollback(
    ledger: list[dict], import_ledger: list[dict], sealed_ids: list[str],
) -> dict:
    """Publish complete call/import ledgers in one test-DB transaction, then erase it."""
    settings = SETTINGS.graph_database
    generation_id = f"spec131-call-occurrence-{uuid.uuid4().hex}"
    owner_files = {
        row["caller_id"]: row["file"] for row in ledger if row["caller_id"]
    }
    owner_ids = sorted(owner_files)
    owner_items = [
        {
            "id": owner_id,
            "file_path": owner_files[owner_id],
            "semantic_frame_json": _source_free_test_frame(owner_id),
        }
        for owner_id in owner_ids
    ]
    driver = AsyncGraphDatabase.driver(
        settings.uri,
        auth=(settings.user, settings.password),
    )
    await ensure_schema(_FrameworkDatabaseSchemaClient(driver))

    async def active_rows() -> list[dict]:
        async with driver.session(database="framework") as session:
            result = await session.run(
                """
MATCH (active:ANALYZER_ACTIVE_GENERATION {
  graph_owner: $graph_owner,
  key: 'active'
})
RETURN active.current_generation_id AS generation_id
""",
                graph_owner=ANALYZER_GRAPH_OWNER,
            )
            return await result.data()

    active_before = await active_rows()
    closure = None
    persisted_counts = None
    sealed_rows: list[dict] = []
    try:
        async with driver.session(database="framework") as session:
            transaction = await session.begin_transaction()
            try:
                await transaction.run(
                    """
CREATE (:ANALYZER_GENERATION {
  graph_owner: $graph_owner,
  run_id: $generation_id,
  status: 'building'
})
WITH 1 AS ignored
UNWIND $owners AS owner
CREATE (:ANALYZER_OWNED:FUNCTION {
  graph_owner: $graph_owner,
  generation_id: $generation_id,
  id: owner.id,
  file_path: owner.file_path,
  semantic_frame_json: owner.semantic_frame_json
})
""",
                    graph_owner=ANALYZER_GRAPH_OWNER,
                    generation_id=generation_id,
                    owners=owner_items,
                )
                client = _SharedTransactionClient(transaction, generation_id)
                assert await upsert_call_occurrences(client, ledger) == len(ledger)
                assert await upsert_import_occurrences(
                    client, import_ledger,
                ) == len(import_ledger)
                closure = await publish_generation(
                    client,
                    generation_id=generation_id,
                    quality_status="completed",
                    call_resolution_ledger=ledger,
                    import_resolution_ledger=import_ledger,
                )
                count_result = await transaction.run(
                    """
MATCH (occurrence:ANALYZER_OWNED:CALL_OCCURRENCE {
  graph_owner: $graph_owner,
  generation_id: $generation_id
})
OPTIONAL MATCH (owner:ANALYZER_OWNED)-[relation:HAS_CALL_OCCURRENCE]->(occurrence)
RETURN count(DISTINCT occurrence) AS occurrences,
       count(relation) AS owner_edges
""",
                    graph_owner=ANALYZER_GRAPH_OWNER,
                    generation_id=generation_id,
                )
                persisted_counts = dict(await count_result.single(strict=True))
                projection_result = await transaction.run(
                    """
MATCH (owner:ANALYZER_OWNED:FUNCTION {
  graph_owner: $graph_owner,
  generation_id: $generation_id
})-[relation:HAS_CALL_OCCURRENCE]->
      (occurrence:ANALYZER_OWNED:CALL_OCCURRENCE {
        graph_owner: $graph_owner,
        generation_id: $generation_id
      })
RETURN owner.id AS owner_id,
       labels(owner) AS owner_labels,
       properties(owner) AS owner_properties,
       properties(relation) AS relation_properties,
       occurrence.id AS occurrence_id,
       labels(occurrence) AS occurrence_labels,
       properties(occurrence) AS occurrence_properties
ORDER BY owner.id, occurrence.ordinal
""",
                    graph_owner=ANALYZER_GRAPH_OWNER,
                    generation_id=generation_id,
                )
                projection_rows = await projection_result.data()
                by_owner: dict[str, dict] = {}
                for row in projection_rows:
                    owner_id = row["owner_id"]
                    packet = by_owner.setdefault(owner_id, {
                        "node_rows": [{
                            "labels": row["owner_labels"],
                            "props": row["owner_properties"],
                        }],
                        "relationships": [],
                    })
                    packet["relationships"].append({
                        "type": "HAS_CALL_OCCURRENCE",
                        "direction": "out",
                        "properties": row["relation_properties"],
                        "other_id": row["occurrence_id"],
                        "other_name": "",
                        "other_labels": row["occurrence_labels"],
                        "other_properties": row["occurrence_properties"],
                    })
                api_occurrences: list[dict] = []
                mcp_occurrences: list[dict] = []
                for owner_id, packet in sorted(by_owner.items()):
                    detail = build_node_detail(
                        packet["node_rows"], packet["relationships"], [], [],
                        node_id=owner_id, require_complete_source=False,
                    )
                    assert "error" not in detail
                    owner_occurrences = detail["node"]["call_resolution"]["occurrences"]
                    api_occurrences.extend(owner_occurrences)
                    gwt = build_gwt_detail(detail, [])
                    assert "error" not in gwt
                    mcp_occurrences.extend(
                        gwt["node"]["linked_context"]["call_occurrences"]
                    )
                api_occurrences = normalize_call_resolution_subset(api_occurrences)
                mcp_occurrences = normalize_call_resolution_subset(mcp_occurrences)
                assert api_occurrences == ledger
                assert mcp_occurrences == ledger
                sealed_result = await transaction.run(
                    """
MATCH (occurrence:ANALYZER_OWNED:CALL_OCCURRENCE {
  graph_owner: $graph_owner,
  generation_id: $generation_id
})
WHERE occurrence.call_id IN $sealed_ids
RETURN properties(occurrence) AS properties
ORDER BY occurrence.call_id
""",
                    graph_owner=ANALYZER_GRAPH_OWNER,
                    generation_id=generation_id,
                    sealed_ids=sealed_ids,
                )
                sealed_rows = [
                    call_resolution_from_storage(row["properties"])
                    for row in await sealed_result.data()
                ]
                import_result = await transaction.run(
                    """
MATCH (occurrence:ANALYZER_OWNED:IMPORT_OCCURRENCE {
  graph_owner: $graph_owner,
  generation_id: $generation_id
})
RETURN properties(occurrence) AS properties
ORDER BY occurrence.ordinal
""",
                    graph_owner=ANALYZER_GRAPH_OWNER,
                    generation_id=generation_id,
                )
                persisted_imports = normalize_import_resolution_ledger([
                    import_resolution_from_storage(row["properties"])
                    for row in await import_result.data()
                ])
                assert persisted_imports == import_ledger

                api_imports: list[dict] = []
                mcp_imports: list[dict] = []
                imports_by_file: dict[str, list[dict]] = {}
                for occurrence in persisted_imports:
                    imports_by_file.setdefault(occurrence["file"], []).append(occurrence)
                owner_by_file: dict[str, str] = {}
                for owner_id, file_name in owner_files.items():
                    owner_by_file.setdefault(file_name, owner_id)
                assert set(imports_by_file) <= set(owner_by_file)
                for file_name, file_imports in sorted(imports_by_file.items()):
                    owner_id = owner_by_file[file_name]
                    detail = build_node_detail(
                        by_owner[owner_id]["node_rows"],
                        [], [], [], node_id=owner_id,
                        require_complete_source=False,
                        import_occurrences=file_imports,
                    )
                    assert "error" not in detail
                    api_imports.extend(
                        detail["node"]["import_resolution"]["occurrences"]
                    )
                    packet = build_gwt_detail(detail, [])
                    assert "error" not in packet
                    mcp_imports.extend(
                        packet["node"]["linked_context"]["import_occurrences"]
                    )
                api_imports = normalize_import_resolution_subset(api_imports)
                mcp_imports = normalize_import_resolution_subset(mcp_imports)
                assert api_imports == import_ledger
                assert mcp_imports == import_ledger
            finally:
                await transaction.rollback()

        async with driver.session(database="framework") as session:
            residue_result = await session.run(
                """
MATCH (n)
WHERE n.run_id = $generation_id OR n.generation_id = $generation_id
RETURN count(n) AS count
""",
                generation_id=generation_id,
            )
            residue = int((await residue_result.single(strict=True))["count"])
        active_after = await active_rows()
    finally:
        await driver.close()

    assert closure is not None and persisted_counts is not None
    assert persisted_counts == {
        "occurrences": len(ledger), "owner_edges": len(ledger),
    }
    assert closure["quality_status"] == "completed_with_findings"
    assert closure["call_occurrences"]["identities"] == ledger
    assert closure["import_occurrences"]["identities"] == import_ledger
    assert residue == 0
    assert active_after == active_before
    sealed_by_id = {row["call_id"]: row for row in ledger if row["call_id"] in sealed_ids}
    assert sealed_rows == [sealed_by_id[call_id] for call_id in sorted(sealed_ids)]
    return {
        "database": "framework",
        "schema_contract": "ensured",
        "transaction": "rolled_back",
        "routine_owner_ids": len(owner_ids),
        "occurrence_nodes": persisted_counts["occurrences"],
        "owner_edges": persisted_counts["owner_edges"],
        "api_owner_packets": len(by_owner),
        "api_occurrences": len(api_occurrences),
        "api_ledger_sha256": summarize_call_resolution_ledger(
            api_occurrences
        )["sha256"],
        "mcp_owner_packets": len(by_owner),
        "mcp_occurrences": len(mcp_occurrences),
        "mcp_ledger_sha256": summarize_call_resolution_ledger(
            mcp_occurrences
        )["sha256"],
        "import_occurrence_nodes": len(persisted_imports),
        "api_import_occurrences": len(api_imports),
        "api_import_ledger_sha256": summarize_import_resolution_ledger(
            api_imports
        )["sha256"],
        "mcp_import_occurrences": len(mcp_imports),
        "mcp_import_ledger_sha256": summarize_import_resolution_ledger(
            mcp_imports
        )["sha256"],
        "generation_node_count": closure["nodes"]["count"],
        "generation_edge_count": closure["edges"]["count"],
        "quality_status": closure["quality_status"],
        "ledger_sha256": closure["call_occurrences"]["sha256"],
        "import_ledger_sha256": closure["import_occurrences"]["sha256"],
        "sealed_direct_judgment_matched": len(sealed_rows),
        "residual_nodes": residue,
        "active_pointer_unchanged": active_after == active_before,
    }


@pytest.mark.skipif(
    not FIXTURE or not REPORT or not PARSER_REPORT,
    reason="set the three ROBO_SPEC131 call evidence paths for actual validation",
)
def test_actual_parser_facts_bind_to_framework_routines_exactly_once() -> None:
    fixture = Path(FIXTURE).resolve()
    report_path = Path(REPORT).resolve()
    parser_report_path = Path(PARSER_REPORT).resolve()
    assert fixture.is_dir()
    assert parser_report_path.is_file()
    assert _allowed_report(report_path)
    source_root = fixture / "source"
    ast_root = fixture / "analysis"
    cache = SourceFileCache()
    closure = verify_source_ast_closure(
        project_root=source_root,
        ast_root=ast_root,
        source_root=source_root,
        source_cache=cache,
    )

    def consume() -> tuple[
        list[dict], list[dict], list[dict], list[dict], list[dict], list,
        float, int,
    ]:
        sites: list[dict] = []
        macros: list[dict] = []
        imports: list[dict] = []
        completeness: list[dict] = []
        preprocessing: list[dict] = []
        nodes = []
        tracemalloc.start()
        started = time.perf_counter()
        for artifact in closure.artifacts:
            result = read_antlr_json(
                artifact.ast_path,
                source_root,
                expected_sha256=artifact.ast_sha256,
                source_language=artifact.language,
                source_cache=cache,
            )
            sites.extend(result.call_sites)
            macros.extend(result.macro_facts)
            imports.extend(result.import_refs)
            completeness.extend(result.evidence_completeness)
            preprocessing.extend(result.preprocessing_contexts)
            nodes.extend(result.code_nodes)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return (
            sites, macros, imports, completeness, preprocessing, nodes,
            elapsed, peak,
        )

    (
        first, macros, imports, completeness, preprocessing, nodes,
        elapsed, peak,
    ) = consume()
    (
        repeated, repeated_macros, repeated_imports, _, repeated_preprocessing, _,
        repeated_elapsed, repeated_peak,
    ) = consume()
    parser_report = json.loads(parser_report_path.read_text(encoding="utf-8"))
    expected_ids = [site["call_id"] for site in first]
    repeated_ids = [site["call_id"] for site in repeated]
    assert len(closure.artifacts) == parser_report["sourceFiles"] == 23
    assert len(first) == parser_report["callFacts"] == 12_113
    assert len(macros) == parser_report["macroFacts"] == 183
    assert len(imports) == parser_report["importFacts"] == 450
    assert len(expected_ids) == len(set(expected_ids))
    assert expected_ids == repeated_ids
    assert [fact["fact_id"] for fact in macros] == [
        fact["fact_id"] for fact in repeated_macros
    ]
    import_ids = [ref["import_id"] for ref in imports]
    assert import_ids == [ref["import_id"] for ref in repeated_imports]
    assert len(import_ids) == len(set(import_ids))
    import_ledger = build_import_resolution_ledger(imports)
    repeated_import_ledger = build_import_resolution_ledger(repeated_imports)
    assert import_ledger == repeated_import_ledger
    assert preprocessing == repeated_preprocessing
    assert _ledger_hash(expected_ids) == parser_report["callFactIdLedgerSha256"]
    assert _ledger_hash(
        [fact["fact_id"] for fact in macros]
    ) == parser_report["macroFactIdLedgerSha256"]
    assert _ledger_hash(import_ids) == parser_report["importFactIdLedgerSha256"]

    presence = Counter(site["presence_status"] for site in first)
    callee_kind = Counter(site["callee_kind"] for site in first)
    owner = Counter(site["owner_status"] for site in first)
    macro_presence = Counter(fact["presence_status"] for fact in macros)
    macro_kind = Counter(fact["macro_kind"] for fact in macros)
    import_presence = Counter(ref["presence_status"] for ref in imports)
    import_entries = [entry for ref in imports for entry in ref["entries"]]
    import_target_kind = Counter(entry["target_kind"] for entry in import_entries)
    import_kind = Counter(entry["import_kind"] for entry in import_entries)
    import_evidence_status = Counter(ref["evidence_status"] for ref in imports)
    assert presence == Counter(active=12_080, inactive=33)
    assert callee_kind == Counter(named=12_113)
    assert macro_presence == Counter(active=182, conditional=1)
    assert macro_kind == Counter(object=182, function=1)
    assert import_presence == Counter(active=449, inactive=1)
    assert import_target_kind == Counter(angle=450)
    assert import_kind == Counter(source_file=450)
    assert len(import_entries) == len(import_ledger) == 450
    assert import_evidence_status == Counter(complete=450)
    inactive_imports = [
        ref for ref in imports if ref["presence_status"] == "inactive"
    ]
    assert [
        (ref["import_id"], ref["source_id"], ref["entries"][0]["target"])
        for ref in inactive_imports
    ] == [(
        "4cb06d86a857bc112715b599876cb23dc52b831190eaf294ad26f57f3ea8ff7d",
        "zapamcom10110.c",
        "<zngmmcap20020.h>",
    )]
    import_outcomes = Counter(row["outcome"] for row in import_ledger)
    import_reasons = Counter(row["reason"] for row in import_ledger)
    assert import_outcomes == Counter(unresolved=449, excluded=1)
    assert import_reasons == Counter({
        "insufficient_import_binding_environment": 449,
        "inactive_import": 1,
    })
    assert all(not row["resolved_module_ids"] for row in import_ledger)
    assert sum(owner.values()) == len(first)
    assert all(site["call_id"] and site["source_id"] for site in first)

    assert len(preprocessing) == len(closure.artifacts) == 23
    assert len({row["source_id"] for row in preprocessing}) == 23
    assert all(
        row["status"] == "unresolved"
        and row["trust"] == "unresolved"
        and row["build"]["status"] == "unresolved"
        and row["build"]["population"] == 1
        and row["build"]["emitted"] == 0
        and row["build"]["explicitlyUnresolved"] == 1
        and row["build"]["reasons"]
            == ["insufficient_compilation_database"]
        and row["trace"]["status"] == "unresolved"
        and row["trace"]["population"] == 1
        and row["trace"]["emitted"] == 0
        and row["trace"]["explicitlyUnresolved"] == 1
        and row["trace"]["reasons"]
            == ["insufficient_preprocessing_build_context"]
        for row in preprocessing
    )
    preprocessing_ids = [
        evidence_id
        for row in preprocessing
        for evidence_id in (
            row["build"]["unresolvedEvidenceIds"]
            + row["trace"]["evidenceIds"]
        )
    ]
    assert len(preprocessing_ids) == len(set(preprocessing_ids)) == 46

    contract = load_skill_contract("proframe-c-pfm-dbio")
    classification = classify_call_sites(
        first,
        nodes,
        framework_names=contract["framework_runtime_functions"],
        wrappers=contract["call_wrappers"],
        macro_facts=macros,
        preprocessing_contexts=preprocessing,
    )
    assert classification.accounted == len(first)
    repeated_classification = classify_call_sites(
        repeated,
        nodes,
        framework_names=contract["framework_runtime_functions"],
        wrappers=contract["call_wrappers"],
        macro_facts=repeated_macros,
        preprocessing_contexts=repeated_preprocessing,
    )
    assert classification.ledger == repeated_classification.ledger
    resolution_rows = [
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in classification.ledger
    ]
    resolution_outcomes = Counter(row["outcome"] for row in classification.ledger)
    assert len(classification.edges) == 0
    assert classification.unresolved_preprocessing == len(first) == 12_113
    assert classification.dropped_stdlib == 0
    assert classification.dropped_framework == 0
    assert classification.dropped_macro == 0
    assert classification.preserved_inactive == 0
    assert classification.unresolved_presence == 0
    assert classification.unresolved_expression == 0
    assert classification.unresolved_owner == 0
    assert classification.unresolved_macro == 0
    assert classification.wrapper_unresolved == 0
    assert resolution_outcomes == Counter(unresolved=12_113)
    assert {
        row["reason"] for row in classification.ledger
    } == {"insufficient_preprocessing_build_context"}
    preprocessing_ids_by_source = {
        row["source_id"]: sorted(
            row["build"]["unresolvedEvidenceIds"]
            + row["trace"]["evidenceIds"]
        )
        for row in preprocessing
    }
    assert all(
        row["preprocessing_evidence_ids"]
            == preprocessing_ids_by_source[site["source_id"]]
        for row, site in zip(classification.ledger, first, strict=True)
    )

    sealed = parser_report["sealedDirectJudgmentPopulation"]
    sealed_calls = [
        selected for selected in sealed if "calleeRange" in selected["payload"]
    ]
    sealed_macros = [
        selected for selected in sealed if "macroKind" in selected["payload"]
    ]
    sealed_imports = [
        selected for selected in sealed if "entries" in selected["payload"]
    ]
    by_id = {site["call_id"]: site for site in first}
    macro_by_id = {fact["fact_id"]: fact for fact in macros}
    import_by_id = {ref["import_id"]: ref for ref in imports}
    node_by_id = {node.id: node for node in nodes}
    ledger_by_id = {row["call_id"]: row for row in classification.ledger}
    assert len(sealed_calls) == 526
    assert len(sealed_macros) == 34
    assert len(sealed_imports) == 450
    for selected in sealed_calls:
        site = by_id[selected["factId"]]
        assert site["range"] == selected["range"]
        assert site["callee_range"] == selected["payload"]["calleeRange"]
        assert site["callee_kind"] == selected["payload"]["calleeKind"]
        assert site["call_text"] == (selected["payload"]["terminalName"] or "")
        assert site["presence"] == selected["presence"]
        owner_node = node_by_id[site["caller_id"]]
        assert owner_node.start_line <= site["line"] <= owner_node.end_line
    for selected in sealed_macros:
        macro = macro_by_id[selected["factId"]]
        payload = selected["payload"]
        assert macro["range"] == selected["range"]
        assert macro["name_range"] == payload["nameRange"]
        assert macro["macro_kind"] == payload["macroKind"]
        assert macro["terminal_name"] == payload["terminalName"]
        assert macro["parameter_ranges"] == payload["parameterRanges"]
        assert macro["variadic"] == payload["variadic"]
        assert macro["replacement_range"] == payload["replacementRange"]
        assert macro["presence"] == selected["presence"]
    for selected in sealed_imports:
        ref = import_by_id[selected["factId"]]
        payload = selected["payload"]
        assert ref["range"] == selected["range"]
        assert ref["directive_kind"] == payload["directiveKind"]
        assert len(ref["entries"]) == len(payload["entries"])
        for entry_ordinal, (entry, sealed_entry) in enumerate(zip(
            ref["entries"], payload["entries"], strict=True,
        )):
            assert entry["entry_ordinal"] == entry_ordinal
            assert entry["import_kind"] == sealed_entry["importKind"]
            assert entry["target_kind"] == sealed_entry["targetKind"]
            assert entry["target_range"] == sealed_entry["targetRange"]
            assert entry["path_component_ranges"] == sealed_entry["pathComponentRanges"]
            assert entry["member_range"] == sealed_entry.get("memberRange")
            assert entry["alias_range"] == sealed_entry.get("aliasRange")
            assert entry["relative_level"] == sealed_entry["relativeLevel"]
            assert entry["wildcard"] == sealed_entry["wildcard"]
            assert entry["locality"] == sealed_entry["locality"]
        assert ref["presence"] == selected["presence"]

    # Fixed, source-range-sealed examples cover calls that the former raw-source
    # classifier would have treated as unrelated semantic categories.  Without
    # configured preprocessing, every one must stop at the earlier evidence gate.
    direct_expectations = {
        "framework_runtime": (
            "5975501d007c95d8e41c77757db52f240a83723c51d82b77cd9e88800d14f0ff",
            "apascrtu0010t02.c", "PFM_TRY", "active",
        ),
        "stdlib": (
            "c06c20c586026157f709dd4bb2c3c365372c5ebe914d5ce2c4a75fd799ce0e40",
            "apascrtu0010t02.c", "strncpy", "active",
        ),
        "function_macro": (
            "e3c796a28b0fe9ab80645b96736736a24511eac0d0e6d3999e0e8b39ab9c656d",
            "zapamcom10090.c", "PFM_DBG", "active",
        ),
        "inactive_source_branch": (
            "66465ddc80d18aa10e4b4bad1c82a42c69e375ae7fef8865e0e6055f7faf6140",
            "zapamcom10020.c", "strncpy", "inactive",
        ),
        "argument_wrapper": (
            "bfd07393bf60d77993986769a53ae928c9106d411f0b31ef718c6ab6a2149dd8",
            "zapamcom10110.c", "mpfm_dlcall", "inactive",
        ),
        "workspace_named_routine": (
            "d1be6aa1b5327e31ace0ceac874adafebbf8183c162ed673b1d6dad0166ae348",
            "zapamcom10200.c", "swgf_snprintf", "active",
        ),
    }
    sealed_call_ids = {selected["factId"] for selected in sealed_calls}
    sealed_preprocessing_judgment = []
    for category, (fact_id, source_id, call_text, presence_status) in (
        direct_expectations.items()
    ):
        assert fact_id in sealed_call_ids
        site = by_id[fact_id]
        assert (
            site["source_id"], site["call_text"], site["presence_status"],
        ) == (source_id, call_text, presence_status)
        resolution = ledger_by_id[fact_id]
        assert resolution["outcome"] == "unresolved"
        assert resolution["reason"] == "insufficient_preprocessing_build_context"
        assert resolution["preprocessing_evidence_ids"] \
            == preprocessing_ids_by_source[source_id]
        sealed_preprocessing_judgment.append({
            "category": category,
            "fact_id": fact_id,
            "source_id": source_id,
            "range": site["range"],
            "call_text": call_text,
            "raw_presence_status": presence_status,
            "semantic_outcome": resolution["outcome"],
            "semantic_reason": resolution["reason"],
            "preprocessing_evidence_ids": resolution[
                "preprocessing_evidence_ids"
            ],
        })

    graph_proof = {"status": "not_run"}
    if os.environ.get("ROBO_RUN_NEO4J_INTEGRATION") == "1":
        graph_proof = asyncio.run(_prove_actual_occurrence_graph_rollback(
            classification.ledger,
            import_ledger,
            [expectation[0] for expectation in direct_expectations.values()],
        ))

    call_completeness = [row for row in completeness if row["kind"] == "call"]
    completeness_status = Counter(row["status"] for row in call_completeness)
    assert sum(completeness_status.values()) == len(closure.artifacts)

    owner_rows = [
        "\0".join((
            site["call_id"], site["caller_id"], site["owner_status"],
            site["presence_status"], site["callee_kind"], site["call_text"],
        ))
        for site in first
    ]
    sealed_owner_judgment = []
    for selected in sealed_calls[:40]:
        site = by_id[selected["factId"]]
        owner_node = node_by_id[site["caller_id"]]
        sealed_owner_judgment.append({
            "fact_id": site["call_id"],
            "source_id": site["source_id"],
            "call_line": site["line"],
            "call_expression": selected["displayPreview"]["callExpression"],
            "owner_id": site["caller_id"],
            "owner_name": owner_node.name,
            "owner_signature": owner_node.signature,
            "owner_start_line": owner_node.start_line,
            "owner_end_line": owner_node.end_line,
        })
    report = {
        "contract_version": "1.1.0",
        "source_files": len(closure.artifacts),
        "code_nodes": len(nodes),
        "call_facts": len(first),
        "macro_facts": len(macros),
        "import_facts": len(imports),
        "unique_call_fact_ids": len(set(expected_ids)),
        "duplicate_call_fact_ids": len(expected_ids) - len(set(expected_ids)),
        "call_fact_id_ledger_sha256": _ledger_hash(expected_ids),
        "macro_fact_id_ledger_sha256": _ledger_hash(
            [fact["fact_id"] for fact in macros]
        ),
        "import_fact_id_ledger_sha256": _ledger_hash(import_ids),
        "owner_ledger_sha256": _ledger_hash(owner_rows),
        "owner_status": dict(sorted(owner.items())),
        "presence_status": dict(sorted(presence.items())),
        "callee_kind": dict(sorted(callee_kind.items())),
        "macro_kind": dict(sorted(macro_kind.items())),
        "macro_presence_status": dict(sorted(macro_presence.items())),
        "import_target_kind": dict(sorted(import_target_kind.items())),
        "import_kind": dict(sorted(import_kind.items())),
        "import_presence_status": dict(sorted(import_presence.items())),
        "import_evidence_status": dict(sorted(import_evidence_status.items())),
        "call_completeness": dict(sorted(completeness_status.items())),
        "configured_preprocessing": {
            "source_population": len(preprocessing),
            "status": dict(sorted(Counter(
                row["status"] for row in preprocessing
            ).items())),
            "trust": dict(sorted(Counter(
                row["trust"] for row in preprocessing
            ).items())),
            "evidence_id_population": len(preprocessing_ids),
            "unique_evidence_ids": len(set(preprocessing_ids)),
            "evidence_id_ledger_sha256": _ledger_hash(preprocessing_ids),
        },
        "call_resolution_accounting": {
            "input": len(first),
            "accounted": classification.accounted,
            "edges": len(classification.edges),
            "dropped_stdlib": classification.dropped_stdlib,
            "dropped_framework": classification.dropped_framework,
            "dropped_macro": classification.dropped_macro,
            "preserved_inactive": classification.preserved_inactive,
            "unresolved_presence": classification.unresolved_presence,
            "unresolved_expression": classification.unresolved_expression,
            "unresolved_owner": classification.unresolved_owner,
            "unresolved_preprocessing": classification.unresolved_preprocessing,
            "unresolved_macro": classification.unresolved_macro,
            "wrapper_unresolved": classification.wrapper_unresolved,
        },
        "call_resolution_outcomes": dict(sorted(resolution_outcomes.items())),
        "call_resolution_ledger_sha256": _ledger_hash(resolution_rows),
        "import_resolution_accounting": {
            "input_facts": len(imports),
            "input_entries": len(import_entries),
            "accounted": len(import_ledger),
            "resolved": import_outcomes["resolved"],
            "excluded": import_outcomes["excluded"],
            "unresolved": import_outcomes["unresolved"],
            "by_reason": dict(sorted(import_reasons.items())),
        },
        "import_resolution_ledger_sha256": summarize_import_resolution_ledger(
            import_ledger
        )["sha256"],
        "sealed_call_direct_judgment_matched": len(sealed_calls),
        "sealed_macro_direct_judgment_matched": len(sealed_macros),
        "sealed_import_direct_judgment_matched": len(sealed_imports),
        "sealed_preprocessing_direct_judgment_population": (
            sealed_preprocessing_judgment
        ),
        "neo4j_call_occurrence_rollback_proof": graph_proof,
        "sealed_owner_judgment_population": sealed_owner_judgment,
        "deterministic_replay": expected_ids == repeated_ids,
        "first_pass_seconds": round(elapsed, 6),
        "repeated_pass_seconds": round(repeated_elapsed, 6),
        "first_pass_peak_tracemalloc_bytes": peak,
        "repeated_pass_peak_tracemalloc_bytes": repeated_peak,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )
