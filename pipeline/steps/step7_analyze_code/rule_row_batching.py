"""규칙 의무 행 배칭과 단일 시도 검증 (spec 116).

입력(SOURCE+분기표)은 유지하고 출력만 서로 겹치지 않는 행 묶음으로 나눈다. 각 배치는
한 번만 호출한다. transport 재시도는 ``yaml_llm_runner`` 하나가 소유한다. 모델의 schema·
내용·누락·좌표 오류는 재요청하지 않고 좌표 있는 품질 finding으로 격리한다. 검증된 sibling
행은 보존하며 운영/결정론 불변식 실패만 예외로 전파한다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Sequence

from pipeline.models.code_analysis_output import RulesExamplesOutput, RuleItem
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_grading import grade_rules, partition_rule_candidates
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from llm.runners.model_output_error import ModelOutputError

# Product evidence (2026-08-04, response cache off): response tokens and latency had
# correlation 0.96. A blanket five-row bound produced 988 attempts, repeated fixed
# output overhead, and a 22-minute wall. Keep the compact eight-row default that reached
# 15m14s, but split only batches whose actual evidence exposes more than 48 cond/then
# references. This is a source-language/name/value-independent response-shape bound.
DEFAULT_ROWS_PER_CALL = 8
MAX_RULE_OUTPUT_REFERENCES = 48
SUMMARY_WAKE_PRIORITY = -10_000_000
RowInvoke = Callable[[Sequence[int]], Awaitable[RulesExamplesOutput]]


def rule_queue_priority(payload_chars: int) -> int:
    """Schedule larger finite Rule jobs first while summaries always stay first.

    Longest-processing-time-first prevents one large request from becoming the final
    180-second tail.  The priority only orders waiters; it never changes concurrency.
    """
    if payload_chars < 0:
        raise ValueError("payload_chars must be nonnegative")
    return -min(payload_chars, abs(SUMMARY_WAKE_PRIORITY) - 1)


@dataclass
class RowCollection:
    """함수 하나의 trusted 규칙과 격리된 모델 품질 findings."""

    rules: list[RuleItem]
    findings: list[AnalysisFinding]
    calls: int = 0


def plan_batches(
    inventory: BranchInventory,
    rows_per_call: int = DEFAULT_ROWS_PER_CALL,
    *,
    max_output_references: int = MAX_RULE_OUTPUT_REFERENCES,
) -> list[list[int]]:
    """Split required rows by measured response-shape bounds.

    ``when`` and ``direct`` are deterministic evidence references that may be copied
    into the forced ``cond``/``then`` arrays.  They predict response work without
    interpreting source language, names, or business values.
    """
    if rows_per_call < 1:
        raise ValueError("rows_per_call must be positive")
    if max_output_references < 1:
        raise ValueError("max_output_references must be positive")
    def output_references(lines: Sequence[int]) -> int:
        evidence = rule_evidence(inventory, lines)
        return sum(
            len(row.get("when") or ()) + len(row.get("direct") or ())
            for row in evidence["rows"].values()
        )

    obligation = required_rule_rows(inventory)
    batches: list[list[int]] = []
    start = 0
    while start < len(obligation):
        batch = obligation[start:start + rows_per_call]
        while (
            len(batch) > 1
            and output_references(batch) > max_output_references
        ):
            # Keep the largest safe prefix.  Dropping one row at a time minimizes
            # duplicated fixed context while still enforcing the response bound.
            batch = batch[:-1]
        batches.append(batch)
        start += len(batch)
    return batches


def required_rule_rows(
    inventory: BranchInventory, *, gate_only: bool = False,
) -> list[int]:
    """Return the deterministic row coordinates a rules schema must require."""
    if gate_only:
        return [
            row["line"] for row in inventory.table_rows()
            if row["role"] in ("decision", "complement")
        ]
    return [
        row["line"]
        for row in inventory.table_rows()
        if row["role"] in ("decision", "complement", "entry")
        or (row["role"] in ("exit", "handler") and not row.get("in_branch"))
    ]


async def collect_rules_by_rows(
    *,
    invoke: RowInvoke,
    inventory: BranchInventory,
    target_scope: str,
    source: str,
    rows_per_call: int = DEFAULT_ROWS_PER_CALL,
    extra_values: Sequence[str] = (),
    allowed_tables: Sequence[str] = (),
    read_tables: Sequence[str] | None = None,
    write_tables: Sequence[str] | None = None,
    table_ops: dict[str, str] | None = None,
) -> RowCollection:
    """모든 배치를 병렬로 한 번 호출하고 검증된 행과 findings를 분리한다."""
    if not target_scope.strip():
        raise ValueError("target_scope must be non-empty")

    def scoped(finding: AnalysisFinding) -> AnalysisFinding:
        target_id = target_scope
        if len(finding.coordinates) == 1:
            target_id = f"{target_scope}::R-{finding.coordinates[0]}"
        return replace(finding, target_id=target_id)

    async def run_batch(batch: Sequence[int]):
        try:
            output = await invoke(batch)
        except ModelOutputError as error:
            findings = [
                AnalysisFinding(
                    concern="rules",
                    status="unavailable",
                    target_id=f"{target_scope}::R-{row}",
                    coordinates=(row,),
                    reasons=(str(error),),
                    evidence_rule=error.rule_name,
                )
                for row in batch
            ]
            return [], findings
        report = grade_rules(
            output,
            inventory,
            source,
            assigned=set(batch),
            extra_values=extra_values,
            allowed_tables=allowed_tables,
            read_tables=read_tables,
            write_tables=write_tables,
            table_ops=table_ops,
            require_exact_conditions=True,
        )
        accepted, findings = partition_rule_candidates(
            output,
            report,
            assigned=set(batch),
            evidence_rule="code_rules_examples",
        )
        return accepted, [scoped(finding) for finding in findings]

    batches = plan_batches(
        inventory,
        rows_per_call,
    )
    if not batches:
        return RowCollection(rules=[], findings=[], calls=0)
    results = await asyncio.gather(*(run_batch(batch) for batch in batches))
    rules = [rule for batch_rules, _ in results for rule in batch_rules]
    findings = [finding for _, batch_findings in results for finding in batch_findings]
    rules.sort(key=lambda rule: rule.anchor_line)
    findings.sort(key=lambda finding: (finding.coordinates, finding.status, finding.target_id))
    return RowCollection(
        rules=rules, findings=findings, calls=len(batches)
    )
