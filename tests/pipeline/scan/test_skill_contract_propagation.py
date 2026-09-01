# -*- coding: utf-8 -*-
"""spec 116 §19 — SKILL fence 계약의 스캔 결과 전파 검증.

api_contracts 는 문서 보증 값 사실의 원천이다: fence 가 있으면 결정론 전파,
스킬 미매칭이면 아무것도 만들어내지 않는다(추측 금지).
"""
from __future__ import annotations

import unittest

from agents.agent_contracts import ScanResult
from agents.scan_agent import _hydrate_skill_contract


class ApiContractPropagation(unittest.TestCase):
    def test_proframe_fence_reaches_scan_result(self):
        result = _hydrate_skill_contract(ScanResult(
            matched_skill="proframe",
        ))
        contracts = result.api_contracts
        self.assertTrue(contracts, "proframe api_contracts fence 가 전파되지 않음")
        by_name = {entry.name: entry for entry in contracts}
        self.assertEqual("RC_NRM", by_name["mpfmdbio"].success_value)
        self.assertEqual({0: "sql_key"}, by_name["mpfmdbio"].arg_roles)

    def test_no_matched_skill_creates_nothing(self):
        result = ScanResult(
            matched_skill=None,
            table_access_status="unavailable",
            no_match_reason="no skill",
        )
        self.assertIs(result, _hydrate_skill_contract(result))
        self.assertEqual([], result.api_contracts)


if __name__ == "__main__":
    unittest.main()
