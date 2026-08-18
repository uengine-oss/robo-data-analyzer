# -*- coding: utf-8 -*-
"""spec 117 — rule 의무 행의 소유자는 루틴이고, 배치 노드에는 자기 범위 행만 배분된다.

결함(2026-08-07 실측): DBMS 는 `_plan_rule_units` 가 루틴의 **자식**부터 단위를 잡는데
`build_statement_facts`/`build_control_subtrees` 는 **루틴 id** 로만 사실을 만든다. 그래서
모든 rule 단위가 빈 분기표를 받아 의무 0건이 되고 findings 0 으로 통과했다(헌법 IV 위반).
"""
from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step6_build_context.control_subtree import (
    build_control_subtrees,
    build_statement_facts,
)
from pipeline.quality_gates.rule_obligations import required_rule_rows
from pipeline.steps.step7_analyze_code.rule_row_ownership import (
    assign_rule_rows,
    owning_routine,
    plan_rule_row_ownership,
)

SQL_SRC = """PROCEDURE p IS
BEGIN
  IF v_flag = 'Y' THEN
    v_cnt := v_cnt + 1;
  END IF;
  RETURN;
END;
"""

C_SRC = """int fn(int a) {
  if (a > 0) {
    return 1;
  }
  return 0;
}
"""


def _node(nid, label, start, end, parent=None, **kw) -> CodeNode:
    return CodeNode(
        id=nid, signature=label, file_path=kw.pop("file_path", "p.sql"),
        start_line=start, end_line=end, label=label, parent_id=parent, **kw,
    )


def _sql_tree() -> tuple[CodeNode, CodeNode, CodeNode, CodeNode]:
    routine = _node("code:p.sql:p@1", "PROCEDURE", 1, 7)
    if_node = _node("code:p.sql:p@1:IF@3", "IF", 3, 5, routine.id, expression="v_flag = 'Y'")
    assign = _node(
        "code:p.sql:p@1:IF@3:ASSIGNMENT@4", "ASSIGNMENT", 4, 4, if_node.id,
        expression="v_cnt + 1", target="v_cnt", operator=":=",
    )
    ret = _node("code:p.sql:p@1:RETURN@6", "RETURN", 6, 6, routine.id)
    return routine, if_node, assign, ret


def _inventory_for(nodes, routine, src, lang):
    facts = build_statement_facts(list(nodes))
    subtrees = build_control_subtrees(list(nodes))
    lines = src.splitlines()
    body = "\n".join(lines[routine.start_line - 1: routine.end_line])
    return build_inventory(
        body,
        subtrees.get(routine.id),
        lang,
        statements=tuple(facts.get(routine.id, ())),
        line_offset=max(routine.start_line - 1, 0),
    )


class OwningRoutine(unittest.TestCase):
    def test_statement_node_resolves_to_its_routine(self) -> None:
        routine, if_node, assign, ret = _sql_tree()
        code_by_id = {n.id: n for n in (routine, if_node, assign, ret)}
        self.assertIs(owning_routine(assign, code_by_id), routine)
        self.assertIs(owning_routine(if_node, code_by_id), routine)

    def test_routine_owns_itself(self) -> None:
        routine, *_ = _sql_tree()
        self.assertIs(owning_routine(routine, {routine.id: routine}), routine)

    def test_orphan_statement_fails_with_coordinates(self) -> None:
        orphan = _node("code:p.sql:X@9", "RETURN", 9, 9)
        with self.assertRaises(ValueError) as caught:
            owning_routine(orphan, {orphan.id: orphan})
        self.assertIn("code:p.sql:X@9", str(caught.exception))


