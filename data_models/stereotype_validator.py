"""stereotype 검증 — ``ontology_roles.yaml`` 의 풀과 매칭.

LLM 이 자유로운 stereotype 작성 시도하면 빈 문자열로 강제 (환각 차단).
strategy 별 풀 분리 (framework / dbms).

새 stereotype 추가 = ``ontology_roles.yaml`` 의 ``framework`` 또는 ``dbms`` 풀에 추가.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


_ONTOLOGY_PATH = Path(__file__).resolve().parent / "ontology_roles.yaml"


@lru_cache(maxsize=1)
def _load_pools() -> dict[str, set[str]]:
    """``ontology_roles.yaml`` 로딩 → {strategy: {stereotype 풀}}."""
    if not _ONTOLOGY_PATH.is_file():
        return {"framework": set(), "dbms": set()}
    data = yaml.safe_load(_ONTOLOGY_PATH.read_text(encoding="utf-8")) or {}
    return {
        "framework": set(data.get("framework", []) or []),
        "dbms": set(data.get("dbms", []) or []),
    }


def is_valid_stereotype(stereotype: str, strategy: str) -> bool:
    """LLM 응답의 stereotype 이 strategy 풀에 속하는지."""
    if not stereotype:
        return True   # 빈 값은 유효 (모호한 케이스)
    return stereotype in _load_pools().get(strategy, set())


def coerce_stereotype(stereotype: str, strategy: str) -> str:
    """풀에 없으면 빈 문자열로 강제."""
    return stereotype if is_valid_stereotype(stereotype, strategy) else ""
