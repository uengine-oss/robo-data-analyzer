"""분기표(정답지)·lexing 결정론 계약 — 조건값 축·행 선택·종료문·수치 병기.

폐기된 근거성 게이트(grounding)는 행 채점(rule_grading)으로 대체돼 삭제됐다 —
여기 남은 것은 분기표와 lexing 이 소유한 계약뿐이다.

spec 016 전환: 조건·종료문·대입·호출의 생산자는 parser AST 다. fixture 는
``ControlSubtreeNode.expression``·``StatementFact``·``call_lines`` 로 그 계약을
그대로 제공하며, analyzer 는 source 를 재탐색하지 않는다.
"""
from __future__ import annotations

import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.lexing import literal_values, split_source


class LexingContract(unittest.TestCase):
    def test_comment_split_preserves_line_alignment(self):
        split = split_source("a = 1;\n// b = 2;\nc = 3;", "c")
        self.assertEqual(len(split.live_lines), 3)
        self.assertNotIn("b", split.live(2, 2))
        self.assertIn("b", split.dead(2, 2))

    def test_string_literal_is_not_mistaken_for_a_comment(self):
        split = split_source('url = "http://x";', "c")
        self.assertIn("http://x", split.live())

    def test_inactive_block_is_dead_by_language_table(self):
        split = split_source("#if 0\ndead_call();\n#endif\nlive_call();", "c")
        self.assertNotIn("dead_call", split.live())
        self.assertIn("dead_call", split.dead())
        self.assertIn("live_call", split.live())

    def test_literal_values_exclude_identifiers(self):
        self.assertEqual(literal_values('x == "01"', "c"), {"01"})

    def test_digits_inside_identifiers_are_not_code_values(self):
        """이름 안의 숫자를 코드값으로 세면 근거 대조축이 통째로 오염된다.

        실측: `LEN_APA_01` 의 `01`, `zngmmcap20020_io` 의 `20020` 이 분기 조건값으로
        잡혀 "규칙이 이 값을 안 담았다"는 거짓 누락을 대량 생산했다.
        """
        condition = 'strncmp(INPUT->pay_mthd_cd, "01", LEN_APASCRTU0010T02_IN_PAY_MTHD_CD)'
        self.assertEqual(literal_values(condition, "c"), {"01"})
        self.assertEqual(literal_values("zngmmcap20020_io.out.result", "c"), set())
        self.assertEqual(literal_values("curcnt != AS_ZNGM_COMM_CD_DTL_F1075", "c"), set())

    def test_standalone_numbers_and_radix_prefix_survive(self):
        self.assertEqual(literal_values("case 10:", "c"), {"10"})
        self.assertEqual(literal_values("x <= 010 )", "c"), {"010"})
        self.assertEqual(literal_values("flag == 0x1F", "c"), {"0x1F"})


