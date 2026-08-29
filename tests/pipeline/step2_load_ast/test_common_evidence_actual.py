from __future__ import annotations

import hashlib
import json
import os
import time
import tracemalloc
from collections import Counter
from pathlib import Path

import pytest
from agents.skills.catalog import load_skill_contract
from pipeline.models.call_resolution import (
    summarize_call_resolution_ledger,
)
from pipeline.models.import_resolution import (
    build_import_resolution_ledger,
    normalize_import_resolution_ledger,
    summarize_import_resolution_ledger,
)
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
)
from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.evidence_ir import (
    SymbolEvidenceFact,
    validate_common_evidence,
)
from pipeline.steps.step2_load_ast.source_ast_closure import verify_source_ast_closure
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache


FIXTURE = os.getenv("ROBO_SPEC131_CALL_FIXTURE", "")
REPORT = os.getenv("ROBO_SPEC131_CALL_REPORT", "")
PARSER_REPORT = os.getenv("ROBO_SPEC131_PARSER_REPORT", "")


def _ledger_hash(rows: list[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _symbol_payload(fact: SymbolEvidenceFact) -> dict:
    if fact.role == "definition":
        assert fact.scope_range is not None
        return {
            "role": "definition",
            "symbolKind": fact.symbol_kind,
            "scopeKind": fact.scope_kind,
            "scopeRange": fact.scope_range.as_dict(),
            "visibilityStartOffset": fact.visibility_start_offset,
        }
    return {
        "role": "lookup",
        "lookupKind": "type_name",
        "parserDecision": fact.parser_decision,
        "resolutionStatus": fact.resolution_status,
        "provenance": fact.provenance,
        "definitionFactId": fact.definition_fact_id,
        "configuredEvidenceId": fact.configured_evidence_id,
        "predicateContexts": list(fact.predicate_contexts),
    }


def _allowed_report(path: Path) -> bool:
    normalized = path.resolve().as_posix()
    return "/_runs/framework/" in normalized


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


def _prove_actual_occurrence_run_artifact(
    ledger: list[dict], import_ledger: list[dict], sealed_ids: list[str],
) -> dict:
    """Prove complete ledgers stay in the run artifact and never become product nodes."""
    call_summary = summarize_call_resolution_ledger(ledger)
    import_summary = summarize_import_resolution_ledger(import_ledger)
    sealed_by_id = {
        row["call_id"]: row for row in ledger if row["call_id"] in sealed_ids
    }
    sealed_rows = [
        sealed_by_id[call_id] for call_id in sorted(sealed_by_id)
    ]
    assert call_summary["total"] == len(ledger)
    assert import_summary["total"] == len(import_ledger)
    return {
        "storage": "content_addressed_run_artifact",
        "occurrence_nodes": 0,
        "occurrence_relationships": 0,
        "call_occurrences": call_summary["total"],
        "call_ledger_sha256": call_summary["sha256"],
        "import_occurrences": import_summary["total"],
        "import_ledger_sha256": import_summary["sha256"],
        "sealed_direct_judgment_matched": len(sealed_rows),
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
        list[tuple[str, SymbolEvidenceFact]], dict[str, str], float, int,
    ]:
        sites: list[dict] = []
        macros: list[dict] = []
        imports: list[dict] = []
        completeness: list[dict] = []
        preprocessing: list[dict] = []
        symbols: list[tuple[str, SymbolEvidenceFact]] = []
        symbol_sources: dict[str, str] = {}
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
            raw = json.loads(artifact.ast_path.read_text(encoding="utf-8"))
            validated = validate_common_evidence(
                raw,
                source_bytes=cache.read_bytes(artifact.source_path),
                expected_source_id=artifact.source_id,
            )
            assert validated.syntax_ambiguities == ()
            symbols.extend(
                (validated.source_id, fact)
                for fact in validated.symbol_facts
            )
            symbol_sources[validated.source_id] = validated.decoded_text
            assert not hasattr(result, "symbol_facts")
            assert all(
                not {
                    "definitionFactId", "configuredEvidenceId",
                    "predicateContexts", "parserDecision",
                    "resolutionStatus", "visibilityStartOffset", "scopeKind",
                }.intersection(node.extra)
                for node in result.code_nodes
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
            symbols, symbol_sources, elapsed, peak,
        )

    (
        first, macros, imports, completeness, preprocessing, nodes,
        symbols, symbol_sources, elapsed, peak,
    ) = consume()
    (
        repeated, repeated_macros, repeated_imports, _, repeated_preprocessing, _,
        repeated_symbols, repeated_symbol_sources,
        repeated_elapsed, repeated_peak,
    ) = consume()
    parser_report = json.loads(parser_report_path.read_text(encoding="utf-8"))
    expected_ids = [site["call_id"] for site in first]
    repeated_ids = [site["call_id"] for site in repeated]
    assert len(closure.artifacts) == parser_report["sourceFiles"] == 23
    assert len(first) == parser_report["callFacts"] == 12_113
    assert len(macros) == parser_report["macroFacts"] == 183
    assert len(imports) == parser_report["importFacts"] == 450
    symbol_ids = [fact.fact_id for _, fact in symbols]
    repeated_symbol_ids = [fact.fact_id for _, fact in repeated_symbols]
    assert len(symbols) == parser_report["symbolFacts"] == 4_659
    assert len(symbol_ids) == len(set(symbol_ids))
    assert symbol_ids == repeated_symbol_ids
    assert symbol_sources == repeated_symbol_sources
    assert _ledger_hash(symbol_ids) == parser_report["symbolFactIdLedgerSha256"]
    assert Counter(fact.role for _, fact in symbols) == Counter(
        definition=2_624, lookup=2_035,
    )
    assert Counter(
        fact.resolution_status for _, fact in symbols if fact.role == "lookup"
    ) == Counter(resolved=755, unresolved=1_280)
    unresolved_symbols = [
        fact for _, fact in symbols if fact.resolution_status == "unresolved"
    ]
    assert Counter(
        fact.predicate_contexts for fact in unresolved_symbols
    ) == Counter({
        ("declaration_only_context",): 694,
        ("declaration_only_parse",): 584,
        ("cast_only_parse",): 1,
        ("external_declaration_only_parse",): 1,
    })
    assert {fact.parser_decision for fact in unresolved_symbols} == {"type_name"}
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

    symbol_completeness = [
        row for row in completeness if row["kind"] == "symbol"
    ]
    assert len(symbol_completeness) == len(closure.artifacts) == 23
    assert sum(row["population"] for row in symbol_completeness) == len(symbols)
    completeness_unresolved_ids = [
        fact_id
        for row in symbol_completeness
        for fact_id in row.get("unresolvedFactIds", [])
    ]
    assert completeness_unresolved_ids == [
        fact.fact_id for fact in unresolved_symbols
    ]

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
        "213be4df83a31036ede95626a803da153adbeddc6f46a5cea2ca05becd65e852",
        "source/zapamcom10110.c",
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
    assert classification.missing_routine_owner_count == 0
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
    sealed_symbols = parser_report["sealedSymbolDirectJudgmentPopulation"]
    by_id = {site["call_id"]: site for site in first}
    macro_by_id = {fact["fact_id"]: fact for fact in macros}
    import_by_id = {ref["import_id"]: ref for ref in imports}
    symbol_by_id = {
        fact.fact_id: (source_id, fact) for source_id, fact in symbols
    }
    node_by_id = {node.id: node for node in nodes}
    ledger_by_id = {row["call_id"]: row for row in classification.ledger}
    assert len(sealed_calls) == 526
    assert len(sealed_macros) == 34
    assert len(sealed_imports) == 450
    assert len(sealed_symbols) == 40
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
    for selected in sealed_symbols:
        source_id, fact = symbol_by_id[selected["factId"]]
        assert source_id == selected["sourceId"]
        assert fact.symbol_range.as_dict() == selected["range"]
        assert fact.grammar_rule == selected["grammarRule"]
        assert _symbol_payload(fact) == selected["payload"]
        start = fact.symbol_range.char_offset
        assert symbol_sources[source_id][start:fact.symbol_range.end] \
            == selected["sourceSlice"]

    # Fixed, source-range-sealed examples cover calls that the former raw-source
    # classifier would have treated as unrelated semantic categories.  Without
    # configured preprocessing, every one must stop at the earlier evidence gate.
    direct_expectations = {
        "framework_runtime": (
            "0d268f10c575f808cd15e9fbe8c89b8e5cf8a2c69fba80ec4534b118391e7923",
            "source/apascrtu0010t02.c", "PFM_TRY", "active",
        ),
        "stdlib": (
            "66552cea60760867e5b419d5f6ffb966e73fd0875cb6b1e65f5ec71936cdb03e",
            "source/apascrtu0010t02.c", "strncpy", "active",
        ),
        "function_macro": (
            "f2a48f3034c7ee18ea2af8d8f0e1b5b2f0c777d28ffcb016a21a873b1c47b1f5",
            "source/zapamcom10090.c", "PFM_DBG", "active",
        ),
        "inactive_source_branch": (
            "d34161e98059b2dd2232b9658cc603756ef00f95fe74c9a92cf24a013731bffa",
            "source/zapamcom10020.c", "strncpy", "inactive",
        ),
        "argument_wrapper": (
            "b887ac8dea6d83fdff40a823bcbc18f80a7dd81a22549d0198225b160476cf55",
            "source/zapamcom10110.c", "mpfm_dlcall", "inactive",
        ),
        "workspace_named_routine": (
            "14f5ec567211e3720d49f1f456188499c799f50d66b1e1d765e77a601468613f",
            "source/zapamcom10200.c", "swgf_snprintf", "active",
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

    ledger_proof = _prove_actual_occurrence_run_artifact(
        classification.ledger,
        import_ledger,
        [expectation[0] for expectation in direct_expectations.values()],
    )

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
        "contract_version": parser_report["contractVersion"],
        "source_files": len(closure.artifacts),
        "code_nodes": len(nodes),
        "call_facts": len(first),
        "macro_facts": len(macros),
        "import_facts": len(imports),
        "symbol_facts": len(symbols),
        "symbol_definitions": sum(
            fact.role == "definition" for _, fact in symbols
        ),
        "symbol_lookups": sum(fact.role == "lookup" for _, fact in symbols),
        "symbol_resolved_lookups": sum(
            fact.resolution_status == "resolved" for _, fact in symbols
        ),
        "symbol_unresolved_lookups": len(unresolved_symbols),
        "symbol_fact_id_ledger_sha256": _ledger_hash(symbol_ids),
        "symbol_syntax_ambiguities": 0,
        "symbol_graph_domain_projection": "none",
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
            "missing_routine_owner_count": classification.missing_routine_owner_count,
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
        "sealed_symbol_direct_judgment_matched": len(sealed_symbols),
        "sealed_preprocessing_direct_judgment_population": (
            sealed_preprocessing_judgment
        ),
        "occurrence_run_artifact_proof": ledger_proof,
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
