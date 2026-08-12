"""조건이 실제로 없는 행은 조건을 요구하지 않는다 (spec 118 §2.1).

`control_roles` 는 **의미 축**만 선언하고, 조건 유무는 노드의 사실이다. 조건이 빈
decision 을 그대로 두면 채점기(`rule_grading.py` decision 행은 cond 필수)가 모델에게
증거에 없는 답을 요구한다 — 04 §4.4-3 정보 충분성상 `needs_context` 이며 결함은
증거 생산 쪽이다.

**언어 중립 결함이다**(spec 118 §1.1 실측):
- dbms  `PRC_DATA_ANALYSYS_TEST2.sql:131` 등 커서 `LOOP` 5건
- framework `zapamcom10200.c:1948` `for(;;)` 1건
- java   `FINALLY` — 코퍼스에 Java 가 없어 드러나지 않았을 뿐 계약이 이미 모순이었다
"""
import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory


def _role_at(source, subtree, lang, line, statements=(), call_lines=()):
    inventory = build_inventory(
        source, subtree, lang, statements=tuple(statements), call_lines=tuple(call_lines),
    )
    rows = {row["line"]: row for row in inventory.table_rows()}
    return rows[line]


class ConditionlessDecisionBecomesControllerTest(unittest.TestCase):
    """S1·S2 — 조건이 빈 반복문은 어느 언어에서든 controller 다."""

    def test_s1_sql_bare_loop_is_controller(self):
        source = "\n".join([
            "PROCEDURE p IS",              # 1
            "BEGIN",                       # 2
            "  LOOP",                      # 3
            "    FETCH c INTO v;",         # 4
            "    EXIT WHEN c%NOTFOUND;",   # 5
            "  END LOOP;",                 # 6
            "END;",                        # 7
        ])
        tree = [ControlSubtreeNode(node_id="loop@3", ntype="LOOP", start=3, end=6)]
        row = _role_at(source, tree, "sql", 3)
        self.assertEqual("controller", row["role"])
        self.assertEqual("", row["condition"])

    def test_s2_c_forever_loop_is_controller(self):
        """`zapamcom10200.c:1948` 의 최소 재현 — framework 에도 같은 결함이 있었다."""
        source = "\n".join([
            "long f(void) {",              # 1
            "  for(;;)",                   # 2
            "  {",                         # 3
            "    rc = fetch();",           # 4
            "  }",                         # 5
            "  return rc;",                # 6
            "}",                           # 7
        ])
        tree = [ControlSubtreeNode(node_id="loop@2", ntype="LOOP", start=2, end=5)]
        row = _role_at(
            source, tree, "c", 2,
            statements=(
                StatementFact("s4", "ASSIGNMENT", 4, 4, expression="fetch()",
                              target="rc", operator="="),
                StatementFact("s6", "RETURN", 6, 6, expression="rc"),
            ),
            call_lines=((4, "fetch"),),
        )
        self.assertEqual("controller", row["role"])


class ExistingRoleContractsUnchangedTest(unittest.TestCase):
    """S4·S5 — 조건이 있으면 decision 이고, CASE default 는 여전히 complement 다."""

    def test_s4_conditional_loop_stays_decision(self):
        source = "\n".join([
            "long f(long n) {",            # 1
            "  while (i < n)",             # 2
            "  {",                         # 3
            "    i = i + 1;",              # 4
            "  }",                         # 5
            "  return i;",                 # 6
            "}",                           # 7
        ])
        tree = [ControlSubtreeNode(node_id="loop@2", ntype="LOOP", start=2, end=5,
                                   expression="i < n")]
        row = _role_at(
            source, tree, "c", 2,
            statements=(
                StatementFact("s4", "ASSIGNMENT", 4, 4, expression="i + 1",
                              target="i", operator="="),
                StatementFact("s6", "RETURN", 6, 6, expression="i"),
            ),
        )
        self.assertEqual("decision", row["role"])
        self.assertEqual("i < n", row["condition"])

    def test_s5_caseless_case_label_stays_complement(self):
        source = "\n".join([
            "long f(long k) {",            # 1
            "  switch (k)",                # 2
            "  {",                         # 3
            "    case 1:",                 # 4
            "      rc = 1;",               # 5
            "      break;",                # 6
            "    default:",                # 7
            "      rc = 9;",               # 8
            "  }",                         # 9
            "  return rc;",                # 10
            "}",                           # 11
        ])
        tree = [ControlSubtreeNode(
            node_id="switch@2", ntype="SWITCH", start=2, end=9, expression="k",
            children=[
                ControlSubtreeNode(node_id="case@4", ntype="CASE", start=4, end=6,
                                   expression="1"),
                ControlSubtreeNode(node_id="case@7", ntype="CASE", start=7, end=8),
            ],
        )]
        statements = (
            StatementFact("s5", "ASSIGNMENT", 5, 5, expression="1", target="rc", operator="="),
            StatementFact("s8", "ASSIGNMENT", 8, 8, expression="9", target="rc", operator="="),
            StatementFact("s10", "RETURN", 10, 10, expression="rc"),
        )
        self.assertEqual("complement", _role_at(source, tree, "c", 7, statements)["role"])
        self.assertEqual("decision", _role_at(source, tree, "c", 4, statements)["role"])


if __name__ == "__main__":
    unittest.main()
