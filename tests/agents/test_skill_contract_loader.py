# -*- coding: utf-8 -*-
"""spec 079 — SKILL YAML fence 계약의 결정론 파싱·주입 테스트.

계약 (FR-4~FR-6):
- matched SKILL 의 fence 절은 LLM 산출과 무관하게 파싱값으로 주입된다.
- fence 부재/스킬 미매칭이면 기존 산출 유지.
- 깨진 fence YAML 은 조용히 넘기지 않고 SkillCatalogError.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.contracts import ScanResult
from agents.rule_compiler import compile_exclusion_rules, compile_rules, split_rules
from agents.scan import _finalize, _hydrate_skill_contract
from agents.skills.catalog import SkillCatalogError, load_skill_contract
from graph.schema.graph_nodes import CodeNode, Table


def _draft(matched_skill: str | None, **overrides) -> ScanResult:
    values = {
        "matched_skill": matched_skill,
        "confidence": 0.5,
        "evidence": "source evidence",
        "evidence_locations": ["a.c:1", "a.c:2", "a.c:3"],
        "no_match_reason": None if matched_skill else "no framework pattern",
    }
    values.update(overrides)
    return ScanResult(**values)


class LoadSkillContractTest(unittest.TestCase):

    def test_proframe_real_skill_parses_expected_contract(self):
        # 실물 SKILL.md 고정 — 받아쓰기 없이 파싱만으로 계약 전체가 나온다.
        contract = load_skill_contract("proframe-c-pfm-dbio")
        self.assertEqual(
            ["proframe_mpfmdbio"],
            [r["id"] for r in contract["table_access_rules"]],
        )
        self.assertEqual(
            ["proframe_mpfmdbio_sequence"],
            [r["id"] for r in contract["table_access_exclusion_rules"]],
        )
        self.assertNotIn("본 테이블 READ로 단일화", contract["codebase_notes"])
        self.assertEqual(
            [{"wrapper": "mpfm_dlcall", "callee_from": "first_arg_string"}],
            contract["call_wrappers"],
        )
        self.assertEqual("as_is", contract["ddl_transform"]["ddl_naming_rule"])
        self.assertIn("PFM_TRY", contract["framework_runtime_functions"])
        # spec 116: 분기표 [handler] 행의 원천 — 프레임워크 지식은 SKILL 카드가 소유
        self.assertEqual(["PFM_CATCH"], contract["handler_markers"])
        # spec 116 2단계: 값 추적용 API 계약 — 문서 보증 사실만
        api_names = [entry["name"] for entry in contract["api_contracts"]]
        self.assertEqual(["mpfmdbio", "mpfm_dlcall"], api_names)
        self.assertEqual("RC_NRM", contract["api_contracts"][0]["success_value"])
        self.assertIn(
            "PFM_TRY(expr)는 expr 결과가 RC_NRM이 아니면",
            contract["codebase_notes"],
        )

    def test_missing_skill_returns_empty(self):
        self.assertEqual({}, load_skill_contract("no-such-skill"))

    def test_document_without_fence_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            skills = Path(td)
            (skills / "plain").mkdir()
            (skills / "plain" / "SKILL.md").write_text(
                "# SKILL: plain\n\nfence 없는 문서.\n", encoding="utf-8",
            )
            self.assertEqual({}, load_skill_contract("plain", skills_dir=skills))

    def test_broken_fence_yaml_raises(self):
        with tempfile.TemporaryDirectory() as td:
            skills = Path(td)
            (skills / "broken").mkdir()
            (skills / "broken" / "SKILL.md").write_text(
                "# SKILL: broken\n\n```yaml\ntable_access_patterns: [unclosed\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SkillCatalogError):
                load_skill_contract("broken", skills_dir=skills)


class HydrateSkillContractTest(unittest.IsolatedAsyncioTestCase):

    def test_hydrates_before_final_validation_without_llm_copy(self):
        result = _hydrate_skill_contract(_draft(
            "proframe-c-pfm-dbio",
            ddl_transform={"ddl_naming_rule": "snake_to_camel", "fallback_prefixes": []},
        ))

        self.assertEqual(1, len(result.table_access_rules))
        self.assertEqual(1, len(result.table_access_exclusion_rules))
        compilable, _ = split_rules(result.table_access_rules)
        patterns = compile_rules(compilable)
        exclusions = compile_exclusion_rules(result.table_access_exclusion_rules)
        self.assertEqual(1, len(patterns))
        self.assertEqual(1, len(exclusions))
        for pattern in patterns:
            self.assertIn("(?P<entity>", pattern.call_regex)   # 죽은 규칙 구조적 차단
        self.assertEqual("mpfm_dlcall", result.call_wrappers[0].wrapper)
        self.assertEqual("as_is", result.ddl_transform.ddl_naming_rule)
        self.assertIn("PFM_TRY", result.framework_runtime_functions)
        self.assertEqual(["PFM_CATCH"], result.handler_markers)
        self.assertIn("PFM_CATCH", result.codebase_notes)

    async def test_finalize_preserves_every_hydrated_skill_fact(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "sample.c"
            source.write_text(
                'void f(void) {\n'
                '  mpfmdbio("orders_s0001");\n'
                '  mpfmdbio("ticket_s_s0001");\n'
                '}\n',
                encoding="utf-8",
            )
            hydrated = _hydrate_skill_contract(_draft("proframe-c-pfm-dbio"))
            result = await _finalize(
                hydrated,
                Path(td),
                [CodeNode(
                    id="code:f", signature="f", name="f", file_path=str(source),
                    start_line=1, end_line=4, label="FUNCTION",
                )],
                [Table(db="d", schema="s", name="orders")],
            )

        self.assertEqual(["PFM_CATCH"], result.handler_markers)
        self.assertEqual("RC_NRM", result.api_contracts[0].success_value)
        self.assertEqual(1, len(result.table_access_exclusion_rules))

    def test_no_matched_skill_keeps_agent_output(self):
        draft = _draft(None)
        self.assertIs(draft, _hydrate_skill_contract(draft))

    def test_unknown_matched_skill_fails_instead_of_using_empty_contract(self):
        with self.assertRaisesRegex(ValueError, "contract가 없음"):
            _hydrate_skill_contract(_draft("not-in-catalog"))

    def test_absent_skill_keys_clear_agent_injections(self):
        injected = _draft(
            "minimal-skill",
            table_access_exclusion_rules=[{
                "kind": "wrapper_key", "id": "invented", "wrapper": "x",
                "entity_suffix": "_fake", "separator": "_",
                "op_tokens": ["r"], "suffix_digits": 1,
            }],
            framework_runtime_functions=["invented_*"],
            handler_markers=["INVENTED_HANDLER"],
            api_contracts=[{"name": "invented_api", "arg_roles": {}}],
            codebase_notes="invented notes",
        )
        with patch(
            "agents.scan.load_skill_contract",
            return_value={"codebase_notes": "trusted notes"},
        ):
            result = _hydrate_skill_contract(injected)

        self.assertEqual([], result.table_access_exclusion_rules)
        self.assertEqual([], result.framework_runtime_functions)
        self.assertEqual([], result.handler_markers)
        self.assertEqual([], result.api_contracts)
        self.assertEqual("trusted notes", result.codebase_notes)

    def test_unmatched_scan_cannot_invent_non_table_exclusions(self):
        with self.assertRaisesRegex(ValueError, "matched SKILL"):
            _draft(
                None,
                table_access_exclusion_rules=[{
                    "kind": "wrapper_key", "id": "invented", "wrapper": "x",
                    "entity_suffix": "_fake", "separator": "_",
                    "op_tokens": ["r"], "suffix_digits": 1,
                }],
            )

    def test_unmatched_empty_output_requires_explicit_reason(self):
        with self.assertRaisesRegex(ValueError, "no_match_reason"):
            ScanResult(
                matched_skill=None,
                confidence=0.5,
                evidence="source evidence",
                evidence_locations=["a.c:1", "a.c:2", "a.c:3"],
            )

    def test_three_evidence_locations_are_enforced_by_model(self):
        with self.assertRaisesRegex(ValueError, "3개 이상"):
            ScanResult(
                matched_skill="proframe-c-pfm-dbio",
                confidence=0.5,
                evidence="source evidence",
                evidence_locations=["a.c:1", "a.c:2"],
            )


if __name__ == "__main__":
    unittest.main()
