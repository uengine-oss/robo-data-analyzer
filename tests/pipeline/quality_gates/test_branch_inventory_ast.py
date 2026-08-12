"""branch_inventory 의 parser AST statement 계약(spec 016) 소비 검증.

statements 가 주어지면 종료행·반환식·대입·entry·직접호출을 AST 정본에서 만들고
source 첫 토큰/정규식 재탐색을 쓰지 않는다. legacy 경로가 구조적으로 놓치던
multiline return 이 AST 경로에서 보존되는 반증을 포함한다.
"""
import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import (
    expected_rule_condition,
    rule_evidence,
)
from pipeline.steps.step7_analyze_code.rule_row_batching import required_rule_rows

_SOURCE = "\n".join([
    "long f(long x) {",          # 1
    "  long rc;",                # 2
    "  rc = init();",            # 3
    "  if (x > 0) {",            # 4
    "    rc = x * 2;",           # 5
    "    return rc",             # 6 (multiline return 6~7)
    "        + 1;",              # 7
    "  }",                       # 8
    "  PFM_CATCH();",            # 9
    "  return 0;",               # 10
    "}",                         # 11
])

_SUBTREE = [ControlSubtreeNode(node_id="if@4", ntype="IF", start=4, end=8, expression="x > 0")]

_STATEMENTS = (
    StatementFact("s3", "ASSIGNMENT", 3, 3, expression="init()", target="rc", operator="="),
    StatementFact("s5", "ASSIGNMENT", 5, 5, expression="x * 2", target="rc", operator="="),
    StatementFact("s6", "RETURN", 6, 7, expression="rc\n        + 1"),
    StatementFact("s10", "RETURN", 10, 10, expression="0"),
)

_CALL_LINES = ((3, "init"), (9, "PFM_CATCH"))


