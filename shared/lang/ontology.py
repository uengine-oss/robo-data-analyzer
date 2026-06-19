"""온톨로지 로더 — antlr node type 의 역할 분류 + stereotype 검증.

``ontology_roles.yaml`` 단일 진실:
- ``roles``    : 언어별 node type → 역할 (module/data_type/function/variable/constant/internal).
- ``paradigm`` : 언어 → oop / procedural.
- ``framework`` / ``dbms`` : LLM stereotype 풀.

★Neo4j 라벨 = 노드의 antlr type 그대로 (reader 가 ``node["type"]`` 직결). ontology 는
**분류 전용** — ``role_of`` 가 "어느 dataclass / 어느 엣지 / internal(노드X)"를 정한다 (라벨엔 안 씀).
``roles`` 의 언어별 매핑이 module 기준 등 언어차를 흡수 (java FILE=internal · c FILE=module).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_ONTOLOGY_PATH = Path(__file__).resolve().parent / "ontology_roles.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    """ontology_roles.yaml 1회 로딩. 파일 없으면 즉시 실패 (silent 차단)."""
    if not _ONTOLOGY_PATH.is_file():
        raise FileNotFoundError(f"ontology_roles.yaml 없음: {_ONTOLOGY_PATH}")
    return yaml.safe_load(_ONTOLOGY_PATH.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=None)
def role_of(node_type: str, lang: str) -> str | None:
    """antlr node type → 역할 (module/data_type/function/variable/constant/internal).

    매칭 없으면 None — DBMS node type 이거나 yaml 미등록 타입.
    """
    for role, langs in (_load().get("roles") or {}).items():
        if node_type in (langs.get(lang) or []):
            return role
    return None


def is_internal(node_type: str, lang: str) -> bool:
    """Neo4j 노드로 안 박는 타입인지 (FUNCTION_CALL/IMPORT/FILE 컨테이너 등)."""
    return role_of(node_type, lang) == "internal"


@lru_cache(maxsize=None)
def paradigm(lang: str) -> str | None:
    """언어 → "oop" / "procedural". 미등록은 None."""
    for para, langs in (_load().get("paradigm") or {}).items():
        if lang in (langs or []):
            return para
    return None


# ─────────────────────────────────────────────
# stereotype 검증 — LLM 환각 차단
# ─────────────────────────────────────────────

@lru_cache(maxsize=1)
def _stereotype_pools() -> dict[str, frozenset]:
    data = _load()
    return {
        "framework": frozenset(data.get("framework") or []),
        "dbms":      frozenset(data.get("dbms") or []),
    }


def is_valid_stereotype(stereotype: str, strategy: str) -> bool:
    """LLM 응답 stereotype 이 strategy 풀에 속하는지. 빈 값은 유효 (모호 케이스)."""
    if not stereotype:
        return True
    return stereotype in _stereotype_pools().get(strategy, frozenset())


def coerce_stereotype(stereotype: str, strategy: str) -> str:
    """풀에 없는 stereotype 은 빈 문자열로 강제 (환각 차단)."""
    return stereotype if is_valid_stereotype(stereotype, strategy) else ""
