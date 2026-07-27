"""Semantic contracts apply unchanged across languages and unrelated domains."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _safe_format
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
            "QUESTION은 실제 공백만",
            "지원되지 않은 추가 의미 금지",
            "구조 인덱스 회수와 압축 금지",
            "Example 수와 길이",
            "한 Rule의 Example은 최대 2개",
            "잘못:",
            "좋음:",
        ):
            self.assertIn(required, framework)

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