class BranchInventoryAstContractTest(unittest.TestCase):

    def _build(self):
        return build_inventory(
            _SOURCE, _SUBTREE, "c",
            handler_markers=("PFM_CATCH",),
            statements=_STATEMENTS,
            call_lines=_CALL_LINES,
        )

    def test_termination_rows_come_from_ast_not_source_tokens(self):
        inventory = self._build()
        rows = {line: (kind, text) for line, kind, text in inventory.termination_rows}
        self.assertEqual({6, 9, 10}, set(rows))
        self.assertEqual("exit", rows[6][0])
        self.assertEqual("handler", rows[9][0])
        self.assertEqual("exit", rows[10][0])

    def test_multiline_return_expression_preserved(self):
        inventory = self._build()
        # legacy 한 줄 스캐너는 6~7행 return 을 잃었다 — AST 계약의 핵심 수리 지점.
        self.assertIn("rc + 1", inventory.return_expressions)
        self.assertIn("0", inventory.return_expressions)

    def test_branch_direct_facts_from_ast(self):
        inventory = self._build()
        branch = inventory.at_line(4)
        self.assertEqual((("rc", "x * 2"),), branch.assignment_pairs)
        self.assertEqual(frozenset({"rc + 1"}), branch.return_expressions)
        self.assertEqual(frozenset(), branch.callees)

    def test_prompt_index_exposes_implicit_false_as_normal_flow(self):
        row = next(
            line for line in self._build().branch_index().splitlines()
            if line.startswith("4:")
        )
        self.assertIn("[implicit_false]", row)
        self.assertIn("handler 진입 아님", row)

    def test_explicit_else_suppresses_implicit_false_marker(self):
        source = "\n".join((
            "int f(int x) {", "if (x) {", "work();", "} else {",
            "fallback();", "}", "}",
        ))
        tree = [ControlSubtreeNode(
            "if@2", "IF", 2, 6, expression="x",
            children=[ControlSubtreeNode("else@4", "ELSE", 4, 6)],
        )]
        index = build_inventory(
            source, tree, "c", statements=(),
            call_lines=((3, "work"), (5, "fallback")),
        ).branch_index()
        decision = next(line for line in index.splitlines() if "[decision]" in line)
        self.assertNotIn("[implicit_false]", decision)

    def test_prompt_index_marks_handler_as_error_only(self):
        source = "\n".join((
            "BEGIN", "  work;", "EXCEPTION", "  WHEN OTHERS THEN", "  log;", "END;",
        ))
        tree = [
            ControlSubtreeNode("try@1", "TRY", 1, 6),
            ControlSubtreeNode("exception@3", "EXCEPTION", 3, 6),
        ]
        index = build_inventory(source, tree, "sql", statements=()).branch_index()
        handler = next(line for line in index.splitlines() if "[handler]" in line)
        self.assertIn("[error_only]", handler)
        self.assertIn("조건 거짓으로 진입하지 않음", handler)

    def test_entry_owns_top_level_assignment_and_call(self):
        inventory = self._build()
        self.assertEqual(3, inventory.entry_line)
        source, returns, assignments = inventory.direct_evidence(3)
        self.assertEqual((("rc", "init()"),), assignments)
        self.assertEqual(frozenset(), returns)  # 최상위 return 은 [exit] 행 소유
        self.assertEqual(frozenset({"init"}), inventory.direct_callees(3))

    def test_exit_row_direct_evidence_from_ast(self):
        inventory = self._build()
        _source, returns, assignments = inventory.direct_evidence(10)
        self.assertEqual(frozenset({"0"}), returns)
        self.assertEqual((), assignments)

    def test_effects_after_controls_get_separate_coordinate_owned_rows(self):
        source = "\n".join([
            "int f(int flag, int deny) {",  # 1
            "  result = 0;",                # 2 entry prefix
            "  if (flag) {",                # 3 parent
            "    if (deny) return -1;",     # 4 terminating child
            "    result = 1;",              # 5 parent continuation
            "  }",                          # 6
            "  audit();",                   # 7 routine continuation
            "  return 0;",                  # 8 top-level exit
            "}",                             # 9
        ])
        tree = [ControlSubtreeNode(
            "if@3", "IF", 3, 6, expression="flag",
            children=[ControlSubtreeNode(
                "if@4", "IF", 4, 4, expression="deny",
            )],
        )]
        inventory = build_inventory(
            source, tree, "c",
            statements=(
                StatementFact("a2", "ASSIGNMENT", 2, 2,
                              expression="0", target="result", operator="="),
                StatementFact("r4", "RETURN", 4, 4, expression="-1"),
                StatementFact("a5", "ASSIGNMENT", 5, 5,
                              expression="1", target="result", operator="="),
                StatementFact("r8", "RETURN", 8, 8, expression="0"),
            ),
            call_lines=((7, "audit"),),
        )

        self.assertEqual(2, inventory.entry_line)
        self.assertEqual((('result', '0'),), inventory.direct_evidence(2)[2])
        self.assertNotIn("audit", inventory.direct_callees(2))
        rows = {row["line"]: row for row in inventory.table_rows()}
        self.assertEqual("continuation", rows[5]["role"])
        self.assertEqual("", rows[5]["after_control"])
        self.assertEqual(4, rows[5]["fallthrough_false_of"])
        self.assertEqual("continuation", rows[7]["role"])
        self.assertEqual("IF@3", rows[7]["after_control"])
        self.assertEqual((('result', '1'),), inventory.direct_evidence(5)[2])
        self.assertEqual(frozenset({"audit"}), inventory.direct_callees(7))
        self.assertEqual(
            'F("flag") and not (F("deny"))',
            expected_rule_condition(inventory, 5),
        )
        self.assertEqual(
            'after_control("IF@3")', expected_rule_condition(inventory, 7),
        )
        self.assertTrue({2, 3, 4, 5, 7, 8}.issubset(required_rule_rows(inventory)))
        evidence = rule_evidence(inventory, (5, 7))
        self.assertEqual("", evidence["rows"]["R5"]["after_control"])
        self.assertEqual("IF@3", evidence["rows"]["R7"]["after_control"])
        self.assertEqual([], evidence["rows"]["R5"]["narrative_focus"])
        self.assertEqual([], evidence["rows"]["R7"]["narrative_focus"])

    def test_static_goto_target_splits_sequence_at_parser_label(self):
        source = "\n".join([
            "state = 0;",                 # 1 entry
            "if (skip) { goto done; }",   # 2 decision + jump
            "state = 1;",                 # 3 normal fall-through only
            "done:",                      # 4 static join coordinate
            "audit();",                   # 5 normal or goto predecessor
        ])
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                "if@2", "IF", 2, 2, expression="skip",
            )],
            "c",
            statements=(
                StatementFact("a1", "ASSIGNMENT", 1, 1,
                              expression="0", target="state", operator="="),
                StatementFact("g2", "GOTO", 2, 2, target="done"),
                StatementFact("a3", "ASSIGNMENT", 3, 3,
                              expression="1", target="state", operator="="),
                StatementFact("l4", "LABEL", 4, 4, target="done"),
            ),
            call_lines=((5, "audit"),),
        )

        rows = {row["line"]: row for row in inventory.table_rows()}
        self.assertEqual("", rows[3]["after_control"])
        self.assertEqual(2, rows[3]["fallthrough_false_of"])
        self.assertEqual(
            'not (F("skip"))', expected_rule_condition(inventory, 3),
        )
        self.assertEqual("", rows[3]["at_label"])
        self.assertEqual("done@4", rows[4]["at_label"])
        self.assertEqual("", rows[4]["after_control"])
        self.assertFalse(rows[4]["unresolved_reachability"])
        self.assertEqual((('state', '1'),), inventory.direct_evidence(3)[2])
        self.assertEqual(frozenset({"audit"}), inventory.direct_callees(4))
        self.assertEqual(
            'at_label("done@4")', expected_rule_condition(inventory, 4),
        )
        evidence = rule_evidence(inventory, (3, 4))
        self.assertEqual("done@4", evidence["rows"]["R4"]["at_label"])
        self.assertNotIn("audit", evidence["rows"]["R3"].get("source", ""))

    def test_unconditional_continue_makes_following_path_the_guard_complement(self):
        source = "\n".join([
            "while (again) {",            # 1
            "  if (skip) { continue; }",  # 2
            "  work();",                  # 3
            "}",                           # 4
        ])
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                "loop@1", "LOOP", 1, 4, expression="again",
                children=[ControlSubtreeNode(
                    "if@2", "IF", 2, 2, expression="skip",
                )],
            )],
            "c",
            statements=(
                StatementFact("c2", "CONTINUE", 2, 2, target="continue"),
            ),
            call_lines=((3, "work"),),
        )

        rows = {row["line"]: row for row in inventory.table_rows()}
        self.assertEqual(2, rows[3]["fallthrough_false_of"])
        self.assertEqual("", rows[3]["after_control"])
        self.assertFalse(rows[3]["unresolved_reachability"])
        self.assertEqual(
            'F("again") and not (F("skip"))',
            expected_rule_condition(inventory, 3),
        )
        evidence = rule_evidence(inventory, (2, 3))
        self.assertEqual(["C2"], evidence["rows"]["R2"]["narrative_focus"])
        self.assertEqual([], evidence["rows"]["R3"]["narrative_focus"])
        jump = [
            item for item in evidence["effects"].values()
            if item.get("op") == "jump"
        ]
        self.assertEqual(["continue"], [item["target"] for item in jump])

    def test_compound_assignment_preserves_read_modify_write_meaning(self):
        inventory = build_inventory(
            "disc += 2000;\nflags |= MASK;\n",
            [],
            "c",
            statements=(
                StatementFact(
                    "a1", "ASSIGNMENT", 1, 1,
                    expression="2000", target="disc", operator="+=",
                ),
                StatementFact(
                    "a2", "ASSIGNMENT", 2, 2,
                    expression="MASK", target="flags", operator="|=",
                ),
            ),
            call_lines=(),
        )
        _source, _returns, assignments = inventory.direct_evidence(1)
        self.assertEqual(
            (("disc", "(disc) + (2000)"), ("flags", "(flags) | (MASK)")),
            assignments,
        )

    def test_java_python_sql_ast_paths(self):
        # 04 §3.3.1-5: 지원 언어별 fixture 없이 legacy 를 삭제하지 않는다.
        java = build_inventory(
            "int f(int x) {\n  if (x > 0) {\n    return x + 1;\n  }\n"
            "  throw new IllegalStateException(x);\n}\n",
            [ControlSubtreeNode("if@2", "IF", 2, 4, expression="x > 0")],
            "java",
            statements=(
                StatementFact("s3", "RETURN", 3, 3, expression="x + 1"),
                StatementFact("s5", "THROW", 5, 5,
                              expression="new IllegalStateException(x)"),
            ),
            call_lines=(),
        )
        self.assertEqual({3, 5}, {l for l, k, _ in java.termination_rows if k == "exit"})
        self.assertEqual(
            frozenset({"x + 1"}), java.at_line(2).return_expressions,
            "분기 안 return 귀속 (java)")
        self.assertNotIn("new IllegalStateException(x)", java.return_expressions)
        self.assertEqual((("raise_error", ""),), java.direct_statement_effects(5))

        python = build_inventory(
            "def f(x):\n    if x > 0:\n        return x + 1\n    raise ValueError(x)\n",
            [ControlSubtreeNode("if@2", "IF", 2, 3, expression="x > 0")],
            "python",
            statements=(
                StatementFact("s3", "RETURN", 3, 3, expression="x + 1"),
                StatementFact("s4", "RAISE", 4, 4, expression="ValueError(x)"),
            ),
            call_lines=(),
        )
        self.assertEqual({3, 4}, {l for l, k, _ in python.termination_rows if k == "exit"})
        self.assertEqual(frozenset({"x + 1"}), python.at_line(2).return_expressions)
        self.assertNotIn("ValueError(x)", python.return_expressions)
        self.assertEqual((("raise_error", ""),), python.direct_statement_effects(4))

        sql = build_inventory(
            "BEGIN\n  IF p_id > 0 THEN\n    v_st := 'OK';\n    RETURN 'OK';\n"
            "  END IF;\n  RETURN 'NG';\nEND;\n",
            [ControlSubtreeNode("if@2", "IF", 2, 5, expression="p_id > 0")],
            "sql",
            statements=(
                StatementFact("s3", "ASSIGNMENT", 3, 3,
                              expression="'OK'", target="v_st", operator=":="),
                StatementFact("s4", "RETURN", 4, 4, expression="'OK'"),
                StatementFact("s6", "RETURN", 6, 6, expression="'NG'"),
            ),
            call_lines=(),
        )
        self.assertEqual({4, 6}, {l for l, k, _ in sql.termination_rows if k == "exit"})
        branch = sql.at_line(2)
        self.assertEqual((("v_st", "'OK'"),), branch.assignment_pairs)
        self.assertEqual(frozenset({"'OK'"}), branch.return_expressions)
        self.assertIn("'NG'", sql.return_expressions)

    def test_statements_are_required_no_source_fallback(self):
        # legacy source 재탐색 경로는 삭제됐다 (spec 016 G단계) — statements 미제공은
        # 조용한 폴백이 아니라 즉시 실패다 (No Silent Failure).
        with self.assertRaises(TypeError):
            build_inventory(_SOURCE, _SUBTREE, "c")


