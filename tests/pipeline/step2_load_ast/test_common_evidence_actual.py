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
from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.source_ast_closure import verify_source_ast_closure
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
from shared.files.cache import SourceFileCache


FIXTURE = os.getenv("ROBO_SPEC131_CALL_FIXTURE", "")
REPORT = os.getenv("ROBO_SPEC131_CALL_REPORT", "")
PARSER_REPORT = os.getenv("ROBO_SPEC131_PARSER_REPORT", "")


def _ledger_hash(rows: list[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _allowed_report(path: Path) -> bool:
    normalized = path.resolve().as_posix()
    return "/specs/131-cross-node-semantic-grounding/_runs/framework/" in normalized


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

    def consume() -> tuple[list[dict], list[dict], list[dict], list, float, int]:
        sites: list[dict] = []
        macros: list[dict] = []
        completeness: list[dict] = []
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
            completeness.extend(result.evidence_completeness)
            nodes.extend(result.code_nodes)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return sites, macros, completeness, nodes, elapsed, peak

    first, macros, completeness, nodes, elapsed, peak = consume()
    repeated, repeated_macros, _, _, repeated_elapsed, repeated_peak = consume()
    parser_report = json.loads(parser_report_path.read_text(encoding="utf-8"))
    expected_ids = [site["call_id"] for site in first]
    repeated_ids = [site["call_id"] for site in repeated]
    assert len(closure.artifacts) == parser_report["sourceFiles"] == 23
    assert len(first) == parser_report["callFacts"] == 12_113
    assert len(macros) == parser_report["macroFacts"] == 183
    assert len(expected_ids) == len(set(expected_ids))
    assert expected_ids == repeated_ids
    assert [fact["fact_id"] for fact in macros] == [
        fact["fact_id"] for fact in repeated_macros
    ]
    assert _ledger_hash(expected_ids) == parser_report["callFactIdLedgerSha256"]
    assert _ledger_hash(
        [fact["fact_id"] for fact in macros]
    ) == parser_report["macroFactIdLedgerSha256"]

    presence = Counter(site["presence_status"] for site in first)
    callee_kind = Counter(site["callee_kind"] for site in first)
    owner = Counter(site["owner_status"] for site in first)
    macro_presence = Counter(fact["presence_status"] for fact in macros)
    macro_kind = Counter(fact["macro_kind"] for fact in macros)
    assert presence == Counter(active=12_080, inactive=33)
    assert callee_kind == Counter(named=12_113)
    assert macro_presence == Counter(active=182, conditional=1)
    assert macro_kind == Counter(object=182, function=1)
    assert sum(owner.values()) == len(first)
    assert all(site["call_id"] and site["source_id"] for site in first)

    contract = load_skill_contract("proframe-c-pfm-dbio")
    classification = classify_call_sites(
        first,
        nodes,
        framework_names=contract["framework_runtime_functions"],
        wrappers=contract["call_wrappers"],
        macro_facts=macros,
    )
    assert classification.accounted == len(first)
    repeated_classification = classify_call_sites(
        repeated,
        nodes,
        framework_names=contract["framework_runtime_functions"],
        wrappers=contract["call_wrappers"],
        macro_facts=repeated_macros,
    )
    assert classification.ledger == repeated_classification.ledger
    resolution_rows = [
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in classification.ledger
    ]
    resolution_outcomes = Counter(row["outcome"] for row in classification.ledger)
    macro_unresolved = [
        row for row in classification.ledger
        if row.get("reason") == "insufficient_conditional_macro_definition"
    ]
    assert len(macro_unresolved) == classification.unresolved_macro == 76
    assert {
        tuple(row["macro_fact_ids"]) for row in macro_unresolved
    } == {("bc5446c0cb047dee5a8f7ed1ef83f6a981e767660ef6ed7eb4914b8d3d803a48",)}
    assert all(
        by_site["source_id"] == "source/zordmfax00001.c"
        and by_site["call_text"] == "PFM_DBG"
        for row in macro_unresolved
        for by_site in (next(site for site in first if site["call_id"] == row["call_id"]),)
    )

    sealed = parser_report["sealedDirectJudgmentPopulation"]
    sealed_calls = [
        selected for selected in sealed if "calleeRange" in selected["payload"]
    ]
    sealed_macros = [
        selected for selected in sealed if "macroKind" in selected["payload"]
    ]
    by_id = {site["call_id"]: site for site in first}
    macro_by_id = {fact["fact_id"]: fact for fact in macros}
    node_by_id = {node.id: node for node in nodes}
    assert len(sealed_calls) == 526
    assert len(sealed_macros) == 34
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
        "contract_version": "1.0.0",
        "source_files": len(closure.artifacts),
        "code_nodes": len(nodes),
        "call_facts": len(first),
        "macro_facts": len(macros),
        "unique_call_fact_ids": len(set(expected_ids)),
        "duplicate_call_fact_ids": len(expected_ids) - len(set(expected_ids)),
        "call_fact_id_ledger_sha256": _ledger_hash(expected_ids),
        "macro_fact_id_ledger_sha256": _ledger_hash(
            [fact["fact_id"] for fact in macros]
        ),
        "owner_ledger_sha256": _ledger_hash(owner_rows),
        "owner_status": dict(sorted(owner.items())),
        "presence_status": dict(sorted(presence.items())),
        "callee_kind": dict(sorted(callee_kind.items())),
        "macro_kind": dict(sorted(macro_kind.items())),
        "macro_presence_status": dict(sorted(macro_presence.items())),
        "call_completeness": dict(sorted(completeness_status.items())),
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
            "unresolved_macro": classification.unresolved_macro,
            "wrapper_unresolved": classification.wrapper_unresolved,
        },
        "call_resolution_outcomes": dict(sorted(resolution_outcomes.items())),
        "call_resolution_ledger_sha256": _ledger_hash(resolution_rows),
        "sealed_call_direct_judgment_matched": len(sealed_calls),
        "sealed_macro_direct_judgment_matched": len(sealed_macros),
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
