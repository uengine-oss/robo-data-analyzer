# -*- coding: utf-8 -*-
"""spec 079 — verify_table_regex 멱등 가드 테스트.

실측 사례: 약한 모델이 같은 정규식을 55회 재검증해 스캔 호출 한도를 소진.
계약: 동일 call_regex 재호출 시 재계산 없이 캐시 결과 + "재검증 불필요" 안내를
반환한다 (같은 입력 = 결정론 동일 결과).
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agents.tools.validation import make_verify_table_regex_tool
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    LinkOutcome,
    MatchSample,
)


class VerifyTableRegexIdempotentTest(unittest.TestCase):

    def setUp(self):
        # project_root 는 빈 임시 폴더 — 링커가 실제 파일 스캔 없이 즉시 0건 반환.
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)

    def _tool(self):
        return make_verify_table_regex_tool(
            project_root=Path(self._td.name), code_nodes=[], tables=[],
        )

    def test_repeat_call_returns_cached_with_stop_hint(self):
        tool = self._tool()
        regex = r'mpfmdbio\s*\(\s*"(?P<entity>[A-Za-z0-9_]+?)_(?P<op_token>[siud])\d{4}"'
        first = asyncio.run(tool.ainvoke({"call_regex": regex}))
        second = asyncio.run(tool.ainvoke({"call_regex": regex}))
        self.assertNotIn("재검증 불필요", first, "최초 호출은 정상 리포트")
        self.assertIn("재검증 불필요", second, "재호출은 멱등 안내 포함")
        self.assertIn("다음 단계", second, "루프 탈출 지시 포함")
        self.assertTrue(second.endswith(first), "이전 결과를 그대로 재제공")

    def test_different_regex_is_validated_fresh(self):
        tool = self._tool()
        a = asyncio.run(tool.ainvoke({"call_regex": r'foo\("(?P<entity>\w+)"'}))
        b = asyncio.run(tool.ainvoke({"call_regex": r'bar\("(?P<entity>\w+)"'}))
        self.assertNotIn("재검증 불필요", a)
        self.assertNotIn("재검증 불필요", b)

    def test_invalid_inputs_not_cached_as_reports(self):
        tool = self._tool()
        bad = asyncio.run(tool.ainvoke({"call_regex": "no_group("}))
        self.assertIn("MISSING_GROUP", bad)
        again = asyncio.run(tool.ainvoke({"call_regex": "no_group("}))
        self.assertIn("MISSING_GROUP", again, "실격 입력은 매번 같은 실격 사유")

    def test_all_samples_are_reachable_without_rescanning(self):
        tool = self._tool()
        regex = r'foo\("(?P<entity>\w+)"'
        outcome = LinkOutcome(
            matched_sites=21,
            samples=[
                MatchSample(
                    entity=f"table_{index}",
                    op_token="",
                    entity_complete=True,
                    token_recognized=False,
                    resolved_table=f"table_{index}",
                )
                for index in range(21)
            ],
        )
        with patch(
            "agents.tools.validation.RegexTableLinker.link",
            new=AsyncMock(return_value=outcome),
        ) as link:
            first = asyncio.run(tool.ainvoke({"call_regex": regex}))
            second = asyncio.run(tool.ainvoke({"call_regex": regex, "offset": 20}))

        self.assertIn("offset=20", first)
        self.assertIn("table_20", second)
        self.assertEqual(link.await_count, 1)
        self.assertTrue(link.await_args.kwargs["collect_samples"])


if __name__ == "__main__":
    unittest.main()