class ConditionValueAxis(unittest.TestCase):
    """조건 코드값 = 그 분기가 **판정하는 업무 값**. 반복 제어·이름은 값이 아니다."""

    @staticmethod
    def _values(body: str, ntype: str, expression: str, lang: str = "c") -> set[str]:
        node = ControlSubtreeNode(
            node_id="n", ntype=ntype, start=1, end=len(body.splitlines()),
            children=[], expression=expression)
        inventory = build_inventory(body, [node], lang, statements=())
        return set(inventory.all_branches[0].cond_values)

    def test_counted_loop_header_keeps_only_the_test_clause(self):
        """parser LOOP expression 은 판정절만이다 — `for` 의 0 초기값은 오지 않는다."""
        self.assertEqual(
            self._values("for (li = 0; li <= 10; li++)\n{\n}", "LOOP", "li <= 10"),
            {"10"},
        )

    def test_language_without_counted_loop_syntax_keeps_condition(self):
        self.assertEqual(
            self._values('while status != "DONE"\n    pass', "LOOP",
                         'status != "DONE"', "python"),
            {"done"},
        )

    def test_quoted_symbol_name_is_not_a_code_value(self):
        """호출 대상·함수 이름 문자열은 도메인 코드값이 아니다 — 본문 식별자로 판정한다.

        남는 `0` 은 `strncmp(...) == 0` 의 비교 상수다(값 축의 잔여 잡음).
        """
        body = ('if (strncmp("ordf_chk_iden_numb", itf->in.id, LEN) == 0)\n'
                '{\n    ordf_chk_iden_numb(itf);\n}')
        expression = 'strncmp("ordf_chk_iden_numb", itf->in.id, LEN) == 0'
        self.assertEqual(self._values(body, "IF", expression), {"0"})

    def test_domain_literal_survives_next_to_a_symbol_name(self):
        body = 'if (strncmp(itf->in.chg_rsn_cd, "08", LEN) == 0)\n{\n    run();\n}'
        expression = 'strncmp(itf->in.chg_rsn_cd, "08", LEN) == 0'
        self.assertIn("08", self._values(body, "IF", expression))

    def test_declared_constants_outside_the_body_count_as_source(self):
        """본문 밖 `#define` 상수는 프롬프트가 증거 2순위로 주는 값이다 — 앵커에 포함한다."""
        body = 'if (rc != OK)\n{\n    put(DOCU_RCV);\n}'
        inv = build_inventory(
            body, [], "c", statements=(), declared_values=['"ZAPAE0021"'],
        )
        # 배선 계약(2026-08-03 실측 갭 수리): 생산 경로 build_control_inventory 가
        # ctx.referenced_symbols 의 immutable_constant 선언값을 실제로 전달하는지는
        # tests/pipeline/step7_analyze_code/test_control_prompt_declared_values.py 가 고정.
        self.assertIn("zapae0021", inv.source_values)
        plain = build_inventory(body, [], "c", statements=())
        self.assertNotIn("zapae0021", plain.source_values)


class ConditionConsumption(unittest.TestCase):
    """조건식은 parser 가 소유한다(spec 016) — 여러 줄 조건도 expression 하나로 온다."""

    def test_multiline_condition_expression_keeps_every_literal(self):
        body = 'if (eq(a,"11") ||\n    eq(a,"12") ||\n    eq(a,"13"))\n{\n    run();\n}'
        expression = 'eq(a,"11") ||\n    eq(a,"12") ||\n    eq(a,"13")'
        node = ControlSubtreeNode("if", "IF", 1, 6, expression=expression)
        inventory = build_inventory(body, [node], "c", statements=())
        self.assertEqual(
            inventory.all_branches[0].cond_values, frozenset({"11", "12", "13"}),
        )

    def test_case_label_expression_is_the_condition(self):
        body = "case 21:\n    run();"
        node = ControlSubtreeNode("case", "CASE", 1, 2, expression="21")
        inventory = build_inventory(body, [node], "c", statements=())
        self.assertEqual(inventory.all_branches[0].cond_values, frozenset({"21"}))
        sql_body = "WHEN '21' THEN\n    run();"
        sql_node = ControlSubtreeNode("when", "CASE", 1, 2, expression="'21'")
        sql_inventory = build_inventory(sql_body, [sql_node], "sql", statements=())
        self.assertEqual(sql_inventory.all_branches[0].cond_values, frozenset({"21"}))


