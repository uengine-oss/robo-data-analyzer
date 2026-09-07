"""분기표·커버리지 감사 산출물의 단일 소유자.

`branch-tables`는 원 분기표를, `rule-structures`는 그 분기표에서 물질화한 exact
condition/effects와 evidence를 보존한다. 종전에 이 감사 기록은 framework 엔진 안에만
있어서 DBMS의 `[A]`가 항상 0이었고, Rule provider를 제거한 뒤에는 prompt 기록도 0이므로
두 전략 모두 전용 구조 산출물을 남겨야 헌법 IX 2층이 성립한다.

기록 단위는 **분기표의 소유자와 같다 = 루틴 하나당 파일 하나.** 배치 노드마다 남기면
같은 행이 여러 파일에 중복돼 회계가 부풀려진다.
"""
from __future__ import annotations

import json
from typing import Iterable, Mapping

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.run_output_context import active_run_dir
from pipeline.checks.audit_artifact_paths import audit_file_stem
from pipeline.checks.branch_inventory import BranchInventory
from pipeline.checks.coverage_residue import coverage_residue
from pipeline.checks.rule_audit_artifact import persist_rule_audit
from pipeline.checks.rule_obligations import required_rule_rows
from pipeline.meaning.rule.row_ownership import owning_routine


def persist_branch_table(fn: CodeNode, inventory: BranchInventory) -> None:
    """활성 분석 run 에만 분기표 정답지를 기록한다.

    독립 단위 호출에는 run 산출물 소유권이 없으므로 기본 ``logs/`` 로 폴백하지 않는다.
    """
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / "branch-tables"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{audit_file_stem(fn.id)}.json").write_text(
        json.dumps(inventory.table_rows(), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def persist_coverage(fn: CodeNode, inventory: BranchInventory) -> None:
    """커버리지 잔차 감사(골키퍼 그물2)를 run 산출물로 기록한다.

    함수별 잔차·위험구문 장부는 실행 산출물에서 corpus 단위로 집계할 수 있다.
    분기표와 같은 소유권 규칙 — 활성 run 이 없으면 기록하지 않는다.
    """
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / "coverage"
    directory.mkdir(parents=True, exist_ok=True)
    report = coverage_residue(inventory)
    report["function"] = fn.id
    (directory / f"{audit_file_stem(fn.id)}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def persist_routine_audits(
    inventories: Mapping[str, BranchInventory],
    code_by_id: Mapping[str, CodeNode],
    *,
    row_plan: Mapping[str, Iterable[int]],
) -> list[str]:
    """루틴별 분기표·Rule 구조·커버리지를 정확히 한 번 기록한다.

    DBMS 배치 엔진은 노드마다 분기표를 만들지 않고 루틴 것을 나눠 쓰므로, 감사 기록도
    루틴 단위여야 회계가 맞는다.
    """
    written: list[str] = []
    owners_by_routine: dict[str, dict[str, str]] = {
        routine_id: {} for routine_id in inventories
    }
    for owner_id, rows in row_plan.items():
        owner = code_by_id.get(owner_id)
        if owner is None:
            raise ValueError(f"Rule row owner가 code_by_id 에 없다: {owner_id!r}")
        routine = owning_routine(owner, code_by_id)
        if routine.id not in owners_by_routine:
            raise ValueError(
                f"Rule row owner의 routine inventory가 없다: {owner_id!r} → {routine.id!r}"
            )
        mapping = owners_by_routine[routine.id]
        for row in rows:
            row_key = f"R{int(row)}"
            if row_key in mapping:
                raise ValueError(
                    f"Rule row owner가 중복됐다: {routine.id!r} {row_key} "
                    f"({mapping[row_key]!r}, {owner_id!r})"
                )
            mapping[row_key] = owner_id
    for routine_id, inventory in inventories.items():
        routine = code_by_id.get(routine_id)
        if routine is None:
            raise ValueError(f"분기표 소유 루틴이 code_by_id 에 없다: {routine_id!r}")
        persist_branch_table(routine, inventory)
        expected = {f"R{row}" for row in required_rule_rows(inventory)}
        actual = set(owners_by_routine[routine_id])
        if actual != expected:
            raise ValueError(
                f"{routine_id}: Rule row owner coverage mismatch: "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        persist_rule_audit(
            routine,
            inventory,
            row_owners=owners_by_routine[routine_id],
            condition_bundle=routine.extra.get("_resolved_rule_condition_bundle"),
        )
        persist_coverage(routine, inventory)
        written.append(routine_id)
    return written
