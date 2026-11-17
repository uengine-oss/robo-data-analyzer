"""재채점(CE) 모델을 실을 수 없을 때 — 검색이 멈추지 않고 BM25 점수로 대신한다.

폐쇄망(포스코)에서는 HF Hub 에 닿지 못해 CrossEncoder 로드가 OSError 로 끝난다(2026-11-17 재현).
예전에는 그 예외가 검색 전체를 멈췄다.
"""
from __future__ import annotations

import sys
import types
import unittest

import pytest

pytest.importorskip("rank_bm25")

from search.corpus.search_corpus import Corpus  # noqa: E402
from search.ranking import result_rerank  # noqa: E402
from search.ranking.graph_retriever import Retriever  # noqa: E402
from search.search_config import DEFAULT  # noqa: E402


def _corpus() -> Corpus:
    rows = [
        {"id": "a", "label": "METHOD", "name": "request", "summary": "남은 연차가 부족하면 휴가 신청을 막는다", "stereotype": ""},
        {"id": "b", "label": "METHOD", "name": "getStatus", "summary": "현재 상태 값을 반환한다", "stereotype": ""},
        # BM25 의 IDF 는 문서가 둘뿐이면 0 이 된다 — 실제 코퍼스처럼 몇 개 더 둔다
        {"id": "c", "label": "METHOD", "name": "save", "summary": "요청을 저장소에 저장한다", "stereotype": ""},
        {"id": "d", "label": "METHOD", "name": "deduct", "summary": "잔여 일수에서 사용 일수를 뺀다", "stereotype": ""},
        {"id": "e", "label": "METHOD", "name": "findById", "summary": "아이디로 요청을 찾는다", "stereotype": ""},
    ]
    return Corpus(rows, {}, None)


class RerankerFallbackTest(unittest.TestCase):
    def setUp(self):
        result_rerank._ce_cache.clear()
        result_rerank._ce_unavailable.clear()
        self.calls = 0
        test = self

        class _Offline:
            def __init__(self, *a, **k):
                test.calls += 1
                raise OSError("We couldn't connect to 'https://huggingface.co' to load the files")

        self._saved = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = types.SimpleNamespace(CrossEncoder=_Offline)

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("sentence_transformers", None)
        else:
            sys.modules["sentence_transformers"] = self._saved
        result_rerank._ce_cache.clear()
        result_rerank._ce_unavailable.clear()

    def test_모델을_못_실으면_BM25_점수로_대신한다(self):
        r = Retriever(session=None, corpus=_corpus())
        scores = r._ce("연차 부족 휴가 신청", ["a", "b"], DEFAULT)
        self.assertEqual(set(scores), {"a", "b"})
        self.assertEqual(scores["a"], 1.0)          # 후보 안 최댓값으로 나눈 0..1
        self.assertLess(scores["b"], scores["a"])

    def test_실패는_기억해서_요청마다_다시_받으려_하지_않는다(self):
        r = Retriever(session=None, corpus=_corpus())
        r._ce("휴가", ["a", "b"], DEFAULT)
        r._ce("휴가 신청", ["a", "b"], DEFAULT)
        self.assertEqual(self.calls, 1)
