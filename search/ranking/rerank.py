"""검색 크로스인코더 재순위화와 결정론적 점수 캐시.

(질의, passage) 쌍을 bge-reranker 가 직접 채점(sigmoid 확률 0..1). 결정론이라 프로세스 내
메모리 캐시(같은 질의 재요청 시 재사용). CE 모델은 DB 무관 → 프로세스 1회 로드.
판정 재료 통일(spec 009): passage 에 search_text 병기(``ROBO_SEM_UNIFIED``, 기본 on — 게이트 승자).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from log.logger import log_warning


_LOG = "search.ranking.rerank"

_SEM_UNIFIED = os.environ.get("ROBO_SEM_UNIFIED", "1") == "1"

_ce_cache: dict = {}
# CE 점수 = 결정론(같은 model·질의·passage → 항상 같은 점수) → 디스크 캐시로 재측정·반복질의 0초.
# 첫 채점만 CPU 비용 지불, 이후 재사용. logs/ 밖 전용 디렉토리(plan §C).
_CACHE_PATH = Path(__file__).resolve().parent.parent / ".search_cache" / "ce_scores.json"
_score_cache: dict | None = None


def _load_scores() -> dict:
    global _score_cache
    if _score_cache is None:
        try:
            _score_cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _score_cache = {}
        except (OSError, json.JSONDecodeError) as error:
            log_warning(
                _LOG, "load_score_cache", "CE 점수 캐시를 읽지 못해 빈 캐시로 시작",
                path=str(_CACHE_PATH), error=f"{type(error).__name__}: {error}",
            )
            _score_cache = {}
    return _score_cache


def _save_scores(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except OSError as error:
        log_warning(
            _LOG, "save_score_cache", "CE 점수 캐시 적재 실패 — 현재 검색 결과는 유지",
            path=str(_CACHE_PATH), error=f"{type(error).__name__}: {error}",
        )


def get_ce(model_name: str):
    """CE 모델 1회 로드(프로세스 전역 — 모델이지 DB 상태 아님)."""
    if model_name not in _ce_cache:
        # GPU 없는 장비에서 torch 스레드를 전 코어로 — CE 채점 4~5배 가속(놀이터 실측, 무료).
        try:
            import os

            import torch
            torch.set_num_threads(os.cpu_count() or 4)
        except (ImportError, OSError, RuntimeError) as error:
            log_warning(
                _LOG, "configure_torch_threads", "Torch 스레드 최적화 실패 — 기본 설정으로 계속",
                error=f"{type(error).__name__}: {error}",
            )
        from sentence_transformers import CrossEncoder

        _ce_cache[model_name] = CrossEncoder(model_name)
    return _ce_cache[model_name]


def predict_cached(model_name: str, pairs: list[tuple[str, str]]) -> list[float]:
    """ce.predict 의 결과중립 디스크 캐시 래퍼. (모델·질의·passage)→점수. 순서 보존."""
    if not pairs:
        return []
    cache = _load_scores()
    out: list[float | None] = [None] * len(pairs)
    miss_idx, miss_pairs, miss_keys = [], [], []
    for i, (q, psg) in enumerate(pairs):
        # model 포함 — ce_model 교체 시 옛 점수 재사용 방지.
        key = hashlib.md5(f"{model_name}\x00{q}\x00{psg}".encode("utf-8")).hexdigest()
        if key in cache:
            out[i] = cache[key]
        else:
            miss_idx.append(i); miss_pairs.append((q, psg)); miss_keys.append(key)
    if miss_pairs:
        ce = get_ce(model_name)
        scores = ce.predict(miss_pairs)
        for i, key, s in zip(miss_idx, miss_keys, scores):
            out[i] = float(s); cache[key] = float(s)
        _save_scores(cache)
    return [float(x) for x in out]


def passage(n: dict) -> str:
    """CE 입력 = 타입 + 이름/논리명 + 요약 + 역할 + (통일 시) search_text. 임베딩과 같은 재료.
    길이 상한 없음 — 자르면 잘린 사실이 채점에서 사라진다."""
    name = n.get("name") or n.get("logical_name") or ""
    stereo = n.get("stereotype") or ""
    parts = [f"[{n.get('label', '')}]"]
    if name:
        parts.append(name)
    parts.append(n.get("summary", ""))
    if stereo:
        parts.append(f"(역할:{stereo})")
    if _SEM_UNIFIED and (n.get("search_text") or "").strip():
        parts.append("검색: " + n["search_text"].replace("\n", " ").strip())
    return " ".join(parts).strip()
