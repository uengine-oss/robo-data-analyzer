"""Semantic contracts apply unchanged across languages and unrelated domains."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _safe_format
from pipeline.models.code_analysis_output import RuleItem
from pipeline.languages import (
    module_stereotype_prompt_options,
    require_stereotype,
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
                "table_ops": "{}",
                "data_object_usages": "[]",
                "referenced_symbols": "[]",
                "external_evidence": "[]",
                "callee_summaries": "[]",
                "branch_index": "(없음)",
                "id": "code:generic:target",
                "signature": "target",
                "code_body": body,
                "source_file": "generic.source",
                "source_range": "1-1",
                "source_status": "available",
                "coordinate_system": "absolute-file-line",
                "structural_profile": "{}",
                "applicable_slots": "[]",
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
                self.assertIn("## [EFFECTIVE_FLOW_EVIDENCE]", rendered)
                self.assertIn("## [NON-AUTHORITATIVE CONTEXT STATUS]", rendered)
                self.assertIn(
                    "## [ASSIGNED CALL FACTS — NOT CALLEE IMPLEMENTATION PROSE]",
                    rendered,
                )
                self.assertIn("coordinate_system: absolute-file-line", rendered)
                self.assertIn("잘못:", rendered)
                self.assertIn("좋음:", rendered)
                self.assertIn("`slots`", rendered)
                self.assertIn("`stereotype`", rendered)
                self.assertIn("missing_context", rendered)
                self.assertNotIn("`domain_terms`", rendered)
                self.assertNotIn("`logical_name`", rendered)
                contracts.append(rendered.replace(body, "<BODY>").replace(locale, "<LOCALE>"))
        self.assertEqual(1, len(set(contracts)))

    def test_sql_batch_uses_same_single_meaning_contract_and_dbms_ontology(self) -> None:
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
                    self.assertEqual(value, require_stereotype(value, strategy))
                with self.assertRaises(ValueError):
                    require_stereotype("CorpusSpecificRole", strategy)

        self.assertTrue({"Router", "Utility", "Util"} <= set(stereotype_values("framework")))

    def test_each_rule_places_fixed_contract_before_variable_target_input(self) -> None:
        # RadixAttention은 첫 변동 지점까지만 재사용한다. 같은 concern의 모든 대상이 긴 고정
        # 계약을 공유하도록 분할 계약을 TARGET/SOURCE보다 앞에 두고, 행 증거는 맨 뒤에 둔다.
        for strategy, names in (
            ("framework", ("code_summary", "code_table_insights")),
            ("dbms", ("code_summary_batch", "code_table_insights_batch")),
        ):
            for name in names:
                with self.subTest(strategy=strategy, name=name):
                    loaded = _load_rule(
                        ROOT / "llm" / "prompts" / strategy, name
                    )
                    template = loaded["user_template"]
                    expanded = template
                    for key, value in loaded["static_values"].items():
                        expanded = expanded.replace("{" + key + "}", value)
                    self.assertTrue(template.lstrip().startswith("# [TASK"))
                    self.assertLess(template.index("# [TASK"), template.index("# [INPUT"))
                    for contract_marker in (
                        "[OUTPUT CONTRACT", "[INPUT GUIDE", "[DECISION", "[SELF CHECK]",
                    ):
                        self.assertIn(contract_marker, expanded)
                    self.assertLess(template.index("# [TASK"), template.index("# [INPUT"))

        self.assertFalse(
            (ROOT / "llm" / "prompts" / "framework" / "code_rules_examples.yaml").exists()
        )
        self.assertFalse(
            (ROOT / "llm" / "prompts" / "dbms" / "code_rules_examples_batch.yaml").exists()
        )

    def test_rule_model_contract_is_physically_absent(self) -> None:
        retired = (
            ROOT / "llm" / "prompts" / "common" / "rule_leaf_contract.md",
            ROOT / "llm" / "prompts" / "framework" / "_rule_common.yaml",
            ROOT / "llm" / "prompts" / "framework" / "code_rules_examples.yaml",
            ROOT / "llm" / "prompts" / "dbms" / "_rule_common.yaml",
            ROOT / "llm" / "prompts" / "dbms" / "code_rules_examples_batch.yaml",
            ROOT / "llm" / "prompts" / "dbms" / "code_gate_rules_batch.yaml",
        )
        self.assertTrue(all(not path.exists() for path in retired))

    def test_rule_anchor_contract_is_identical_for_unrelated_languages_and_domains(self) -> None:
        fixtures = (
            ("java", "배송 요청", 17),
            ("python", "도서 대여", 23),
            ("c", "장치 상태", 31),
            ("sql", "재고 조회", 47),
        )
        for language, statement, line in fixtures:
            with self.subTest(language=language):
                parsed = RuleItem(
                    anchor_line=line,
                    cond='F("req_cd") == "01"',
                    then=['ret("0")'],
                )
                self.assertEqual(line, parsed.anchor_line)
                self.assertEqual("", parsed.nl)

    def test_rule_contract_is_language_neutral_structure(self) -> None:
        for language, _statement, line in (
            ("java", "배송 요청", 17),
            ("python", "도서 대여", 23),
            ("c", "장치 상태", 31),
            ("sql", "재고 조회", 47),
        ):
            with self.subTest(language=language):
                item = RuleItem(
                    anchor_line=line,
                    cond='F("state") == "READY"',
                    then=['call("handle")'],
                )
                self.assertEqual("", item.nl)

    def test_framework_stereotype_rubric_covers_residual_role_boundaries(self) -> None:
        text = _load_rule(
            ROOT / "llm" / "prompts" / "framework",
            "code_summary",
        )["user_template"]
        for role in ("Util", "Utility", "Helper", "Adapter", "Query",
                     "Validator", "Router", "ApplicationService"):
            with self.subTest(role=role):
                self.assertIn(f"`{role}`", text)
        for decision_boundary in (
            "정보가 산출물이면 `Query`",
            "I/O 계약을 다른 모듈 계약으로 변환",
            "직접 검사 종합은 `Validator`",
            "여러 업무 단계를 조정",
            "저수준 자료구조·문자열 연산은 `Util`",
        ):
            with self.subTest(boundary=decision_boundary):
                self.assertIn(decision_boundary, text)

    def test_module_intent_does_not_infer_business_meaning_from_signatures(self) -> None:
        rule = _load_rule(
            ROOT / "llm" / "prompts" / "framework", "module_pre_analysis"
        )
        contract = rule["static_values"]["semantic_frame_contract"]
        self.assertIn("MODULE.RESPONSIBILITY/DISPLAY_NAME/INTENT", contract)
        self.assertIn("insufficient", contract)
        self.assertIn("잘못:", contract)
        self.assertIn("좋음:", contract)
        self.assertLess(
            rule["user_template"].index("{semantic_frame_contract}"),
            rule["user_template"].index("module_header_metadata:"),
        )

    def test_table_description_preserves_authoritative_semantic_head(self) -> None:
        contract = (
            ROOT / "llm" / "prompts" / "common" / "table_description_contract.md"
        ).read_text(encoding="utf-8")
        for required in (
            "중심 명사와 수식어",
            "source의 업무 명사구를 그대로 재사용",
            "사람/계정/기관/지점",
            "원장등록/신청가능",
            "후보를 버리며 둘을 “또는”으로 합쳐",
        ):
            self.assertIn(required, contract)

    def test_component_naming_contract_is_shared_and_closed(self) -> None:
        rendered = []
        contracts = []
        for strategy in ("framework", "dbms"):
            rule = _load_rule(
                ROOT / "llm" / "prompts" / strategy, "component_naming"
            )
            contracts.append(rule["static_values"]["semantic_frame_contract"])
            text = _safe_format(
                rule["user_template"],
                {
                    **rule["static_values"],
                    "component_id": "component:generic",
                    "members": "- 요청 검증\n- 요청 접수",
                    "structural_profile": "{}",
                    "applicable_slots": "[]",
                },
            )
            self.assertIn("잘못:", text)
            self.assertIn("좋음:", text)
            self.assertIn("COMPONENT.DISPLAY_NAME", text)
            self.assertNotIn("허용 stereotype:", text)
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
                    "structural_profile": "{}",
                    "applicable_slots": "[]",
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
            ("framework", "module_pre_analysis"),
            ("framework", "module_summary"),
            ("framework", "component_naming"),
            ("framework", "table_description"),
            ("dbms", "code_summary_batch"),
            ("dbms", "code_table_insights_batch"),
            ("dbms", "module_summary"),
            ("dbms", "component_naming"),
            ("dbms", "table_description"),
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
        # 폐기된 자연어 위험 계약(rule_evidence_contract.md)은 소비자 0으로 삭제됐다 —
        # 파일이 되살아나면 죽은 데이터 재유입이므로 부재를 게이트로 단언한다.
        self.assertFalse(
            (ROOT / "llm" / "prompts" / "common" / "rule_evidence_contract.md").exists()
        )

        self.assertFalse(
            (ROOT / "pipeline" / "models" / "rule_response_schema.py").exists()
        )

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

    def test_component_failure_has_no_empty_name_fallback(self) -> None:
        source = (
            ROOT
            / "pipeline"
            / "steps"
            / "step8_post_processing"
            / "build_components_step.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("return_exceptions=True", source)
        self.assertNotIn('names[cid] = ""', source)
        self.assertNotIn("isinstance(result, BaseException)", source)


if __name__ == "__main__":
    unittest.main()
