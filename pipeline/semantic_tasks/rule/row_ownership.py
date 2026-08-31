"""rule 의무 행의 소유자 해소와 배분 (spec 117).

**분기표는 루틴이 소유한다.** 입력 예산 때문에 하강한 배치 노드(`_plan_rule_units` 의
units/gates)는 자기 줄 범위의 행만 배분받는다. 이 모듈의 유일한 책임은 **입력 본문 크기와
의무 소유권을 분리**하는 것이다.

종전에는 배치 노드마다 자기 분기표를 만들었는데, statement/control 사실의 생산자
(`step6_build_context/control_subtree.py`)가 **루틴 id** 로만 집계하므로 비루틴 노드는 항상
빈 분기표를 받았다. 그 결과 DBMS 의 rule 의무가 0건이 되고 findings 0 으로 통과했다
(2026-08-07 실측, 헌법 IV 위반 상태).

경로 조건(`when`)은 루틴 전체 문맥에서 계산돼야 의미가 맞으므로, 배분은 **잘라 만드는 것이
아니라 나눠 갖는 것**이다.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable, Mapping, Sequence

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import ROUTINE_LABELS
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_obligations import required_rule_rows


def owning_routine(node: CodeNode, code_by_id: Mapping[str, CodeNode]) -> CodeNode:
    """가장 가까운 routine 조상 (자신이 routine 이면 자신).

    routine 판정은 ``ontology.ROUTINE_LABELS`` 단일 정의처를 쓴다. 소유자가 없으면 추측하지
    않고 좌표와 함께 실패한다 — 조용히 자기 자신으로 폴백하면 이 spec 이 고치려는 결함이
    그대로 되살아난다.
    """
    current = node
    seen: set[str] = set()
    while current.label not in ROUTINE_LABELS:
        seen.add(current.id)
        parent_id = current.parent_id
        if parent_id is None:
            raise ValueError(
                f"rule 의무 소유 routine 을 찾지 못했다: {node.id!r} "
                f"({node.file_path}:{node.start_line}) — 부모 사슬 끝까지 "
                f"{sorted(ROUTINE_LABELS)} 라벨이 없다"
            )
        if parent_id in seen:
            raise ValueError(
                f"parent_id 사슬에 순환이 있다: {node.id!r} → {parent_id!r}"
            )
        parent = code_by_id.get(parent_id)
        if parent is None:
            raise ValueError(
                f"parent_id 가 가리키는 노드가 없다: {current.id!r} → {parent_id!r}"
            )
        current = parent
    return current


def assign_rule_rows(
    rows: Sequence[int],
    owners: Iterable[CodeNode],
    *,
    scope_id: str,
) -> dict[str, tuple[int, ...]]:
    """의무 행을 **자기를 포함하는 가장 안쪽 소유자 하나**에 배분한다 (spec 117 §3.1).

    - 여러 소유자가 포함하면 범위가 가장 좁은 쪽이 갖는다. 컨테이너(gate)는 자손이 가져가고
      남은 행 — 즉 자기 줄의 게이트 조건만 갖게 된다.
    - 범위가 완전히 같으면 (start, id) 로 결정론 tie-break 한다. 어느 경우에도 한 행이 두
      소유자에게 가지 않는다.
    - 어느 소유자도 포함하지 않는 행은 **좌표와 함께 실패**한다(§3.3). 조용히 버리면
      의무 회계의 잔차가 소리 없이 생긴다.
    """
    owner_list = list(owners)
    if not owner_list:
        raise ValueError(f"{scope_id}: rule 의무를 배분할 소유자가 없다")

    assigned: dict[str, list[int]] = {owner.id: [] for owner in owner_list}
    unowned: list[int] = []
    for row in rows:
        candidates = [
            owner for owner in owner_list
            if owner.start_line <= row <= owner.end_line
        ]
        if not candidates:
            unowned.append(row)
            continue
        best = min(
            candidates,
            key=lambda owner: (owner.end_line - owner.start_line, owner.start_line, owner.id),
        )
        assigned[best.id].append(row)

    if unowned:
        raise ValueError(
            f"{scope_id}: rule 의무 행 {sorted(unowned)} 을 소유하는 배치 노드가 없다 "
            f"(소유자 {len(owner_list)}개: "
            f"{[(owner.id, owner.start_line, owner.end_line) for owner in owner_list[:4]]}…) "
            "— 루틴이 소유자 집합에 포함됐는지 확인하라"
        )
    return {node_id: tuple(sorted(lines)) for node_id, lines in assigned.items()}


def plan_rule_row_ownership(
    owner_ids: Iterable[str],
    code_by_id: Mapping[str, CodeNode],
    inventory_for: Callable[[CodeNode], BranchInventory],
) -> dict[str, tuple[int, ...]]:
    """배치 노드들을 소유 루틴별로 묶어 루틴당 분기표를 **한 번만** 만들고 행을 배분한다.

    ``inventory_for`` 는 루틴 노드를 받아 그 루틴의 분기표를 돌려주는 호출자 주입이다 —
    이 모듈은 소스 캐시·컨텍스트를 모른다(헌법 VIII 의존 방향).

    소유 루틴은 **언제나 소유자 집합에 포함**된다. entry 행처럼 어느 자식 범위에도 들어가지
    않는 행의 소유자가 있어야 잔차 0이 성립하기 때문이다. framework 는 분석 대상이 루틴뿐
    이므로 소유자가 루틴 하나가 되고 배분 결과는 전체 행이다(결과 불변).
    """
    by_routine: dict[str, dict[str, CodeNode]] = defaultdict(dict)
    for node_id in owner_ids:
        node = code_by_id.get(node_id)
        if node is None:
            raise ValueError(f"rule 배치 노드가 code_by_id 에 없다: {node_id!r}")
        routine = owning_routine(node, code_by_id)
        by_routine[routine.id][node.id] = node
        by_routine[routine.id][routine.id] = routine

    plan: dict[str, tuple[int, ...]] = {}
    for routine_id, owners in by_routine.items():
        inventory = inventory_for(code_by_id[routine_id])
        plan.update(assign_rule_rows(
            required_rule_rows(inventory),
            owners.values(),
            scope_id=routine_id,
        ))
    return plan
