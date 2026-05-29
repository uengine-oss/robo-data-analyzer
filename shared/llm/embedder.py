"""임베딩 — LLM 영역의 책임.

분석기 step (``embed_step``) 은 ``encode_texts(texts)`` 한 줄만 호출하고, 어디서
어떻게 임베딩을 받는지는 이 모듈이 결정한다. 미래에 GPU 임베딩 서버 (LLM 서버
일부) 가 도입되면 이 모듈 안만 HTTP 클라이언트로 교체 — 호출자 (embed_step)
변경 0.

현재 구현: 로컬 ``sentence-transformers`` 다국어 모델. 모델 로드는 lazy + 1회 캐시.
"""
from __future__ import annotations

from typing import List


# 로컬 다국어 모델 — 한국어 분석 결과 + 영어 코드 식별자 양쪽 처리.
# GPU 임베딩 서버 도입 시 이 상수와 _model 캐시 자체가 사라지고 HTTP 클라이언트로 교체.
_LOCAL_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model = None


def _get_model():
    """sentence-transformers 모델 lazy 로드 + 1회 캐시."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_LOCAL_MODEL_NAME)
    return _model


def encode_texts(texts: List[str]) -> List[List[float]]:
    """텍스트 리스트 → 벡터 리스트. 배치 인코딩 1회 (개수 많을수록 큰 이득).

    빈 리스트 입력 → 빈 리스트 반환 (모델 로드 X).
    """
    if not texts:
        return []
    model = _get_model()
    return model.encode(texts, show_progress_bar=False).tolist()


def describe() -> str:
    """현재 임베딩 backend 사람 가독 표기 — 로그/usage 표시용."""
    return f"local:{_LOCAL_MODEL_NAME}"