class RowAssignment(unittest.TestCase):
    """S1 — 배분 합 == 루틴 분기표 행, 중복 0, 잔차 0."""

    def test_units_partition_the_routine_rows(self) -> None:
        routine, if_node, assign, ret = _sql_tree()
        nodes = (routine, if_node, assign, ret)
        inventory = _inventory_for(nodes, routine, SQL_SRC, "sql")
        required = required_rule_rows(inventory)
        self.assertGreater(len(required), 0, "루틴 분기표가 비면 이 테스트가 무의미하다")

        # 루틴은 항상 소유자 집합에 포함된다 — entry 행처럼 어느 자식 범위에도
        # 들어가지 않는 행의 소유자가 있어야 잔차 0이 성립한다.
        assigned = assign_rule_rows(required, (routine, if_node, ret), scope_id=routine.id)

        flat = [row for rows in assigned.values() for row in rows]
        self.assertEqual(sorted(flat), sorted(required), "합계가 루틴 행과 달라졌다")
        self.assertEqual(len(flat), len(set(flat)), "행이 두 번 배분됐다")

    def test_innermost_owner_wins(self) -> None:
        """S2 — 게이트(컨테이너)는 자손이 가져가고 남은 행만 갖는다."""
        routine, if_node, assign, ret = _sql_tree()
        nodes = (routine, if_node, assign, ret)
        inventory = _inventory_for(nodes, routine, SQL_SRC, "sql")
        required = required_rule_rows(inventory)

        # routine 자신을 게이트로 함께 넣어도 IF 범위 행은 IF 가 가져가야 한다.
        assigned = assign_rule_rows(required, (routine, if_node, ret), scope_id=routine.id)
        for row in assigned.get(if_node.id, ()):
            self.assertTrue(
                if_node.start_line <= row <= if_node.end_line,
                f"IF 가 자기 범위 밖 행 {row} 을 가져갔다",
            )
        gate_rows = assigned.get(routine.id, ())
        for row in gate_rows:
            self.assertFalse(
                if_node.start_line <= row <= if_node.end_line,
                f"게이트가 자손 범위 행 {row} 을 중복 소유했다",
            )

    def test_node_without_rows_gets_empty_tuple(self) -> None:
        """S3 — 배분 행 0개는 정상 상태이며 예외가 아니다."""
        routine, if_node, assign, ret = _sql_tree()
        nodes = (routine, if_node, assign, ret)
        inventory = _inventory_for(nodes, routine, SQL_SRC, "sql")
        required = required_rule_rows(inventory)
        empty = _node("code:p.sql:p@1:COMMIT@6", "COMMIT", 6, 6, routine.id)
        assigned = assign_rule_rows(
            required, (routine, if_node, ret, empty), scope_id=routine.id,
        )
        # ret(6-6) 과 범위가 같으면 결정론 tie-break 로 하나만 가져간다 — 둘 다 갖지 않는다.
        self.assertEqual(
            set(assigned[empty.id]) & set(assigned[ret.id]), set(),
            "같은 범위 노드가 행을 중복 소유했다",
        )
        self.assertIn(empty.id, assigned, "행 0개인 노드도 계획에 나타나야 한다")

    def test_unowned_row_fails_loudly(self) -> None:
        """S4 — 어느 단위도 포함하지 않는 행은 좌표와 함께 실패한다."""
        routine, if_node, assign, ret = _sql_tree()
        nodes = (routine, if_node, assign, ret)
        inventory = _inventory_for(nodes, routine, SQL_SRC, "sql")
        required = required_rule_rows(inventory)
        with self.assertRaises(ValueError) as caught:
            assign_rule_rows(required, (if_node,), scope_id=routine.id)
        message = str(caught.exception)
        self.assertIn(routine.id, message)


class FrameworkUnchanged(unittest.TestCase):
    """S5 — 루틴이 곧 단위인 framework 는 전체 행을 그대로 받는다."""

    def test_routine_unit_receives_every_row(self) -> None:
        fn = _node("code:s.c:fn", "FUNCTION", 1, 6, file_path="s.c")
        if_node = _node("code:s.c:fn:IF@2", "IF", 2, 4, fn.id, file_path="s.c",
                        expression="a > 0")
        ret_in = _node("code:s.c:fn:IF@2:RETURN@3", "RETURN", 3, 3, if_node.id,
                       file_path="s.c", expression="1")
        ret_out = _node("code:s.c:fn:RETURN@5", "RETURN", 5, 5, fn.id,
                        file_path="s.c", expression="0")
        nodes = (fn, if_node, ret_in, ret_out)
        inventory = _inventory_for(nodes, fn, C_SRC, "c")
        required = required_rule_rows(inventory)

        assigned = assign_rule_rows(required, (fn,), scope_id=fn.id)
        self.assertEqual(sorted(assigned[fn.id]), sorted(required))


class PathConditionPreserved(unittest.TestCase):
    """S6 — 단위에 배분된 행의 경로 조건이 루틴 문맥을 유지한다."""

    def test_row_inside_branch_keeps_its_path_condition(self) -> None:
        routine, if_node, assign, ret = _sql_tree()
        nodes = (routine, if_node, assign, ret)
        inventory = _inventory_for(nodes, routine, SQL_SRC, "sql")
        required = required_rule_rows(inventory)
        assigned = assign_rule_rows(required, (routine, if_node, ret), scope_id=routine.id)

        rows = assigned[if_node.id]
        self.assertTrue(rows, "IF 단위에 배분된 행이 없다")
        evidence = rule_evidence(inventory, rows)
        self.assertTrue(
            any(row.get("when") for row in evidence["rows"].values()),
            "분기 안 행인데 경로 조건이 비었다 — 루틴 문맥이 끊겼다",
        )


class OwnershipPlan(unittest.TestCase):
    """소유 루틴 그룹핑 + 루틴당 분기표 1회 생성."""

    def test_plan_groups_owners_by_routine(self) -> None:
        routine, if_node, assign, ret = _sql_tree()
        nodes = (routine, if_node, assign, ret)
        code_by_id = {n.id: n for n in nodes}
        inventory = _inventory_for(nodes, routine, SQL_SRC, "sql")
        built: list[str] = []

        def inventory_for(node: CodeNode):
            built.append(node.id)
            return inventory

        plan = plan_rule_row_ownership(
            (if_node.id, ret.id), code_by_id, inventory_for,
        )
        self.assertEqual(built, [routine.id], "루틴당 분기표는 한 번만 만들어야 한다")
        flat = [row for rows in plan.values() for row in rows]
        self.assertEqual(sorted(flat), sorted(required_rule_rows(inventory)))


if __name__ == "__main__":
    unittest.main()
