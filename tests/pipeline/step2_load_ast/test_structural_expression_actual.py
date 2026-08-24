from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.steps.step2_load_ast.evidence_ir import validate_common_evidence


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sealed_skt_structural_expression_replay_is_exact_and_deterministic() -> None:
    source_value = os.getenv("C081_ACTUAL_SOURCE", "")
    artifact_value = os.getenv("C081_ACTUAL_ARTIFACT", "")
    source_hash = os.getenv("C081_ACTUAL_SOURCE_SHA256", "")
    artifact_hash = os.getenv("C081_ACTUAL_ARTIFACT_SHA256", "")
    if not all((source_value, artifact_value, source_hash, artifact_hash)):
        pytest.skip("run only for an explicitly sealed C-081 actual slice")

    source = Path(source_value)
    artifact = Path(artifact_value)
    assert _sha256(source) == source_hash
    assert _sha256(artifact) == artifact_hash
    source_root = source.parent

    raw = json.loads(artifact.read_text(encoding="utf-8"))
    evidence = validate_common_evidence(
        raw,
        source_bytes=source.read_bytes(),
        expected_source_id=source.name,
    )
    expected: list[tuple[str, str | None, str, int]] = []

    def visit(node: dict) -> None:
        label = str(node.get("type") or "")
        expression = str(node.get("expression") or "")
        target = str(node.get("target") or "")
        origin = str(node.get("statementOrigin") or "")
        line = int(node.get("startLine") or 0)
        if label in {"IF", "SWITCH", "LOOP"} and expression:
            expected.append(("condition", expression, label, line))
        elif label == "CASE" and expression:
            expected.append(("case_value", expression, label, line))
        elif label == "RETURN" and expression:
            expected.append(("return_value", expression, label, line))
        elif label == "ASSIGNMENT":
            if origin == "declaration_initializer":
                expected.append(("initializer_value", expression, label, line))
            elif origin in {"prefix_update", "postfix_update"}:
                expected.append(("update_expression", None, label, line))
            else:
                expected.extend((
                    ("assignment_target", target, label, line),
                    ("assignment_value", expression, label, line),
                ))
        for child in node.get("children") or ():
            visit(child)

    visit(raw)
    actual = [
        (
            fact.role,
            evidence.decoded_text[
                fact.expression_range.char_offset:fact.expression_range.end
            ],
        )
        for fact in evidence.structural_expression_facts
    ]
    mismatch = next(
        (
            (index, expected[index], actual[index])
            for index in range(min(len(expected), len(actual)))
            if expected[index][0] != actual[index][0]
            or (
                expected[index][1] is not None
                and expected[index][1] != actual[index][1]
            )
        ),
        None,
    )
    assert len(expected) == len(actual), (
        f"legacy={len(expected)} canonical={len(actual)} "
        f"firstMismatch={mismatch}"
    )

    first = read_antlr_json(artifact, source_root, source_language="c")
    second = read_antlr_json(artifact, source_root, source_language="c")

    structural_ids = [item["fact_id"] for item in first.structural_expression_facts]
    semantic_ids = [item["fact_id"] for item in first.semantic_expression_facts]
    resolved_ids = [item["fact_id"] for item in first.resolved_expression_facts]
    assert len(structural_ids) == 578
    assert len(structural_ids) == len(set(structural_ids))
    assert semantic_ids == structural_ids
    assert resolved_ids == structural_ids
    first_ledger = json.dumps(
        first.semantic_expression_facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    second_ledger = json.dumps(
        second.semantic_expression_facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert first_ledger == second_ledger
    first_resolved_ledger = json.dumps(
        first.resolved_expression_facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    second_resolved_ledger = json.dumps(
        second.resolved_expression_facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert first_resolved_ledger == second_resolved_ledger

    first_fact = first.structural_expression_facts[0]
    offset = first_fact["range"]["charOffset"]
    line = source.read_text(encoding="utf-8").count("\n", 0, offset) + 1
    print(
        "C081_ANALYZER "
        f"structural={len(structural_ids)} semantic={len(semantic_ids)} "
        f"resolved={len(resolved_ids)} "
        f"semanticLedgerSha256={hashlib.sha256(first_ledger).hexdigest()} "
        f"resolvedLedgerSha256={hashlib.sha256(first_resolved_ledger).hexdigest()} "
        f"first={source.name}:{line}:{first_fact['role']}"
    )
