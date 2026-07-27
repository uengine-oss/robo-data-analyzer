"""spec 080 — 반복 폭주 실시간 차단의 감지·오탐 계약 검증.

폭주 3유형(공백/짧은 단위/문구 조합 루프)은 반드시 감지하고, 정상 출력(구조 키가
반복되지만 내용이 다른 JSON, 장문 한국어, 창 미만 소형 출력)은 절대 발동하지 않는다.
"""
from __future__ import annotations

import unittest

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
        hit = _feed_all(detector, "{" + " " * 700)
        self.assertEqual(hit, 1)
        self.assertLess(detector.chars_seen, detector.WINDOW)

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
