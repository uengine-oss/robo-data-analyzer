"""규칙 의무 행 배칭과 단일 시도 검증 (spec 116).

입력(SOURCE+분기표)은 유지하고 출력만 서로 겹치지 않는 행 묶음으로 나눈다. 각 배치는
한 번만 호출한다. transport 재시도는 ``yaml_llm_runner`` 하나가 소유한다. 모델의 schema·
내용·누락·좌표 오류는 재요청하지 않고 좌표 있는 품질 finding으로 격리한다. 검증된 sibling
행은 보존하며 운영/결정론 불변식 실패만 예외로 전파한다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import json
from typing import Awaitable, Callable, Sequence

from pipeline.models.code_analysis_output import RulesExamplesOutput, RuleItem
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.semantic_frame import SemanticFrame
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_grading import grade_rules, partition_rule_candidates
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from llm.runners.model_output_error import ModelOutputError

# Product evidence (2026-08-04, response cache off): response tokens and latency had
# correlation 0.96. A blanket five-row bound produced 988 attempts, repeated fixed
# output overhead, and a 22-minute wall.  The Rule packet was subsequently compacted
# by 77.3% in measured input tokens, so the old fixed eight-row target no longer
# describes request cost.  Pack by the exact compact evidence and forced schema size;
# this row limit is only a last-resort guard against an unmodelled cost axis.
DEFAULT_ROWS_PER_CALL = 24
MAX_RULE_SEMANTIC_SLOTS = 14
MAX_RULE_EVIDENCE_CHARS = 16_000
MAX_RULE_EVIDENCE_REFERENCES = 64
SUMMARY_WAKE_PRIORITY = -10_000_000
RowInvoke = Callable[[Sequence[int]], Awaitable[RulesExamplesOutput]]
SemanticSlotCount = Callable[[Sequence[int]], int]


def _semantic_rule_findings(
    frame: SemanticFrame | None,
    *,
    target_scope: str,
) -> tuple[list[AnalysisFinding], set[int]]:
    """Surface every non-sufficient rule slot and quarantine its legacy Rule row."""
    if frame is None:
        return [], set()
    by_line: dict[int, list[object]] = {}
    for slot in frame.slots.values():
        if slot.status == "sufficient":
            continue
        anchors = {
            int(fact.attributes["anchor_line"])
            for ref in slot.evidence_refs
            if (fact := frame.profile.evidence[ref]).kind == "RULE"
            and fact.attributes.get("anchor_line") is not None
        }
        if len(anchors) != 1:
            raise ValueError(
                f"non-sufficient rule slot lacks one anchor: {slot.slot_id}"
            )
        by_line.setdefault(next(iter(anchors)), []).append(slot)
    findings = [
        AnalysisFinding(
            concern="rules",
            status="unverified",
            target_id=f"{target_scope}::R-{line}",
            coordinates=(line,),
            reasons=tuple(
                dict.fromkeys(
                    f"{slot.family}.{slot.role}={slot.status}: {missing}"
                    for slot in slots
                    for missing in (slot.missing_context or ("근거 불충분",))
                )
            ),
            candidate={
                "semantic_slots": [
                    {
                        "slot_id": slot.slot_id,
                        "status": slot.status,
                        "meaning": slot.meaning,
                        "missing_context": list(slot.missing_context),
                    }
                    for slot in slots
                ],
            },
            evidence_rule="code_rules_examples",
        )
        for line, slots in sorted(by_line.items())
    ]
    return findings, set(by_line)


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
    semantic_frames: list[SemanticFrame] = field(default_factory=list)


def plan_batches(
    inventory: BranchInventory,
    rows_per_call: int = DEFAULT_ROWS_PER_CALL,
    *,
    max_semantic_slots: int = MAX_RULE_SEMANTIC_SLOTS,
    max_evidence_chars: int = MAX_RULE_EVIDENCE_CHARS,
    max_evidence_references: int = MAX_RULE_EVIDENCE_REFERENCES,
    semantic_slot_count: SemanticSlotCount | None = None,
) -> list[list[int]]:
    """Pack required rows by exact input and response-shape bounds.

    ``when`` and ``direct`` are deterministic parser evidence.  They are no longer
    model output: verified ``cond``/``then`` are joined after the semantic response.
    Compact evidence chars therefore bound their input work, while the actual assigned
    semantic-slot count bounds forced output work.  Reference count is not an output
    field limit: it bounds how many exact conditions/effects those meanings must
    interpret.  Callers that do not produce semantic frames conservatively count one
    slot per row.
    """
    if rows_per_call < 1:
        raise ValueError("rows_per_call must be positive")
    if max_semantic_slots < 1:
        raise ValueError("max_semantic_slots must be positive")
    if max_evidence_chars < 1:
        raise ValueError("max_evidence_chars must be positive")
    if max_evidence_references < 1:
        raise ValueError("max_evidence_references must be positive")

    def packet_cost(lines: Sequence[int]) -> tuple[int, int, int]:
        evidence = rule_evidence(inventory, lines)
        references = sum(
            len(row.get("when") or ()) + len(row.get("direct") or ())
            for row in evidence["rows"].values()
        )
        evidence_chars = len(json.dumps(
            evidence, ensure_ascii=False, separators=(",", ":"),
        ))
        slots = (
            semantic_slot_count(lines)
            if semantic_slot_count is not None else len(lines)
        )
        if slots < 0:
            raise ValueError("semantic_slot_count must be nonnegative")
        return evidence_chars, references, slots

    def fits(lines: Sequence[int]) -> bool:
        if len(lines) > rows_per_call:
            return False
        evidence_chars, references, slots = packet_cost(lines)
        return (
            evidence_chars <= max_evidence_chars
            and references <= max_evidence_references
            and slots <= max_semantic_slots
        )

    obligation = required_rule_rows(inventory)
    rows_by_line = {int(row["line"]): row for row in inventory.table_rows()}

    # A small controller subtree is one semantic packet.  Splitting its parent from
    # its child rows made the model fold an initializer across an unseen child loop
    # (shopmall local_trim R91/R93).  Group only when the complete subtree already
    # satisfies both measured bounds; large trees remain splittable and no child
    # evidence is copied into the parent row.
    groups: list[list[int]] = []
    index = 0
    while index < len(obligation):
        line = obligation[index]
        row = rows_by_line[line]
        end_line = int(row.get("end_line") or line)
        group = [line]
        if inventory.has_nested_control(line):
            candidate = [
                item for item in obligation[index:]
                if item <= end_line
            ]
            if fits(candidate):
                group = candidate
        groups.append(group)
        index += len(group)

    batches: list[list[int]] = []
    batch: list[int] = []
    for group in groups:
        candidate = [*batch, *group]
        if batch and not fits(candidate):
            batches.append(batch)
            batch = list(group)
        else:
            batch = candidate
    if batch:
        batches.append(batch)
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
        if row["role"] in ("decision", "complement", "entry", "continuation")
        or (row["role"] in ("exit", "handler") and not row.get("in_branch"))
    ]


async def collect_rules_by_rows(
    *,
    invoke: RowInvoke,
    inventory: BranchInventory,
    target_scope: str,
    source: str,
    rows_per_call: int = DEFAULT_ROWS_PER_CALL,
    max_semantic_slots: int = MAX_RULE_SEMANTIC_SLOTS,
    max_evidence_chars: int = MAX_RULE_EVIDENCE_CHARS,
    max_evidence_references: int = MAX_RULE_EVIDENCE_REFERENCES,
    semantic_slot_count: SemanticSlotCount | None = None,
    extra_values: Sequence[str] = (),
    allowed_tables: Sequence[str] = (),
    read_tables: Sequence[str] | None = None,
    write_tables: Sequence[str] | None = None,
    table_ops: dict[str, str] | None = None,
    table_aliases: dict[str, str] | None = None,
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
            # A deadline can be caused by the amount of required structured meaning in
            # this row set.  A repetition abort can likewise be caused by one row
            # poisoning a multi-row forced response.  Do not retry identical content:
            # bisect the required rows so every smaller response has a different
            # schema/evidence packet and remains independently gradeable.  A failing
            # singleton is still recorded as unavailable instead of being hidden.
            if error.kind in {"deadline", "repetition"} and len(batch) > 1:
                midpoint = len(batch) // 2
                left, right = await asyncio.gather(
                    run_batch(batch[:midpoint]),
                    run_batch(batch[midpoint:]),
                )
                return (
                    [*left[0], *right[0]],
                    [*left[1], *right[1]],
                    [*left[2], *right[2]],
                    1 + left[3] + right[3],
                )
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
            return [], findings, [], 1
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
            table_aliases=table_aliases,
            require_exact_conditions=True,
        )
        accepted, findings = partition_rule_candidates(
            output,
            report,
            assigned=set(batch),
            evidence_rule="code_rules_examples",
        )
        frame = output.semantic_frame
        semantic_findings, untrusted_rows = _semantic_rule_findings(
            frame, target_scope=target_scope,
        )
        if untrusted_rows:
            accepted = [
                rule for rule in accepted if rule.anchor_line not in untrusted_rows
            ]
        findings.extend(semantic_findings)
        frames = [frame] if frame is not None else []
        return accepted, [scoped(finding) for finding in findings], frames, 1

    batches = plan_batches(
        inventory,
        rows_per_call,
        max_semantic_slots=max_semantic_slots,
        max_evidence_chars=max_evidence_chars,
        max_evidence_references=max_evidence_references,
        semantic_slot_count=semantic_slot_count,
    )
    if not batches:
        return RowCollection(rules=[], findings=[], calls=0)
    results = await asyncio.gather(*(run_batch(batch) for batch in batches))
    rules = [rule for batch_rules, _, _frames, _calls in results for rule in batch_rules]
    findings = [
        finding for _, batch_findings, _frames, _calls in results
        for finding in batch_findings
    ]
    semantic_frames = [
        frame for _, _, batch_frames, _calls in results for frame in batch_frames
    ]
    rules.sort(key=lambda rule: rule.anchor_line)
    findings.sort(key=lambda finding: (finding.coordinates, finding.status, finding.target_id))
    return RowCollection(
        rules=rules, findings=findings,
        calls=sum(batch_calls for _, _, _, batch_calls in results),
        semantic_frames=semantic_frames,
    )