class LanguageIndependence(unittest.TestCase):
    """게이트 로직에 언어 분기가 없다 — 같은 코드가 표(데이터)만 바꿔 java/python/sql 을 판정한다.

    C 코퍼스로만 실측했으므로 **적중률은 다른 언어에서 미검증**이다. 여기서 단언하는 것은
    "판정 경로가 언어에 걸려 죽지 않는다"까지다.
    """

    CASES = (
        ("java", 'if (code.equals("11") || code.equals("14")) {\n    apply();\n}',
         'code.equals("11") || code.equals("14")'),
        ("python", 'if code == "11" or code == "14":\n    apply()',
         'code == "11" or code == "14"'),
        ("sql", "IF v_code = '11' OR v_code = '14' THEN\n    apply();\nEND IF;",
         "v_code = '11' OR v_code = '14'"),
    )

    def test_condition_literals_are_read_in_every_language(self):
        for lang, body, expression in self.CASES:
            with self.subTest(lang=lang):
                tree = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=3,
                                           expression=expression)]
                inventory = build_inventory(body, tree, lang, statements=())
                self.assertEqual(inventory.all_branches[0].cond_values, frozenset({"11", "14"}))

    def test_python_docstring_is_not_live_code(self):
        """독스트링은 문법상 문자열이지만 실행 코드가 아니다.

        활성으로 두면 그 안의 산문·예시 SQL 이 "코드에 있는 값"으로 둔갑해 근거성이 무너진다.
        실측 사례: library_system/src/core/database.py 의 CREATE TABLE 독스트링.
        """
        body = '\n'.join([
            'def make():',
            '    """설정 파서',
            '    CREATE TABLE books (isbn TEXT PRIMARY KEY)',
            '    """',
            '    return "0001"',
        ])
        live = split_source(body, "python").live()
        self.assertNotIn("CREATE TABLE", live)
        self.assertNotIn("isbn", live)
        self.assertIn('"0001"', live)          # 진짜 코드값은 남는다
        self.assertNotIn("0001", split_source(body, "python").dead())

    def test_line_comment_token_comes_from_the_table(self):
        self.assertNotIn("dead", split_source("live();\n// dead();", "java").live())
        self.assertNotIn("dead", split_source("live()\n# dead()", "python").live())
        self.assertNotIn("dead", split_source("live();\n-- dead();", "sql").live())

    def test_unregistered_language_fails_loudly_instead_of_passing_silently(self):
        from pipeline.languages import UnknownSourceLexiconError

        with self.assertRaises(UnknownSourceLexiconError):
            split_source("x = 1", "unregistered_test_language")

    def test_complement_ownership_is_language_data_not_a_global_loop_heuristic(self):
        from pipeline.languages import complement_owner_types

        self.assertEqual(frozenset({"IF"}), complement_owner_types("c"))
        self.assertEqual(frozenset({"IF", "LOOP"}), complement_owner_types("python"))


