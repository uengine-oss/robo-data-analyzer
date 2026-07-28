"""Semantic contracts apply unchanged across languages and unrelated domains."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _safe_format
from pipeline.models.code_analysis_output import ExampleItem, RuleItem, RulesExamplesOutput
from pipeline.languages import (
    coerce_stereotype,
    module_stereotype_prompt_options,
    stereotype_prompt_options,
    stereotype_values,
)


ROOT = Path(__file__).resolve().parents[3]


class SemanticPromptGeneralityTest(unittest.TestCase):
    FRAMEWORK_FIXTURES = (
        (
            "java-ordering",
            "public Result apply(Request input) { return gateway.send(encode(input)); }",
            "en-US",
        ),
        (
            "python-library",
            "def lend(book, member):\n    return register.create(book, member)",
            "ko",
        ),
        (
            "c-device",
            "int normalize(Packet *p) { validate(p); return encode(p); }",
            "ja",
        ),
    )

    def _render_framework_summary(self, body: str, locale: str) -> str:
        rule = _load_rule(ROOT / "llm" / "prompts" / "framework", "code_summary")
        return _safe_format(
            rule["user_template"],
            {
                **rule["static_values"],
                "response_locale": locale,
                "codebase_notes": "",
                "module_intent": "",
                "reads": "[]",
                "writes": "[]",
                "referenced_symbols": "[]",
                "external_evidence": "[]",
                "callee_summaries": "[]",
                "id": "code:generic:target",
                "signature": "target",
                "code_body": body,
                "source_file": "generic.source",
                "source_range": "1-1",
                "source_status": "available",
                "coordinate_system": "absolute-file-line",
            },
        )

    def test_same_contract_wraps_java_python_and_c_without_special_branch(self) -> None:
        contracts = []
        for name, body, locale in self.FRAMEWORK_FIXTURES:
            with self.subTest(name=name):
                rendered = self._render_framework_summary(body, locale)
                self.assertIn(body, rendered)
                self.assertIn(f"response_locale: {locale}", rendered)
                self.assertIn("## [TARGET]", rendered)
                self.assertIn("## [DIRECT_EVIDENCE]", rendered)
                self.assertIn("## [SURROUNDING_CONTEXT — NOT TARGET BEHAVIOR]", rendered)
                self.assertIn("## [CALLEE_INTERFACE — NOT CALLEE IMPLEMENTATION]", rendered)
                self.assertIn("coordinate_system: absolute-file-line", rendered)
                self.assertIn("잘못:", rendered)
                self.assertIn("좋음:", rendered)
                self.assertIn("분석 대상으로 제공된 모든 루틴은 `search_text`를 채운다", rendered)
                self.assertIn("본문 없이 호출 규약과 반환 형식만 선언한다", rendered)
                contracts.append(rendered.replace(body, "<BODY>").replace(locale, "<LOCALE>"))
        self.assertEqual(1, len(set(contracts)))

    def test_sql_batch_uses_same_search_contract_and_dbms_ontology(self) -> None:
        rule = _load_rule(ROOT / "llm" / "prompts" / "dbms", "code_summary_batch")
        body = "BEGIN SELECT state INTO v_state FROM records WHERE id = p_id; END;"
        rendered = _safe_format(
            rule["user_template"],
            {
                **rule["static_values"],
                "response_locale": "fr",
                "unit_id": "unit:generic",
                "unit_signature": "procedure target",
                "module_intent": "",
                "codebase_notes": "",
                "ancestor_context": "",
                "node_count": 1,
                "node_keys": "1-1",
                "nodes": body,
            },
        )
        self.assertIn(body, rendered)
        self.assertIn("response_locale: fr", rendered)
        self.assertIn("## [TARGET_BATCH]", rendered)
        self.assertIn("## [SURROUNDING_CONTEXT — NOT NODE BEHAVIOR]", rendered)
        self.assertIn("## [TARGET_NODES]", rendered)
        self.assertIn("coordinate_system: absolute-file-line", rendered)
        self.assertIn("잘못:", rendered)
        self.assertIn("좋음:", rendered)
        self.assertIn(stereotype_prompt_options("dbms"), rendered)
        self.assertNotIn("`ApplicationService`, `DomainService`", rendered)

    def test_stereotype_prompt_and_parser_share_exact_ontology(self) -> None:
        for strategy in ("framework", "dbms"):
            with self.subTest(strategy=strategy):
                values = stereotype_values(strategy)
                rendered = stereotype_prompt_options(strategy)
                self.assertEqual(
                    list(values),
                    [
                        line.split("`", 2)[1]
                        for line in rendered.splitlines()
                        if line.startswith("- `")
                    ],
                )
                for value in values:
                    self.assertRegex(rendered, rf"(?m)^- `{re.escape(value)}`: .+")
                for value in values:
                    self.assertEqual(value, coerce_stereotype(value, strategy))
                self.assertEqual("", coerce_stereotype("CorpusSpecificRole", strategy))

        self.assertTrue({"Router", "Utility", "Util"} <= set(stereotype_values("framework")))

    def test_rule_risk_contract_is_one_shared_good_bad_source(self) -> None:
        framework = _load_rule(
            ROOT / "llm" / "prompts" / "framework", "code_rules_examples"
        )["static_values"]["rule_evidence_contract"]
        dbms = _load_rule(
            ROOT / "llm" / "prompts" / "dbms", "code_rules_examples_batch"
        )["static_values"]["rule_evidence_contract"]
        self.assertEqual(framework, dbms)
        for required in (
            "조건 방향과 범위 보존",
            "종료 계약 분리",
            "활성 코드만 사실",
            "호출·필드 소유권 보존",
            "값과 fallback 회계",
            "종료 전에 실제 수행된 효과만 서술",
            "QUESTION은 실제 공백만",
            "지원되지 않은 추가 의미 금지",
            "연산 범위와 결과 종류 보존",
            "같은 효과 값은 완전하게 그룹화",
            "숫자 결과는 SOURCE 연산으로 검산",
            "모든 Rule은 양의 절대 근거 줄",
            "구조 인덱스 회수와 압축 금지",
            "Example 수와 길이",
            "관측 계약별 Rule 목록을 완성한다",
            "Rule로 합치지 않는다",
            "외부 결과로 이어지는 데이터 조립",
            "성공·무변경·실패 후 계속",
            "여러 독립 단계나 서로 다른 분기를 한 개의 “전체 처리” Rule로 압축하지 않는다",
            "반환 객체, 출력 파라미터, 저장 레코드, 메시지, 조회 키, 외부 호출 인자",
            "셋 중 하나 이상 있으면 다음 단계로 진행",
            "잘못:",
            "좋음:",
        ):
            self.assertIn(required, framework)

    def test_rule_anchor_contract_is_identical_for_unrelated_languages_and_domains(self) -> None:
        fixtures = (
            ("java", "배송 요청", 17),
            ("python", "도서 대여", 23),
            ("c", "장치 상태", 31),
            ("sql", "재고 조회", 47),
        )
        for language, statement, line in fixtures:
            with self.subTest(language=language):
                parsed = RulesExamplesOutput(rules=[
                    RuleItem(
                        statement=f"{statement}을 처리하면 결과를 반환한다.",
                        anchor_line=line,
                        examples=[
                            ExampleItem(
                                given=f"{statement} 입력",
                                when="처리를 요청",
                                then="결과를 반환",
                            )
                        ],
                    )
                ])
                self.assertEqual(line, parsed.rules[0].anchor_line)

        for strategy, rule_name in (
            ("framework", "code_rules_examples"),
            ("dbms", "code_rules_examples_batch"),
        ):
            text = _load_rule(
                ROOT / "llm" / "prompts" / strategy,
                rule_name,
            )["user_template"]
            with self.subTest(strategy=strategy):
                self.assertIn("양의 절대 파일 줄번호", text)
                self.assertIn("anchor_line: 0", text)
                self.assertIn("잘못", text)
                self.assertIn("좋음", text)

    def test_framework_rule_checklist_is_the_last_volatile_evidence(self) -> None:
        rule = _load_rule(
            ROOT / "llm" / "prompts" / "framework", "code_rules_examples"
        )
        text = rule["user_template"]
        self.assertLess(text.index("child_business_logic:"), text.index("branch_index:"))
        self.assertEqual("branch_index:\n{branch_index}", text.rstrip().split(
            "# [Rule 전용 고변동 증거]", 1
        )[1].strip().split("\n\n")[-1])

    def test_framework_stereotype_rubric_covers_residual_role_boundaries(self) -> None:
        text = _load_rule(
            ROOT / "llm" / "prompts" / "framework",
            "code_summary",
        )["user_template"]
        for role in (
            "Initializer",
            "Util",
            "Utility",
            "Helper",
            "Adapter",
            "Query",
            "Validator",
            "Router",
            "ApplicationService",
        ):
            with self.subTest(role=role):
                self.assertIn(f"`{role}`", text)
        for decision_boundary in (
            "로컬 변수, 작업 버퍼, 출력 파라미터, 반환 객체에 값을 쓰는 것은 그 자체로 업무 상태",
            "외부 상태를 바꾸지 않으면 `Query`",
            "단순히 외부 조회를 호출하거나 반환값을 전달하는 것만으로는 Adapter가 아니다",
            "최종 산출물이 하나의 허용/거절·정합성 판정이면 Validator",
            "서로 다른 업무 단계",
            "저수준 자료구조·문자열 원시 연산은 `Util`",
        ):
            with self.subTest(boundary=decision_boundary):
                self.assertIn(decision_boundary, text)

    def test_module_intent_does_not_infer_business_meaning_from_signatures(self) -> None:
        rule = _load_rule(
            ROOT / "llm" / "prompts" / "framework", "module_pre_analysis"
        )
        contract = rule["static_values"]["module_intent_contract"]
        self.assertIn("시그니처는", contract)
        self.assertIn("보조 증거", contract)
        self.assertIn("빈 문자열", contract)
        self.assertIn("잘못:", contract)
        self.assertIn("좋음:", contract)
        self.assertLess(
            rule["user_template"].index("{module_intent_contract}"),
            rule["user_template"].index("module_header_metadata:"),
        )

    def test_component_naming_contract_is_shared_and_closed(self) -> None:
        rendered = []
        contracts = []
        for strategy in ("framework", "dbms"):
            rule = _load_rule(
                ROOT / "llm" / "prompts" / strategy, "component_naming"
            )
            contracts.append(rule["static_values"]["component_naming_contract"])
            text = _safe_format(
                rule["user_template"],
                {
                    **rule["static_values"],
                    "component_id": "component:generic",
                    "members": "- 요청 검증\n- 요청 접수",
                },
            )
            self.assertIn(stereotype_prompt_options(strategy), text)
            self.assertIn("잘못:", text)
            self.assertIn("좋음:", text)
            rendered.append(text)
        self.assertEqual(contracts[0], contracts[1])

    def test_module_summary_contract_uses_strategy_closed_pool(self) -> None:
        for strategy in ("framework", "dbms"):
            rule = _load_rule(ROOT / "llm" / "prompts" / strategy, "module_summary")
            rendered = _safe_format(
                rule["user_template"],
                {
                    **rule["static_values"],
                    "id": "module:generic",
                    "name": "generic",
                    "member_code_nodes": "- 요청을 검증한다.",
                },
            )
            self.assertIn(module_stereotype_prompt_options(strategy), rendered)

    def test_prompts_have_no_observed_corpus_identifiers(self) -> None:
        forbidden = re.compile(r"(?i)\b(?:skt|apas|zpay|sk-van)\b")
        for path in (ROOT / "llm" / "prompts").rglob("*"):
            if path.is_file() and path.suffix in {".yaml", ".md"}:
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    def test_every_semantic_prompt_pairs_bad_and_good_examples(self) -> None:
        cases = (
            ("framework", "code_summary"),
            ("framework", "code_table_insights"),
            ("framework", "code_rules_examples"),
            ("framework", "module_pre_analysis"),
            ("framework", "module_summary"),
            ("framework", "component_naming"),
            ("framework", "table_description"),
            ("framework", "glossary_synthesis"),
            ("dbms", "code_summary_batch"),
            ("dbms", "code_table_insights_batch"),
            ("dbms", "code_rules_examples_batch"),
            ("dbms", "code_gate_rules_batch"),
            ("dbms", "module_summary"),
            ("dbms", "component_naming"),
            ("dbms", "table_description"),
            ("dbms", "glossary_synthesis"),
            ("common", "large_code_one_liner"),
        )
        for strategy, rule_name in cases:
            rule = _load_rule(ROOT / "llm" / "prompts" / strategy, rule_name)
            text = rule["system"] + rule["user_template"] + "\n".join(
                rule["static_values"].values()
            )
            with self.subTest(strategy=strategy, rule=rule_name):
                self.assertRegex(text, r"(?:잘못|나쁨|금지)")
                self.assertRegex(text, r"(?:좋음|좋은|올바른|정상:)")

    def test_rule_inventory_is_not_compressed_to_reduce_examples(self) -> None:
        common = (
            ROOT
            / "llm"
            / "prompts"
            / "common"
            / "rule_evidence_contract.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "먼저 Example 수를 전혀 고려하지 않고 관측 계약별 Rule 목록을 완성한다",
            common,
        )
        self.assertIn(
            "Example 수를 줄이기 위해 서로 다른 조건·결과·예외를 한 Rule로 합치지 않는다",
            common,
        )
        self.assertNotIn("별도 경계나 fallback이 있을 때만\n2개까지", common)

        for strategy, rule_name in (
            ("framework", "code_rules_examples"),
            ("dbms", "code_rules_examples_batch"),
        ):
            rule = _load_rule(
                ROOT / "llm" / "prompts" / strategy,
                rule_name,
            )
            text = rule["user_template"] + "\n".join(
                rule["static_values"].values()
            )
            with self.subTest(strategy=strategy):
                self.assertIn("Rule 목록을 모두 확정한 뒤", text)
                self.assertIn("Example 수를 줄이", text)

    def test_semantic_logic_has_no_corpus_specific_condition(self) -> None:
        forbidden_branch = re.compile(
            r"(?im)^\s*(?:if|elif)\b[^\n]*(?:skt|apas|zpay|customer|bank|telecom)"
        )
        semantic_files = (
            ROOT / "pipeline" / "steps" / "step7_analyze_code" / "summary_quality.py",
            ROOT / "pipeline" / "languages.py",
            ROOT / "pipeline" / "execution" / "analysis_profile.py",
        )
        for path in semantic_files:
            with self.subTest(path=path.name):
                self.assertIsNone(forbidden_branch.search(path.read_text(encoding="utf-8")))

    def test_component_failure_does_not_copy_another_semantic_field(self) -> None:
        source = (
            ROOT
            / "pipeline"
            / "steps"
            / "step8_post_processing"
            / "build_components_step.py"
        ).read_text(encoding="utf-8")
        failure_branch = source[source.index("if isinstance(result, BaseException):"):]
        self.assertIn('names[cid] = ""', failure_branch)
        self.assertNotIn("names[cid] = _headline", failure_branch)


if __name__ == "__main__":
    unittest.main()