class FinallyWithConsumptionTest(unittest.TestCase):
    """FW 타언어 내부 노드의 analyzer 소비 준비 (사용자 지시 2026-08-05 — 실기동 검증은 C만).

    - java FINALLY = "도달하면 항상 실행" — 조건 항 없이 조상 경로만 갖는 행(회귀 5호
      계약, 창작 0). **2026-08-08(spec 118 §2.1) 정정**: 이 행의 role 은 종전 ``decision``
      이었는데, 채점기(`rule_grading.py` decision 행은 cond 필수)와 정면으로 모순이었다 —
      Java 코퍼스가 들어오면 모든 FINALLY 가 finding 이 된다. 조건이 없다는 사실은
      그대로 두고 role 만 ``controller`` 로 내린다. **증거(`when: []`)는 불변**이며
      달라지는 것은 없는 조건을 요구하지 않는다는 점뿐이다.
    - python WITH = controller: TRY 와 같은 감싸는 구조 — 자원 식은 SWITCH 스위치식처럼
      문맥으로만 실리고 조건 경로를 오염하지 않는다.
    """

    def test_java_finally_is_conditionless_row_without_condition_demand(self):
        from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
        source = "\n".join([
            "void f() {",                 # 1
            "  try {",                    # 2
            "    a();",                   # 3
            "  }",                        # 4
            "  catch (Exception e) {",    # 5
            "    r = 1;",                 # 6
            "  }",                        # 7
            "  finally {",                # 8
            "    close();",               # 9
            "  }",                        # 10
            "}",                          # 11
        ])
        tree = [ControlSubtreeNode(
            node_id="try@2", ntype="TRY", start=2, end=10,
            children=[
                ControlSubtreeNode(node_id="catch@5", ntype="CATCH", start=5, end=7,
                                   expression="Exception e"),
                ControlSubtreeNode(node_id="finally@8", ntype="FINALLY", start=8, end=10),
            ],
        )]
        inventory = build_inventory(
            source, tree, "java",
            statements=(StatementFact("a6", "ASSIGNMENT", 6, 6,
                                      expression="1", target="r", operator="="),),
            call_lines=((3, "a"), (9, "close")),
        )
        rows = {r["line"]: r for r in inventory.table_rows()}
        self.assertEqual("controller", rows[8]["role"])
        self.assertEqual("", rows[8]["condition"])
        payload = rule_evidence(inventory, (8,))
        self.assertEqual([], payload["rows"]["R8"]["when"])
        self.assertEqual([], payload["rows"]["R8"]["narrative_focus"])

    def test_python_with_is_controller_row(self):
        source = "\n".join([
            "def f():",                        # 1
            "    with open(p) as fh:",         # 2
            "        data = fh.read()",        # 3
            "    return data",                 # 4
        ])
        tree = [ControlSubtreeNode(
            node_id="with@2", ntype="WITH", start=2, end=3,
            expression="open(p) as fh",
        )]
        inventory = build_inventory(
            source, tree, "python",
            statements=(
                StatementFact("a3", "ASSIGNMENT", 3, 3,
                              expression="fh.read()", target="data", operator="="),
                StatementFact("r4", "RETURN", 4, 4, expression="data"),
            ),
            call_lines=((2, "open"), (3, "fh.read")),
        )
        rows = {r["line"]: r for r in inventory.table_rows()}
        self.assertEqual("controller", rows[2]["role"])