class BranchIndexUniqueness(unittest.TestCase):
    """분기 목록의 줄번호는 유일해야 한다 — 앵커가 확정되지 않으면 무의미하다."""

    BODY = ('if (a == "1")\n{\n    x();\n}\nelse if (a == "2")\n{\n    y();\n}\n')
    # else if → ELSE 와 IF 가 같은 줄(5)에서 시작한다
    TREE = [
        ControlSubtreeNode(node_id="if1", ntype="IF", start=1, end=4,
                           expression='a == "1"'),
        ControlSubtreeNode(node_id="el", ntype="ELSE", start=5, end=8, children=[
            ControlSubtreeNode(node_id="if2", ntype="IF", start=5, end=8,
                               expression='a == "2"'),
        ]),
    ]

    def setUp(self):
        self.inv = build_inventory(self.BODY, self.TREE, "c", statements=())

    def test_line_numbers_are_unique(self):
        lines = [row.split(":")[0] for row in self.inv.branch_index().splitlines()]
        self.assertEqual(len(lines), len(set(lines)), self.inv.branch_index())

    def test_conditioned_branch_wins_over_bare_else(self):
        row = [r for r in self.inv.branch_index().splitlines() if r.startswith("5:")][0]
        self.assertIn("IF", row)
        self.assertIn('"2"', row)

    def test_at_line_resolves_to_the_conditioned_branch(self):
        branch = self.inv.at_line(5)
        self.assertEqual(branch.cond_values, frozenset({"2"}))

    def test_single_atom_if_is_a_decision_not_a_complement(self):
        inv = build_inventory(
            "if (enabled)\n{\n    run();\n}\n",
            [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                                expression="enabled")],
            "c",
            statements=(),
        )
        row = inv.branch_index().splitlines()[0]
        self.assertIn("[decision] IF", row)
        self.assertIn("enabled", row)
        self.assertNotIn("그 외", row)

    def test_switch_is_controller_and_default_case_is_complement(self):
        body = (
            "switch (kind)\n{\n"
            'case "A":\n    first();\n'
            "default:\n    other();\n}\n"
        )
        tree = [
            ControlSubtreeNode(
                node_id="switch",
                ntype="SWITCH",
                start=1,
                end=7,
                expression="kind",
                children=[
                    ControlSubtreeNode(node_id="a", ntype="CASE", start=3, end=4,
                                       expression='"A"'),
                    # grammar 사실: caseless 라벨(default)은 expression 이 없다.
                    ControlSubtreeNode(node_id="default", ntype="CASE", start=5, end=6),
                ],
            )
        ]
        rows = build_inventory(body, tree, "c", statements=()).branch_index().splitlines()
        self.assertIn("[controller] SWITCH", rows[0])
        self.assertIn("[decision] CASE", rows[1])
        self.assertIn("[complement] CASE", rows[2])
        self.assertIn("그 외", rows[2])

    def test_switch_default_path_negates_sibling_cases_not_the_subject(self):
        """default 도달 조건은 '주제식의 부정'이 아니라 '형제 case 전부 불성립'이다.

        실측 결함(L-③ 판정, 08 §20 ⑥): controller(주제식 `kind`)를 부정해
        `not (F("kind"))` 로 렌더되어 의미가 어긋났다. 올바른 경로는 각 case 라벨의 부정이다.
        """
        from pipeline.quality_gates.rule_prompt_evidence import rule_evidence

        body = (
            "switch (kind)\n{\n"
            'case "A":\n    first();\n'
            'case "B":\n    second();\n'
            "default:\n    other();\n}\n"
        )
        tree = [
            ControlSubtreeNode(
                node_id="switch", ntype="SWITCH", start=1, end=9, expression="kind",
                children=[
                    ControlSubtreeNode(node_id="a", ntype="CASE", start=3, end=4,
                                       expression='"A"'),
                    ControlSubtreeNode(node_id="b", ntype="CASE", start=5, end=6,
                                       expression='"B"'),
                    ControlSubtreeNode(node_id="default", ntype="CASE", start=7, end=8),
                ],
            )
        ]
        inventory = build_inventory(body, tree, "c", statements=())
        complement = next(
            r for r in inventory.table_rows() if r.get("role") == "complement"
        )
        evidence = rule_evidence(inventory, [int(complement["line"])])
        when = evidence["rows"][f"R{complement['line']}"]["when"]
        conditions = evidence["conditions"]

        subject_refs = [
            ref for ref in when
            if conditions.get(ref.lstrip("!"), "").strip() == "kind"
        ]
        self.assertEqual(
            [], subject_refs,
            "default 경로가 주제식(kind)을 부정하면 안 된다 — 형제 case 를 부정해야 한다",
        )
        negated_labels = {
            conditions[ref.lstrip("!")] for ref in when if ref.startswith("!")
        }
        self.assertEqual({'"A"', '"B"'}, negated_labels)

    def test_nested_else_negates_direct_decision_and_keeps_outer_holding(self):
        # if A { if B {} else {} } — 안쪽 else 는 B 의 부정이면서 A 는 **성립**(본문 중첩).
        body = (
            "if (outer)\n{\n"
            "  if (inner)\n  {\n    first();\n  }\n"
            "  else\n  {\n    second();\n  }\n"
            "}\n"
        )
        inner = ControlSubtreeNode(
            node_id="inner",
            ntype="IF",
            start=3,
            end=10,
            expression="inner",
            children=[
                ControlSubtreeNode(
                    node_id="inner_else",
                    ntype="ELSE",
                    start=7,
                    end=10,
                )
            ],
        )
        tree = [
            ControlSubtreeNode(
                node_id="outer",
                ntype="IF",
                start=1,
                end=11,
                expression="outer",
                children=[inner],
            )
        ]
        rows = build_inventory(body, tree, "c", statements=()).branch_index().splitlines()
        complement = next(row for row in rows if "[complement]" in row)
        self.assertIn("직접 decision @3의 부정", complement)
        self.assertIn("외곽 @1 성립", complement)

    def test_parent_if_owns_else_even_when_its_body_contains_a_later_loop(self):
        # 실제 Check_Sum32 parser 형상: IF.children == [LOOP, ELSE(IF ...)].
        # LOOP는 ELSE의 선행 형제일 뿐 소유자가 아니다.
        body = (
            "if (flag == 1)\n{\n"
            "  for (i = 0; i < 3; i++)\n  {\n    first();\n  }\n"
            "}\nelse if (flag == 2)\n{\n  second();\n}\n"
        )
        outer = ControlSubtreeNode(
            node_id="outer",
            ntype="IF",
            start=1,
            end=11,
            expression="flag == 1",
            children=[
                ControlSubtreeNode("loop", "LOOP", 3, 6, expression="i < 3"),
                ControlSubtreeNode(
                    "outer_else", "ELSE", 8, 11,
                    children=[ControlSubtreeNode("inner", "IF", 8, 11,
                                                 expression="flag == 2")],
                ),
            ],
        )
        inv = build_inventory(body, [outer], "c", statements=())
        complement = next(branch for branch in inv.all_branches if branch.ntype == "ELSE")
        self.assertIs(inv.all_branches[0], inv.paired_decision(complement))

    def test_python_loop_else_is_owned_by_its_loop_via_ontology(self):
        body = "for item in items:\n    use(item)\nelse:\n    empty()\n"
        loop = ControlSubtreeNode(
            "loop", "LOOP", 1, 4, expression="item in items",
            children=[ControlSubtreeNode("loop_else", "ELSE", 3, 4)],
        )
        inv = build_inventory(body, [loop], "python", statements=())
        complement = next(branch for branch in inv.all_branches if branch.ntype == "ELSE")
        self.assertIs(inv.all_branches[0], inv.paired_decision(complement))

    def test_else_if_chain_final_else_negates_every_outer_decision(self):
        # if A ... else if B ... else — 마지막 else 는 A·B **모두의 부정**이다.
        # "외곽 조건도 상속"으로 쓰면 정반대다 (spec 116 1단계 — 실측 결함 수리).
        body = (
            'if (a == "1")\n{\n  x();\n}\n'
            'else if (a == "2")\n{\n  y();\n}\n'
            "else\n{\n  z();\n}\n"
        )
        if2 = ControlSubtreeNode(
            node_id="if2",
            ntype="IF",
            start=5,
            end=12,
            expression='a == "2"',
            children=[
                ControlSubtreeNode(node_id="el2", ntype="ELSE", start=9, end=12)
            ],
        )
        tree = [
            ControlSubtreeNode(
                node_id="if1",
                ntype="IF",
                start=1,
                end=12,
                expression='a == "1"',
                children=[
                    ControlSubtreeNode(
                        node_id="el1", ntype="ELSE", start=5, end=12, children=[if2]
                    )
                ],
            )
        ]
        rows = build_inventory(body, tree, "c", statements=()).branch_index().splitlines()
        complement = next(row for row in rows if "[complement]" in row)
        self.assertTrue(complement.strip().startswith("9:"), complement)
        self.assertIn("직접 decision @5의 부정", complement)
        self.assertIn("외곽 @1 부정", complement)
        self.assertNotIn("성립", complement)
        self.assertNotIn("상속", complement)

    def test_switch_default_complement_points_at_its_switch_not_outer_decision(self):
        # if(A) { switch(k) { case..: default: } } — default 는 SWITCH 의 여집합이며
        # 외곽 if(A) 는 성립이다. 이전 표기는 외곽 decision 을 "직접 대응"으로 오지목했다.
        body = (
            "if (mode)\n{\n"
            "  switch (kind)\n  {\n"
            '  case "A":\n    first();\n'
            "  default:\n    other();\n  }\n"
            "}\n"
        )
        tree = [
            ControlSubtreeNode(
                node_id="if",
                ntype="IF",
                start=1,
                end=10,
                expression="mode",
                children=[
                    ControlSubtreeNode(
                        node_id="switch",
                        ntype="SWITCH",
                        start=3,
                        end=9,
                        expression="kind",
                        children=[
                            ControlSubtreeNode(node_id="a", ntype="CASE", start=5, end=6,
                                               expression='"A"'),
                            ControlSubtreeNode(node_id="d", ntype="CASE", start=7, end=8),
                        ],
                    )
                ],
            )
        ]
        rows = build_inventory(body, tree, "c", statements=()).branch_index().splitlines()
        complement = next(row for row in rows if "[complement]" in row)
        self.assertIn("SWITCH@3의 다른 분기 모두 불성립", complement)
        self.assertIn("외곽 @1 성립", complement)


