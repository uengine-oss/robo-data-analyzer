"""분기표·커버리지 감사 산출물의 단일 소유자 (spec 117).

`judge_run.py [A]` 회계는 `run_dir/branch-tables/*.json` 을 센다. 종전에 이 기록은
framework 엔진(`three_split_llm_fanout`) 안에만 있어서 **DBMS run 은 분기표를 한 건도
남기지 않았고, 그래서 `[A]` 가 항상 0** 이었다 — 제품 결함이 아니라 측정 부재였고,
헌법 IX 2층이 DBMS 에서 성립하지 않는 원인이었다(2026-08-07 실측).

기록 단위는 **분기표의 소유자와 같다 = 루틴 하나당 파일 하나.** 배치 노드마다 남기면
같은 행이 여러 파일에 중복돼 회계가 부풀려진다.
"""
from __future__ import annotations

import json
from typing import Iterable, Mapping

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.run_output_context import active_run_dir
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.coverage_residue import coverage_residue


def _safe_name(node_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in node_id)


def persist_branch_table(fn: CodeNode, inventory: BranchInventory) -> None:
    """활성 분석 run 에만 분기표 정답지를 기록한다.

    독립 단위 호출에는 run 산출물 소유권이 없으므로 기본 ``logs/`` 로 폴백하지 않는다.
    """
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / "branch-tables"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{_safe_name(fn.id)}.json").write_text(
        json.dumps(inventory.table_rows(), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def persist_coverage(fn: CodeNode, inventory: BranchInventory) -> None:
    """커버리지 잔차 감사(골키퍼 그물2)를 run 산출물로 기록한다.

    함수별 잔차·위험구문 장부는 ``scripts/audit_coverage.py`` 가 corpus 단위로 집계한다.
    분기표와 같은 소유권 규칙 — 활성 run 이 없으면 기록하지 않는다.
    """
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / "coverage"
    directory.mkdir(parents=True, exist_ok=True)
    report = coverage_residue(inventory)
    report["function"] = fn.id
    (directory / f"{_safe_name(fn.id)}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def persist_routine_audits(
    inventories: Mapping[str, BranchInventory],
    code_by_id: Mapping[str, CodeNode],
) -> list[str]:
    """루틴별 분기표·커버리지를 **루틴당 정확히 한 번** 기록하고 기록한 id 를 돌려준다.

    DBMS 배치 엔진은 노드마다 분기표를 만들지 않고 루틴 것을 나눠 쓰므로, 감사 기록도
    루틴 단위여야 회계가 맞는다.
    """
    written: list[str] = []
    for routine_id, inventory in inventories.items():
        routine = code_by_id.get(routine_id)
        if routine is None:
            raise ValueError(f"분기표 소유 루틴이 code_by_id 에 없다: {routine_id!r}")
        persist_branch_table(routine, inventory)
        persist_coverage(routine, inventory)
        written.append(routine_id)
    return written
