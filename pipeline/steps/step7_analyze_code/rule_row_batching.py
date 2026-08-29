"""Deterministic Rule obligation accounting and materialization."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from typing import Sequence

from pipeline.models.code_analysis_output import RuleItem, RulesExamplesOutput
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.rule_condition_ir import attach_rule_condition_projection
from pipeline.models.rule_materialization import materialize_rule_items
from pipeline.models.rule_meaning_frame import RuleMeaningProjection
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_obligations import required_rule_rows
from pipeline.quality_gates.rule_grading import grade_rules, partition_rule_candidates
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.quality_gates.rule_condition_rendering import (
    render_projected_row_condition,
)


@dataclass
class RowCollection:
    """함수 하나의 trusted 규칙과 격리된 모델 품질 findings."""

    rules: list[RuleItem]
    findings: list[AnalysisFinding]
    calls: int = 0
    evidence: dict[str, object] = field(default_factory=dict)


def materialize_and_validate_rules(
    *,
    inventory: BranchInventory,
    target_scope: str,
    source: str,
    rows: Sequence[int] | None = None,
    extra_values: Sequence[str] = (),
    allowed_tables: Sequence[str] = (),
    read_tables: Sequence[str] | None = None,
    write_tables: Sequence[str] | None = None,
    table_ops: dict[str, str] | None = None,
    table_aliases: dict[str, str] | None = None,
    condition_bundle: dict[str, object] | None = None,
) -> RowCollection:
    """Materialize and self-check every assigned Rule without an LLM call."""
    if not target_scope.strip():
        raise ValueError("target_scope must be non-empty")
    assigned = tuple(
        int(row) for row in (
            required_rule_rows(inventory) if rows is None else rows
        )
    )
    evidence = attach_rule_condition_projection(
        rule_evidence(inventory, assigned), condition_bundle,
    )
    output = RulesExamplesOutput(
        rules=materialize_rule_items(evidence, assigned)
    )
    evidence_rows = evidence["rows"]
    expected_conditions = {
        row: render_projected_row_condition(evidence_rows[f"R{row}"], evidence)
        for row in assigned
    }
    report = grade_rules(
        output,
        inventory,
        source,
        assigned=set(assigned),
        extra_values=extra_values,
        allowed_tables=allowed_tables,
        read_tables=read_tables,
        write_tables=write_tables,
        table_ops=table_ops,
        table_aliases=table_aliases,
        require_exact_conditions=True,
        expected_conditions=expected_conditions,
    )
    accepted, findings = partition_rule_candidates(
        output,
        report,
        assigned=set(assigned),
        evidence_rule="deterministic_rule_structure",
        target_scope=target_scope,
    )
    fatal = [finding for finding in findings if finding.status != "unverified"]
    accepted_rows = {rule.anchor_line for rule in accepted}
    finding_counts = Counter(
        row for finding in findings for row in finding.coordinates
    )
    finding_rows = set(finding_counts)
    accounting_invalid = (
        accepted_rows & finding_rows
        or accepted_rows | finding_rows != set(assigned)
        or any(count != 1 for count in finding_counts.values())
    )
    if fatal or accounting_invalid:
        reasons = [
            f"R{row}: {reason}"
            for finding in fatal
            for row in finding.coordinates
            for reason in finding.reasons
        ]
        raise ValueError(
            f"{target_scope}: deterministic Rule invariant failed: "
            + "; ".join(reasons or ("assigned-row coverage mismatch",))
        )
    accepted.sort(key=lambda rule: rule.anchor_line)
    return RowCollection(
        rules=accepted,
        findings=findings,
        calls=0,
        evidence=dict(evidence),
    )


def apply_rule_meaning_projections(
    collection: RowCollection,
    projections: Sequence[RuleMeaningProjection],
    *,
    target_scope: str,
) -> RowCollection:
    """Publish only sufficiently grounded business rows; account for every other row."""
    by_line = {projection.anchor_line: projection for projection in projections}
    expected = {rule.anchor_line for rule in collection.rules}
    if len(by_line) != len(projections) or set(by_line) != expected:
        raise ValueError(
            f"{target_scope}: Rule meaning row coverage mismatch: "
            f"expected={sorted(expected)}, actual={sorted(by_line)}"
        )
    rules: list[RuleItem] = []
    findings = list(collection.findings)
    for rule in collection.rules:
        projection = by_line[rule.anchor_line]
        if (
            projection.disposition == "business_rule"
            and projection.status == "sufficient"
        ):
            rules.append(rule.model_copy(update={
                "business_condition": projection.condition,
                "business_effects": list(projection.effects),
            }))
            continue
        excluded = projection.disposition == "implementation_detail"
        findings.append(AnalysisFinding(
            concern="rules",
            status="excluded" if excluded else "unverified",
            target_id=f"{target_scope}::R-{rule.anchor_line}",
            coordinates=(rule.anchor_line,),
            reasons=((
                "Structural row is implementation detail, not a product business Rule"
                if excluded else
                "Business Rule meaning is not sufficiently grounded for publication: "
                f"{projection.status}"
            ),),
            candidate={
                "row_disposition": projection.disposition,
                "business_condition": projection.condition,
                "business_effects": list(projection.effects),
                "atom_coverage_status": projection.status,
            },
            evidence_rule="code_rule_meanings",
        ))
    published_rows = {rule.anchor_line for rule in rules}
    accounted_rows = Counter(
        row
        for finding in findings[len(collection.findings):]
        for row in finding.coordinates
    )
    if (
        published_rows & set(accounted_rows)
        or published_rows | set(accounted_rows) != expected
        or any(count != 1 for count in accounted_rows.values())
    ):
        raise ValueError(f"{target_scope}: Rule meaning exact-once accounting failed")
    return RowCollection(
        rules=rules,
        findings=findings,
        calls=collection.calls + 1,
        evidence=collection.evidence,
    )


def mark_rule_meanings_unavailable(
    collection: RowCollection,
    *,
    target_scope: str,
    reason: str,
    count_call: bool = True,
) -> RowCollection:
    """Keep failed meaning rows out of product Rules and account for each once."""
    findings = [
        *collection.findings,
        *(
            AnalysisFinding(
                concern="rules",
                status="unavailable",
                target_id=f"{target_scope}::R-{rule.anchor_line}",
                coordinates=(rule.anchor_line,),
                reasons=(reason,),
                evidence_rule="code_rule_meanings",
            )
            for rule in collection.rules
        ),
    ]
    return RowCollection(
        rules=[],
        findings=findings,
        calls=collection.calls + int(count_call),
        evidence=collection.evidence,
    )