class ConditionRefRendering(unittest.TestCase):
    """모델이 고른 조건 ref → canonical 표기 렌더의 실패 계약.

    ref 정규식은 모양만 닫혀 있어(`^!?C[1-9][0-9]*$`) 증거에 없는 번호도 통과한다.
    그때 번호를 원문으로 둔갑시키면 정상 조건과 구별되지 않는다 — 효과 축이
    `invalid_effect(...)` 로 표면화하는 것과 같은 계약을 조건 축에도 요구한다.
    """

    def test_ref_absent_from_evidence_is_surfaced_not_fabricated(self):
        from pipeline.quality_gates.rule_prompt_evidence import render_condition_refs

        rendered = render_condition_refs(["C7"], {})

        self.assertNotIn(
            'F("C7")', rendered or "",
            "증거에 없는 ref 를 원문으로 둔갑시키면 안 된다",
        )
        self.assertIn("invalid_condition", rendered or "")
        self.assertIn("C7", rendered or "", "원문 후보는 보존해야 사후 판정이 된다")

    def test_invalid_condition_marker_is_outside_the_cond_vocabulary(self):
        """표식은 채점 어휘 밖이어야 행이 실제로 격리된다."""
        from pipeline.models.rule_expression import COND_HELPERS, parse_condition
        from pipeline.models.rule_expression import condition_call_names
        from pipeline.quality_gates.rule_prompt_evidence import render_condition_refs

        rendered = render_condition_refs(["!C7"], {"C9": "rc == 0"})
        names = condition_call_names(parse_condition(rendered))

        self.assertTrue(
            names - set(COND_HELPERS),
            "표식이 허용 어휘 안에 있으면 채점기가 통과시켜 버린다",
        )

    def test_known_refs_still_render_polarity_and_exact_source(self):
        from pipeline.quality_gates.rule_prompt_evidence import render_condition_refs

        rendered = render_condition_refs(
            ["C1", "!C2"], {"C1": "rc == RC_NRM", "C2": "len > 0"},
        )

        self.assertEqual('F("rc == RC_NRM") and not (F("len > 0"))', rendered)


