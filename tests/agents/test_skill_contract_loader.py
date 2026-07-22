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

from agents.skills.catalog import SkillCatalogError, load_skill_contract
from pipeline.steps.step4_scan_project.scan_codebase_step import _apply_skill_contract


class LoadSkillContractTest(unittest.TestCase):

    def test_proframe_real_skill_parses_expected_contract(self):
        # 실물 SKILL.md 고정 — 받아쓰기 없이 파싱만으로 계약 전체가 나온다.
        contract = load_skill_contract("proframe-c-pfm-dbio")
        self.assertEqual(
            ["proframe_mpfmdbio", "proframe_mpfmdbio_seq"],
            [p["id"] for p in contract["table_access_patterns"]],
        )
        self.assertEqual(
            [{"wrapper": "mpfm_dlcall", "callee_from": "first_arg_string"}],
            contract["call_wrappers"],
        )
        self.assertEqual("as_is", contract["ddl_transform"]["ddl_naming_rule"])

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


class ApplySkillContractTest(unittest.TestCase):

    def test_overrides_agent_output_with_fence_values(self):
        # Agent 가 빈 배열로 "받아 적기 누락"한 상황 — 로더가 덮어써야 한다.
        result = {
            "matched_skill": "proframe-c-pfm-dbio",
            "table_access_patterns": [],
            "call_wrappers": [],
            "ddl_transform": {"ddl_naming_rule": "snake_to_camel", "fallback_prefixes": []},
        }
        _apply_skill_contract(result)
        self.assertEqual(2, len(result["table_access_patterns"]))
        self.assertEqual("mpfm_dlcall", result["call_wrappers"][0]["wrapper"])
        self.assertEqual("as_is", result["ddl_transform"]["ddl_naming_rule"])

    def test_no_matched_skill_keeps_agent_output(self):
        result = {"matched_skill": None, "table_access_patterns": [], "call_wrappers": []}
        _apply_skill_contract(result)
        self.assertEqual([], result["table_access_patterns"])
        self.assertEqual([], result["call_wrappers"])


if __name__ == "__main__":
    unittest.main()
