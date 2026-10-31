"""임베딩 없는 그래프(키 없는 Lite)의 검색 — BM25 점수가 CE · 코사인 자리를 대신한다."""
from search.corpus.corpus import Corpus
from search.ranking.retriever import Retriever

ROWS = [
    {"id": "close", "name": "close", "logical_name": "가게 영업 종료",
     "summary": "가게를 영업 종료 상태로 바꾸고 접수 대기 주문을 모두 거절한다", "code_text": ""},
    {"id": "reject", "name": "rejectOrder", "logical_name": "주문 거절",
     "summary": "사장님이 접수 대기 주문을 거절한다", "code_text": ""},
    {"id": "menu", "name": "listMenu", "logical_name": "메뉴 조회",
     "summary": "가게 메뉴 목록을 이름순으로 조회한다", "code_text": ""},
]


def test_lexical_retriever_scores_by_bm25_without_vectors():
    r = Retriever(session=None, corpus=Corpus(ROWS, {}, None, lexical=True))
    assert r.lexical and r._qv("아무 질의") is None
    assert r._cos("close", None) == 0.0
    r._ensure_vecs(["close"])   # 세션을 건드리지 않는다
    scores = r._ce("영업 종료 시 접수 대기 주문 일괄 거절", ["close", "reject", "menu"], cfg=None)
    assert scores["close"] == 1.0
    assert scores["close"] > scores["reject"] > scores["menu"]


def test_vector_corpus_is_not_lexical():
    assert Retriever(session=None, corpus=Corpus(ROWS, {}, None)).lexical is False