_INACTIVE_SOURCE = "\n".join([
    "long f(long x) {",      # 1
    "  long rc;",            # 2
    "  rc = init();",        # 3
    "  if (x > 0) {",        # 4
    "    return 1;",         # 5
    "  }",                   # 6
    "#if 0",                 # 7
    "  if (x < 0) {",        # 8
    "    rc = cleanup();",   # 9
    "    return -1;",        # 10
    "  }",                   # 11
    "#endif",                # 12
    "  return 0;",           # 13
    "}",                     # 14
])

_INACTIVE_SUBTREE = [
    ControlSubtreeNode(node_id="if@4", ntype="IF", start=4, end=6, expression="x > 0"),
    # parser 는 전처리를 평가하지 않으므로 #if 0 블록의 제어 노드도 emit 한다.
    ControlSubtreeNode(node_id="if@8", ntype="IF", start=8, end=11, expression="x < 0"),
]

_INACTIVE_STATEMENTS = (
    StatementFact("s3", "ASSIGNMENT", 3, 3, expression="init()", target="rc", operator="="),
    StatementFact("s5", "RETURN", 5, 5, expression="1"),
    StatementFact("s9", "ASSIGNMENT", 9, 9, expression="cleanup()", target="rc", operator="="),
    StatementFact("s10", "RETURN", 10, 10, expression="-1"),
    StatementFact("s13", "RETURN", 13, 13, expression="0"),
)

