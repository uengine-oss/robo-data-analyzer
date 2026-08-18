"""평면 대안 절 사슬과 PL/SQL 고유 철자의 행 계약 (spec 118 §2.2·§2.3).

PL/SQL parser 는 `ELSIF`/`ELSE` 를 `IF` 의 **자식**으로 낸다. 조상 사슬이 그것을 "본문"으로
보면 부모 조건을 성립으로 상속해 경로가 정반대가 된다 — spec 116 1단계에서 겪은
"else-if 사슬을 조건 상속으로 쓰면 정반대" 결함이 다른 형상으로 재발한 것이다.

실측 좌표(`FN_EDIT_YN.sql`): 수리 전 R41=`["C35","C41"]`(옳게는 `["!C35","C41"]`),
R50=`["!C35","C48","C50"]`(옳게는 `!C48`), R45=`["!C35"]`(`!C41` 누락).
"""
import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.quality_gates.rule_obligations import required_rule_rows

# PROCEDURE p IS
# BEGIN
#   IF a THEN            -- 3
#     x := 1;            -- 4
#   ELSIF b THEN         -- 5
#     x := 2;            -- 6
#   ELSE                 -- 7
#     x := 3;            -- 8
#   END IF;
# END;
_SQL_SOURCE = "\n".join([
    "PROCEDURE p IS",     # 1
    "BEGIN",              # 2
    "  IF a THEN",        # 3
    "    x := 1;",        # 4
    "  ELSIF b THEN",     # 5
    "    x := 2;",        # 6
    "  ELSE",             # 7
    "    x := 3;",        # 8
    "  END IF;",          # 9
    "END;",               # 10
])

_SQL_TREE = [ControlSubtreeNode(
    node_id="if@3", ntype="IF", start=3, end=9, expression="a",
    children=[
        ControlSubtreeNode(node_id="elsif@5", ntype="ELSIF", start=5, end=6, expression="b"),
        ControlSubtreeNode(node_id="else@7", ntype="ELSE", start=7, end=8),
    ],
)]

_SQL_STATEMENTS = (
    StatementFact("s4", "ASSIGNMENT", 4, 4, expression="1", target="x", operator=":="),
    StatementFact("s6", "ASSIGNMENT", 6, 6, expression="2", target="x", operator=":="),
    StatementFact("s8", "ASSIGNMENT", 8, 8, expression="3", target="x", operator=":="),
)


def _evidence(source, tree, lang, statements):
    inventory = build_inventory(
        source, tree, lang, statements=tuple(statements),
    )
    rows = required_rule_rows(inventory)
    return inventory, rule_evidence(inventory, tuple(rows))


class FlatElsifChainTest(unittest.TestCase):
    """S7·S8 — ELSIF 는 조건 있는 decision 이고, 사슬의 앞 판정은 전부 부정이다."""

    def test_s7_elsif_is_decision_negating_the_owning_if(self):
        _, payload = _evidence(_SQL_SOURCE, _SQL_TREE, "sql", _SQL_STATEMENTS)
        self.assertEqual("decision", payload["rows"]["R5"]["role"])
        self.assertEqual(["!C3", "C5"], payload["rows"]["R5"]["when"])

    def test_s8_else_negates_the_if_and_every_preceding_elsif(self):
        _, payload = _evidence(_SQL_SOURCE, _SQL_TREE, "sql", _SQL_STATEMENTS)
        self.assertEqual("complement", payload["rows"]["R7"]["role"])
        self.assertEqual({"!C3", "!C5"}, set(payload["rows"]["R7"]["when"]))

    def test_s8b_nested_row_inherits_the_negated_chain(self):
        """ELSIF 본문 안의 판정도 사슬 부정을 물려받는다."""
        tree = [ControlSubtreeNode(
            node_id="if@3", ntype="IF", start=3, end=9, expression="a",
            children=[
                ControlSubtreeNode(
                    node_id="elsif@5", ntype="ELSIF", start=5, end=6, expression="b",
                    children=[ControlSubtreeNode(
                        node_id="if@6", ntype="IF", start=6, end=6, expression="c")],
                ),
            ],
        )]
        _, payload = _evidence(_SQL_SOURCE, tree, "sql", _SQL_STATEMENTS)
        self.assertEqual({"!C3", "C5", "C6"}, set(payload["rows"]["R6"]["when"]))


class NestedElseIfChainUnchangedTest(unittest.TestCase):
    """S9 — C 의 중첩 `else { if … }` 형상은 조상 사슬이 이미 담당한다(회귀 없음)."""

    def test_s9_c_nested_else_if_chain_keeps_existing_path(self):
        source = "\n".join([
            "long f(long a, long b) {",   # 1
            "  if (a > 0) {",             # 2
            "    x = 1;",                 # 3
            "  } else {",                 # 4
            "    if (b > 0) {",           # 5
            "      x = 2;",               # 6
            "    }",                      # 7
            "  }",                        # 8
            "  return x;",                # 9
            "}",                          # 10
        ])
        tree = [ControlSubtreeNode(
            node_id="if@2", ntype="IF", start=2, end=8, expression="a > 0",
            children=[ControlSubtreeNode(
                node_id="else@4", ntype="ELSE", start=4, end=8,
                children=[ControlSubtreeNode(
                    node_id="if@5", ntype="IF", start=5, end=7, expression="b > 0")],
            )],
        )]
        statements = (
            StatementFact("s3", "ASSIGNMENT", 3, 3, expression="1", target="x", operator="="),
            StatementFact("s6", "ASSIGNMENT", 6, 6, expression="2", target="x", operator="="),
            StatementFact("s9", "RETURN", 9, 9, expression="x"),
        )
        _, payload = _evidence(source, tree, "c", statements)
        self.assertEqual({"!C2", "C5"}, set(payload["rows"]["R5"]["when"]))
        self.assertEqual({"!C2"}, set(payload["rows"]["R4"]["when"]))


class ExceptionHandlerRowTest(unittest.TestCase):
    """S6 — PL/SQL EXCEPTION 섹션은 오류 경로 진입 **의무 행**(handler)이다."""

    def test_s6_exception_section_is_a_required_handler_row(self):
        source = "\n".join([
            "PROCEDURE p IS",                    # 1
            "BEGIN",                             # 2
            "  IF a THEN",                       # 3
            "    BEGIN",                         # 4
            "      INSERT INTO t VALUES (1);",   # 5
            "    EXCEPTION",                     # 6
            "      WHEN OTHERS THEN",            # 7
            "        y := 9;",                   # 8
            "    END;",                          # 9
            "  END IF;",                         # 10
            "END;",                              # 11
        ])
        tree = [ControlSubtreeNode(
            node_id="if@3", ntype="IF", start=3, end=10, expression="a",
            children=[
                ControlSubtreeNode(node_id="try@4", ntype="TRY", start=4, end=9),
                ControlSubtreeNode(node_id="exc@6", ntype="EXCEPTION", start=6, end=8),
            ],
        )]
        statements = (
            StatementFact("s8", "ASSIGNMENT", 8, 8, expression="9", target="y", operator=":="),
        )
        inventory, payload = _evidence(source, tree, "sql", statements)
        self.assertIn(6, required_rule_rows(inventory))
        self.assertEqual("handler", payload["rows"]["R6"]["role"])
        # 바깥 IF 안에 든 핸들러이므로 도달 조건에 그 판정이 남아야 한다.
        self.assertEqual(["C3"], payload["rows"]["R6"]["when"])


if __name__ == "__main__":
    unittest.main()
