"""spec 080 — 반복 폭주 실시간 차단의 감지·오탐 계약 검증.

폭주 3유형(공백/짧은 단위/문구 조합 루프)은 반드시 감지하고, 정상 출력(구조 키가
반복되지만 내용이 다른 JSON, 장문 한국어, 창 미만 소형 출력)은 절대 발동하지 않는다.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from llm.repetition_guard import (
    RepetitionDetector,
    RepetitionGuardCallback,
    RepetitionLoopAborted,
)


def _feed_all(detector: RepetitionDetector, text: str, chunk_size: int = 64):
    """청크 단위 주입 — 최초 감지 주기를 반환 (미감지 None)."""
    for i in range(0, len(text), chunk_size):
        hit = detector.feed(text[i : i + chunk_size])
        if hit is not None:
            return hit
    return None


class RepetitionDetectorTest(unittest.TestCase):
    def test_whitespace_flood_triggers_period_1(self):
        # 사건 A 유형: `{` 뒤 공백 무한 — 주기 1 로 감지돼야 한다.
        hit = _feed_all(RepetitionDetector(), "{" + " " * 5000)
        self.assertEqual(hit, 1)

    def test_whitespace_flood_stops_before_the_general_period_window(self):
        detector = RepetitionDetector()
        hit = _feed_all(detector, "{" + " " * 128, chunk_size=8)
        self.assertEqual(hit, 1)
        # 스트리밍 청크 안에서는 최대 chunk_size-1자만큼 임계값을 넘길 수 있다.
        self.assertLessEqual(
            detector.chars_seen,
            1 + detector.WHITESPACE_FLOOD + 7,
        )
        self.assertLess(detector.chars_seen, detector.WINDOW)

    def test_normal_json_formatting_whitespace_does_not_trigger(self):
        detector = RepetitionDetector()
        text = '{\n  "rules": [\n    {"statement": "정상", "examples": []}\n  ]\n}'
        self.assertIsNone(_feed_all(detector, text, chunk_size=3))

    def test_nested_json_indentation_below_tight_boundary_does_not_trigger(self):
        detector = RepetitionDetector()
        indent = " " * (detector.WHITESPACE_FLOOD - 1)
        text = '{\n' + indent + '"rules": []\n}'
        self.assertIsNone(_feed_all(detector, text, chunk_size=5))

    def test_post_root_whitespace_stops_at_tight_boundary(self):
        detector = RepetitionDetector()
        hit = _feed_all(
            detector,
            '{"rules":[]}' + " " * detector.POST_ROOT_WHITESPACE_FLOOD,
            chunk_size=1,
        )
        self.assertEqual(hit, 1)
        self.assertLess(detector.chars_seen, detector.WHITESPACE_FLOOD)

    def test_whitespace_inside_json_string_is_not_a_flood(self):
        detector = RepetitionDetector()
        text = '{"value":"' + " " * (detector.WHITESPACE_FLOOD + 16) + '"}'
        self.assertIsNone(_feed_all(detector, text, chunk_size=7))

    def test_braces_and_escapes_inside_string_do_not_close_root(self):
        detector = RepetitionDetector()
        text = '{"value":"escaped \\\\\" } ] still text"}'
        self.assertIsNone(_feed_all(detector, text, chunk_size=2))

    def test_short_unit_loop_triggers(self):
        # 사건 B 유형: 유사 별칭 조합의 정확 반복.
        unit = "주민일련번호, 주민번호 부분정보, 주민등록번호; "
        hit = _feed_all(RepetitionDetector(), unit * 300)
        self.assertIsNotNone(hit)
        self.assertLessEqual(hit, len(unit))

    def test_long_unit_loop_triggers(self):
        # 긴 문장 단위(수백 자) 루프 — MAX_PERIOD(512) 이내면 감지.
        unit = ("이 함수는 계좌번호 유효성을 검증하고 실패 시 오류 코드를 반환한다. " * 5)[:400]
        hit = _feed_all(RepetitionDetector(), unit * 30)
        self.assertIsNotNone(hit)

    def test_chunk_boundary_invariance(self):
        # 1자씩 넣어도 동일하게 감지 — 스트리밍 청크 크기에 무관해야 한다.
        text = "abc반복 " * 1000
        hit_big = _feed_all(RepetitionDetector(), text, chunk_size=4096)
        hit_tiny = _feed_all(RepetitionDetector(), text, chunk_size=1)
        self.assertIsNotNone(hit_big)
        self.assertEqual(hit_big, hit_tiny)

    def test_distinct_json_items_do_not_trigger(self):
        # 정상 구조화 출력: 키는 반복되지만 내용이 항목마다 달라 정확 주기가 될 수 없다.
        items = ",".join(
            f'{{"statement": "규칙 {i}: 계좌 유형 {i % 7} 과 납부방법 {i % 5} 를 검증한다"}}'
            for i in range(240)
        )
        text = f'{{"rules": [{items}]}}'
        self.assertGreater(len(text), RepetitionDetector.WINDOW * 2)
        self.assertIsNone(_feed_all(RepetitionDetector(), text))

    def test_varied_prose_does_not_trigger(self):
        text = " ".join(
            f"{i}번째 문장은 서로 다른 업무 흐름과 조건 분기 {i * 13 % 97} 을 설명한다."
            for i in range(300)
        )
        self.assertIsNone(_feed_all(RepetitionDetector(), text))

    def test_below_window_non_whitespace_period_never_triggers(self):
        # 일반 주기 검사는 창 미만에서 시작하지 않는다. 공백 폭주만 별도 조기 계약이다.
        detector = RepetitionDetector()
        text = ("abcde" * RepetitionDetector.WINDOW)[: RepetitionDetector.WINDOW - 100]
        self.assertIsNone(_feed_all(detector, text))

    def test_empty_feed_is_noop(self):
        detector = RepetitionDetector()
        self.assertIsNone(detector.feed(""))
        self.assertEqual(detector.chars_seen, 0)


class RepetitionGuardCallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_raises_on_loop_and_declares_raise_error(self):
        # raise_error=True 가 아니면 LangChain 콜백 매니저가 예외를 삼킨다 — 계약 고정.
        self.assertTrue(RepetitionGuardCallback.raise_error)
        guard = RepetitionGuardCallback("rule:test")
        with self.assertRaises(RepetitionLoopAborted) as ctx:
            for _ in range(200):
                await guard.on_llm_new_token(" " * 64)
        self.assertEqual(ctx.exception.period, 1)
        self.assertEqual(ctx.exception.label, "rule:test")

    async def test_normal_tokens_pass_through(self):
        guard = RepetitionGuardCallback("rule:test")
        for i in range(200):
            await guard.on_llm_new_token(f"서로 다른 토큰 {i} ")
        self.assertGreater(guard.detector.chars_seen, 0)

    async def test_abort_carries_partial_text_for_diagnosis(self):
        # spec 116 0단계: 중단 예외가 수신 tail 을 실어야 에러 기록이 response=None 으로
        # 남지 않는다. 폭주 내용(반복 패턴)이 partial 에 실제로 보여야 한다.
        guard = RepetitionGuardCallback("rule:test")
        with self.assertRaises(RepetitionLoopAborted) as ctx:
            await guard.on_llm_new_token('{"k": "v"')
            for _ in range(200):
                await guard.on_llm_new_token(" " * 64)
        self.assertTrue(ctx.exception.partial_text)
        self.assertIn('{"k": "v"', ctx.exception.partial_text)

    async def test_tool_call_argument_deltas_are_guarded(self):
        guard = RepetitionGuardCallback("rule:tool")
        with self.assertRaises(RepetitionLoopAborted) as ctx:
            for _ in range(200):
                chunk = SimpleNamespace(
                    text="",
                    message=SimpleNamespace(
                        tool_call_chunks=[{"args": " " * 64}],
                        additional_kwargs={},
                    ),
                )
                await guard.on_llm_new_token("", chunk=chunk)
        self.assertEqual(1, ctx.exception.period)
        self.assertGreaterEqual(ctx.exception.chars_seen, 32)

    async def test_direct_ai_chunk_tool_arguments_are_guarded(self):
        guard = RepetitionGuardCallback("rule:tool-direct")
        chunk = SimpleNamespace(
            text="",
            tool_call_chunks=[{"args": '{"value":"ok"}'}],
            additional_kwargs={},
        )
        await guard.on_llm_new_token("", chunk=chunk)
        self.assertIn('{"value":"ok"}', guard.detector.tail_text)