class TerminationRows(unittest.TestCase):
    """종료문·핸들러 행 — 종료 계약이 행이어야 절단·누락이 뺄셈으로 드러난다 (spec 116).

    행의 원천은 parser AST 다: [exit] 는 RETURN/THROW/RAISE StatementFact,
    [handler] 는 SKILL 마커와 일치하는 FUNCTION_CALL 좌표(call_lines)다.
    """

    def test_return_fact_becomes_exit_row(self):
        body = 'if (bad)\n{\n  rc = -1;\n}\nreturn rc;\n'
        tree = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                                   expression="bad")]
        inv = build_inventory(
            body, tree, "c",
            statements=(StatementFact("r5", "RETURN", 5, 5, expression="rc"),),
        )
        rows = inv.branch_index().splitlines()
        exit_row = next(row for row in rows if "[exit]" in row)
        self.assertTrue(exit_row.startswith("5:"), exit_row)
        self.assertIn("return rc", exit_row)

    def test_handler_marker_becomes_handler_row(self):
        # 핸들러 마커는 언어 표가 아니라 프레임워크 SKILL 계약이 주입한다(오염 금지).
        body = "do_work();\nPFM_CATCH();\nreturn RC_ERR;\n"
        inv = build_inventory(
            body, [], "c", handler_markers=("PFM_CATCH",),
            statements=(StatementFact("r3", "RETURN", 3, 3, expression="RC_ERR"),),
            call_lines=((1, "do_work"), (2, "PFM_CATCH")),
        )
        rows = inv.branch_index().splitlines()
        self.assertTrue(any(row.startswith("2: [handler] PFM_CATCH") for row in rows), rows)
        self.assertTrue(any(row.startswith("3: [exit] return RC_ERR") for row in rows), rows)

    def test_handler_marker_without_injection_is_not_a_row(self):
        # 마커 미주입(스킬 미매칭 프로젝트) — 프레임워크 지식이 어디에도 굳어 있지 않다.
        body = "do_work();\nPFM_CATCH();\nreturn RC_ERR;\n"
        inv = build_inventory(
            body, [], "c",
            statements=(StatementFact("r3", "RETURN", 3, 3, expression="RC_ERR"),),
            call_lines=((1, "do_work"), (2, "PFM_CATCH")),
        )
        rows = inv.branch_index().splitlines()
        self.assertFalse(any("[handler]" in row for row in rows), rows)
        self.assertTrue(any(row.startswith("3: [exit] return RC_ERR") for row in rows), rows)

    def test_return_on_a_branch_line_does_not_duplicate_the_row(self):
        # `if (x) return -1;` — 그 줄은 분기 행이 대표한다(줄번호 유일).
        body = "if (x) return -1;\nreturn 0;\n"
        tree = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=1,
                                   expression="x")]
        inv = build_inventory(
            body, tree, "c",
            statements=(
                StatementFact("r1", "RETURN", 1, 1, expression="-1"),
                StatementFact("r2", "RETURN", 2, 2, expression="0"),
            ),
        )
        rows = inv.branch_index().splitlines()
        lines = [row.strip().split(":")[0] for row in rows]
        self.assertEqual(len(lines), len(set(lines)), rows)
        self.assertTrue(any(row.startswith("2: [exit]") for row in rows), rows)

    def test_commented_return_has_no_ast_fact_and_no_row(self):
        # 주석 처리된 return 은 parser 가 emit 하지 않는다 — 행의 원천이 AST 라는 계약.
        body = "// return 1;\nreturn 2;\n"
        inv = build_inventory(
            body, [], "c",
            statements=(StatementFact("r2", "RETURN", 2, 2, expression="2"),),
        )
        rows = inv.branch_index().splitlines()
        self.assertTrue(rows[0].startswith("2: [exit]"), rows)
        self.assertEqual(1, sum(1 for row in rows if "[exit]" in row))


class EntryRow(unittest.TestCase):
    """[entry] 행 — 분기 밖 최상위 효과의 무조건 계약 좌표 (when=함수 실행)."""

    def test_linear_function_gets_one_entry_row(self):
        inv = build_inventory(
            'strncpy(dst, src, LEN);\nsprintf(msg, "x");\n', [], "c",
            statements=(),
            call_lines=((1, "strncpy"), (2, "sprintf")),
        )
        entries = [row for row in inv.table_rows() if row["role"] == "entry"]
        self.assertEqual([{"line": 1, "role": "entry"}], entries)
        self.assertTrue(
            any(row.startswith("1: [entry]") for row in inv.branch_index().splitlines())
        )