_INACTIVE_CALLS = ((3, "init"), (9, "cleanup"))


class LabelHandlerRowTest(unittest.TestCase):
    """라벨형 handler 마커(`PFM_CATCH:`)도 [handler] 행이다.

    framework 실측(2026-08-05, K run): 이 corpus 는 `goto PFM_CATCH;` + `PFM_CATCH:`
    라벨 스타일이라 FUNCTION_CALL 좌표만 보는 신 경로에서 handler 행 67개가
    사라졌다(기준선 대비 결정론 분해로 확정). 마커는 skill 이 선언한 프레임워크
    사실이므로 호출형·라벨형 두 표기를 같은 사실로 인정한다.
    """

    SOURCE = "\n".join([
        "long f(void) {",     # 1
        "  PFM_TRY(a());",    # 2
        "  return 0;",        # 3
        "PFM_CATCH:",         # 4
        "  return 1;",        # 5
        "}",                  # 6
    ])
    STATEMENTS = (
        StatementFact("r3", "RETURN", 3, 3, expression="0"),
        StatementFact("r5", "RETURN", 5, 5, expression="1"),
    )

    def test_label_form_marker_creates_handler_row(self):
        inventory = build_inventory(
            self.SOURCE, [], "c", handler_markers=("PFM_CATCH",),
            statements=self.STATEMENTS, call_lines=((2, "a"),),
        )
        rows = {line: kind for line, kind, _ in inventory.termination_rows}
        self.assertEqual({3: "exit", 4: "handler", 5: "exit"}, rows)

    def test_inactive_label_is_not_a_handler_row(self):
        source = self.SOURCE.replace("PFM_CATCH:", "#if 0\nPFM_CATCH:\n#endif")
        inventory = build_inventory(
            source, [], "c", handler_markers=("PFM_CATCH",),
            statements=(
                StatementFact("r3", "RETURN", 3, 3, expression="0"),
                StatementFact("r7", "RETURN", 7, 7, expression="1"),
            ),
            call_lines=((2, "a"),),
        )
        self.assertEqual(
            [], [line for line, kind, _ in inventory.termination_rows if kind == "handler"])


