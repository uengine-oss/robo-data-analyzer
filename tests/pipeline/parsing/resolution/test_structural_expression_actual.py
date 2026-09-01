from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pipeline.source_languages import ROUTINE_LABELS
from pipeline.models.analysis_context import AnalysisContext
from pipeline.stages.ast.ast_dispatch import read_antlr_json
from pipeline.parsing.facts.evidence_ir import validate_common_evidence
from pipeline.parsing.facts.expression_ownership import routine_expression_ledger
from pipeline.parsing.resolution.rule_condition_projection import (
    build_routine_rule_condition_bundles,
)
from pipeline.models.rule_condition_ir import attach_rule_condition_projection
from pipeline.models.rule_materialization import materialize_rule_items
from pipeline.checks.rule_obligations import required_rule_rows
from pipeline.stages.context.control_subtree import (
    build_control_subtrees,
    build_statement_facts,
)
from pipeline.meaning.rule.control_inventory import build_control_inventory
from pipeline.meaning.rule.row_batching import (
    materialize_and_validate_rules,
)
from shared.files.source_cache import SourceFileCache


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
    routine_ledger = routine_expression_ledger(
        first.code_nodes, first.resolved_expression_facts,
    )
    populated_routines = {
        routine_id: facts for routine_id, facts in routine_ledger.items() if facts
    }
    assert len(populated_routines) == 46
    assert sum(map(len, populated_routines.values())) == 578
    condition_bundles = build_routine_rule_condition_bundles(
        first.code_nodes,
        first.resolved_expression_facts,
        first.structural_expression_facts,
    )
    relevant_expression_count = sum(
        fact["role"] in {"condition", "case_value"}
        for fact in first.resolved_expression_facts
    )
    assert sum(
        len(bundle["conditions"]) for bundle in condition_bundles.values()
    ) == relevant_expression_count
    case_bundle = next(
        bundle for bundle in condition_bundles.values()
        if "C546" in bundle["conditions"]
    )
    projected_case = attach_rule_condition_projection({
        "conditions": {"C546": "RC_NFD"},
        "effects": {},
        "rows": {"R546": {"role": "decision", "when": ["C546"]}},
    }, case_bundle)
    assert materialize_rule_items(projected_case, (546,))[0].structural_condition == (
        'F("rc") == F("RC_NFD")'
    )

    control_subtrees = build_control_subtrees(first.code_nodes)
    statement_facts = build_statement_facts(
        first.code_nodes, first.variables, first.constants,
    )
    source_cache = SourceFileCache()
    projected_rules = []
    changed_conditions = []
    for routine in first.code_nodes:
        if routine.label not in ROUTINE_LABELS:
            continue
        context = AnalysisContext(
            id=routine.id,
            signature=routine.signature,
            file_path=routine.file_path,
            start_line=routine.start_line,
            end_line=routine.end_line,
            extra={
                "control_subtree": control_subtrees.get(routine.id, []),
                "ast_statements": tuple(statement_facts.get(routine.id, ())),
            },
        )
        inventory = build_control_inventory(routine, context, source_cache)
        rows = tuple(required_rule_rows(inventory))
        collection = materialize_and_validate_rules(
            inventory=inventory,
            target_scope=routine.id,
            source=source_cache.slice(
                routine.file_path, routine.start_line, routine.end_line,
            ),
            rows=rows,
            condition_bundle=condition_bundles.get(routine.id),
        )
        baseline = materialize_and_validate_rules(
            inventory=inventory,
            target_scope=routine.id,
            source=source_cache.slice(
                routine.file_path, routine.start_line, routine.end_line,
            ),
            rows=rows,
        )
        assert [rule.anchor_line for rule in collection.rules] == list(rows)
        assert [rule.anchor_line for rule in baseline.rules] == list(rows)
        changed_conditions.extend(
            (
                routine.id,
                current.anchor_line,
                previous.structural_condition,
                current.structural_condition,
            )
            for previous, current in zip(
                baseline.rules, collection.rules, strict=True,
            )
            if previous.structural_condition != current.structural_condition
        )
        projected_rules.extend(collection.rules)
    assert len({rule.anchor_line for rule in projected_rules}) == len(projected_rules)
    actual_case = next(
        rule for rule in projected_rules
        if rule.anchor_line == 546
        and rule.structural_condition == 'F("rc") == F("RC_NFD")'
    )
    assert actual_case.anchor_line == 546
    first_case_change = next(
        item for item in changed_conditions if item[1] == 546
    )
    assert first_case_change[2:] == (
        'F("RC_NFD")', 'F("rc") == F("RC_NFD")',
    )
    effect_case = next(
        rule for rule in projected_rules
        if rule.anchor_line == 549
    )
    assert effect_case.structural_condition == (
        'not (F("rc") == F("RC_NRM")) and F("rc") == F("RC_ERR")'
    )
    first_fact = first.structural_expression_facts[0]
    offset = first_fact["range"]["charOffset"]
    line = source.read_text(encoding="utf-8").count("\n", 0, offset) + 1
    print(
        "C081_ANALYZER "
        f"structural={len(structural_ids)} semantic={len(semantic_ids)} "
        f"resolved={len(resolved_ids)} "
        f"conditionRelations={relevant_expression_count} "
        f"rules={len(projected_rules)} "
        f"changedConditions={len(changed_conditions)} "
        f"firstChanged={changed_conditions[0][1]} "
        f"semanticLedgerSha256={hashlib.sha256(first_ledger).hexdigest()} "
        f"resolvedLedgerSha256={hashlib.sha256(first_resolved_ledger).hexdigest()} "
        f"first={source.name}:{line}:{first_fact['role']}"
    )
