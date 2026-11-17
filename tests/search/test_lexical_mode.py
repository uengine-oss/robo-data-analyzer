"""임베딩 없는 그래프(키 없는 Lite)의 검색 — BM25 점수가 CE · 코사인 자리를 대신한다.

원래 고침: 98655784 (feat/claude-code-bridge). main 의 search_corpus · graph_retriever 에 옮겼다.
"""
from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

from search.corpus import search_corpus
from search.corpus.search_corpus import Corpus, SearchUnavailable, load_corpus
from search.ranking.graph_retriever import Retriever

_HAS_BM25 = importlib.util.find_spec("rank_bm25") is not None

ROWS = [
    {"id": "close", "label": "FUNCTION", "name": "close",
     "summary": "가게를 영업 종료 상태로 바꾸고 접수 대기 주문을 모두 거절한다",
     "stereotype": "", "embedded": False, "dimension": None},
    {"id": "reject", "label": "FUNCTION", "name": "rejectOrder",
     "summary": "사장님이 접수 대기 주문을 거절한다",
     "stereotype": "", "embedded": False, "dimension": None},
    {"id": "menu", "label": "FUNCTION", "name": "listMenu",
     "summary": "가게 메뉴 목록을 이름순으로 조회한다",
     "stereotype": "", "embedded": False, "dimension": None},
]


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return [dict(row) for row in self._rows]


class _Session:
    def run(self, query, **params):
        if "CALLS" in query:
            return _Result([])
        return _Result(ROWS)


@unittest.skipUnless(_HAS_BM25, "rank_bm25 not installed")
class LexicalModeTest(unittest.TestCase):
    def test_lexical_retriever_scores_by_bm25_without_vectors(self):
        retriever = Retriever(session=None, corpus=Corpus(ROWS, {}, None, lexical=True))
        self.assertTrue(retriever.lexical)
        self.assertIsNone(retriever._qv("아무 질의"))
        self.assertEqual(retriever._cos("close", None), 0.0)
        retriever._ensure_vecs(["close"])  # 세션을 건드리지 않는다
        scores = retriever._ce(
            "영업 종료 시 접수 대기 주문 일괄 거절", ["close", "reject", "menu"], cfg=None,
        )
        self.assertEqual(scores["close"], 1.0)
        self.assertGreater(scores["close"], scores["reject"])
        self.assertGreater(scores["reject"], scores["menu"])

    def test_vector_corpus_is_not_lexical(self):
        self.assertFalse(Retriever(session=None, corpus=Corpus(ROWS, {}, None)).lexical)

    def test_embeddings_disabled_loads_lexical_corpus(self):
        with patch.object(search_corpus, "lexical_allowed", return_value=True):
            corpus = load_corpus(session=_Session())
        self.assertTrue(corpus.lexical)
        self.assertEqual(corpus.ids, ["close", "reject", "menu"])

    def test_embeddings_enabled_still_requires_vectors(self):
        with patch.object(search_corpus, "lexical_allowed", return_value=False):
            with self.assertRaises(SearchUnavailable):
                load_corpus(session=_Session())


if __name__ == "__main__":
    unittest.main()
