"""Build routine-relative control trees and statement facts from the AST graph."""
from __future__ import annotations

from collections import defaultdict

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.source_languages import ROUTINE_LABELS, control_structure_labels, statement_labels


def build_control_subtrees(
    code_nodes: list[CodeNode],
) -> dict[str, list[ControlSubtreeNode]]:
    """Return control trees keyed by owning routine id.

    The reader already persists structural identity and absolute source ranges.
    This boundary converts only coordinates: a control at the routine's first
    physical line becomes line 1 in the routine ``code_text`` contract.

    소유자는 **가장 가까운 제어 조상, 없으면 소유 routine** 이다 — 사이에 낀 비제어 구조
    노드(PL/SQL ``TRIGGER_BLOCK``·``DECLARE``)는 건너뛴다. 종전에는 ``parent_id`` 를 직접
    키로 써서 ``build_statement_facts`` 와 **같은 트리를 다르게 읽었고**, 그 결과 rwis 트리거
    ``TRG_INS_RDITAG_TB`` 는 IF 2개를 가지고도 분기표가 비어 규칙 0건으로 조용히 통과했다
    (2026-08-08 실측, 헌법 IV).
    """
    controls = control_structure_labels()
    by_id = {node.id: node for node in code_nodes}

    def control_owner(node: CodeNode) -> CodeNode | None:
        current = node
        while current.parent_id is not None:
            parent = by_id.get(current.parent_id)
            if parent is None:
                return None
            if parent.label in controls or parent.label in ROUTINE_LABELS:
                return parent
            current = parent
        return None

    children_by_parent: dict[str, list[CodeNode]] = defaultdict(list)
    for node in code_nodes:
        if node.label not in controls:
            continue
        owner = control_owner(node)
        if owner is not None:
            children_by_parent[owner.id].append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda node: (node.start_line, node.end_line, node.id))

    def convert(node: CodeNode, routine: CodeNode) -> ControlSubtreeNode:
        start = node.start_line - routine.start_line + 1
        end = node.end_line - routine.start_line + 1
        if start < 1 or end < start or node.end_line > routine.end_line:
            raise ValueError(
                "control range escapes routine: "
                f"control={node.id!r} [{node.start_line},{node.end_line}] "
                f"routine={routine.id!r} [{routine.start_line},{routine.end_line}]"
            )
        return ControlSubtreeNode(
            node_id=node.id,
            ntype=node.label,
            start=start,
            end=end,
            children=[convert(child, routine) for child in children_by_parent.get(node.id, ())],
            expression=node.expression,
            condition_timing=node.condition_timing,
        )

    result: dict[str, list[ControlSubtreeNode]] = {}
    for routine in code_nodes:
        if routine.label not in ROUTINE_LABELS:
            continue
        roots = children_by_parent.get(routine.id, ())
        if roots:
            result[routine.id] = [convert(root, routine) for root in roots]

    return result


def build_statement_facts(
    code_nodes: list[CodeNode],
    variables: list | None = None,
    constants: list | None = None,
) -> dict[str, list[StatementFact]]:
    """Return parser-owned statement facts keyed by owning routine id.

    statement 노드의 구조 부모는 가장 안쪽 제어 노드일 수 있으므로 parent 사슬을
    올라가 소유 routine 을 찾는다. 좌표는 routine ``code_text`` 기준 1-based 로
    변환한다(control_subtree 와 동일 계약). routine 밖 statement 는 소유자가 없으면
    조용히 버리지 않고 무시 사유가 구조적으로 명확한 경우(모듈 레벨)만 제외한다.

    ``variables`` 가 주어지면 routine 안 지역 선언의 초기화(``long rc = 0;`` —
    parser 가 VARIABLE.initValue 로 보존)를 ASSIGNMENT-동등 사실로 합성한다.
    선언 초기화는 실제 실행 효과이며, 같은 행에 명시 ASSIGNMENT statement 가
    이미 있으면 합성하지 않는다(중복 금지 — Python self 필드 등).
    """
    statements = statement_labels()
    by_id = {node.id: node for node in code_nodes}

    def owning_routine(node: CodeNode) -> CodeNode | None:
        current = node
        while current.parent_id is not None:
            parent = by_id.get(current.parent_id)
            if parent is None:
                return None
            if parent.label in ROUTINE_LABELS:
                return parent
            current = parent
        return None

    result: dict[str, list[StatementFact]] = defaultdict(list)
    for node in code_nodes:
        if node.label not in statements:
            continue
        routine = owning_routine(node)
        if routine is None:
            continue
        start = node.start_line - routine.start_line + 1
        end = node.end_line - routine.start_line + 1
        if start < 1 or end < start or node.end_line > routine.end_line:
            raise ValueError(
                "statement range escapes routine: "
                f"statement={node.id!r} [{node.start_line},{node.end_line}] "
                f"routine={routine.id!r} [{routine.start_line},{routine.end_line}]"
            )
        statement_target = node.target or node.name or None
        if statement_target is None and node.label in {"BREAK", "CONTINUE"}:
            statement_target = node.label.lower()
        result[routine.id].append(StatementFact(
            node_id=node.id,
            label=node.label,
            start=start,
            end=end,
            expression=node.expression,
            # GOTO 류의 대상(라벨)은 parser 가 name 에 싣는다 — 효과 대상은 target 로
            # 정규화한다(statement_effect_ops 소비 계약). 대입은 target 이 이미 있다.
            target=statement_target,
            operator=node.operator,
        ))
    routines_by_file: dict[str, list[CodeNode]] = defaultdict(list)
    for node in code_nodes:
        if node.label in ROUTINE_LABELS:
            routines_by_file[node.file_path].append(node)

    # 지역 const 선언(CONSTANT_FIELD)의 초기화도 같은 실행 효과다 — Constant.value 로 합성.
    for variable in list(variables or []) + list(constants or []):
        init_value = (
            getattr(variable, "init_value", "") or getattr(variable, "value", "") or ""
        )
        name = getattr(variable, "name", "") or ""
        if not init_value or not name:
            continue
        line = int(getattr(variable, "start_line", 0) or 0)
        containing = [
            routine
            for routine in routines_by_file.get(getattr(variable, "file_path", ""), ())
            if routine.start_line <= line <= routine.end_line
        ]
        if not containing:
            continue    # 모듈/클래스 레벨 선언 — routine 효과가 아니다
        routine = min(containing, key=lambda r: r.end_line - r.start_line)
        start = line - routine.start_line + 1
        if any(
            fact.label == "ASSIGNMENT" and fact.start == start
            for fact in result.get(routine.id, ())
        ):
            continue
        result[routine.id].append(StatementFact(
            node_id=f"{getattr(variable, 'id', name)}::init",
            label="ASSIGNMENT",
            start=start,
            end=start,
            expression=" ".join(init_value.split()),
            target=name,
            operator="=",
        ))

    for facts in result.values():
        facts.sort(key=lambda fact: (fact.start, fact.end, fact.node_id))
    return dict(result)