class InactiveRegionStatementFactTest(unittest.TestCase):
    """전처리 비활성(#if 0) 구간의 AST 사실은 실행 의무가 아니다.

    분기는 convert 가 live 판정으로 이미 거른다(framework 실측: zapamcom10110.c
    1282~1353 `#if 0` 블록). statement/call 사실도 같은 live 경계를 공유해야
    죽은 return 이 [exit] 행·반환식·직접호출로 승격되지 않는다.
    """

    def _build(self):
        return build_inventory(
            _INACTIVE_SOURCE, _INACTIVE_SUBTREE, "c",
            statements=_INACTIVE_STATEMENTS,
            call_lines=_INACTIVE_CALLS,
        )

    def test_inactive_exit_rows_are_not_obligations(self):
        inventory = self._build()
        self.assertEqual(
            {5, 13}, {l for l, k, _ in inventory.termination_rows if k == "exit"})

    def test_inactive_returns_and_calls_are_masked(self):
        inventory = self._build()
        self.assertEqual(frozenset({"1", "0"}), inventory.return_expressions)
        self.assertEqual(frozenset({"init"}), inventory.callees)

    def test_inactive_branch_still_dropped(self):
        inventory = self._build()
        self.assertIsNotNone(inventory.at_line(4))
        self.assertIsNone(inventory.at_line(8))


if __name__ == "__main__":
    unittest.main()
