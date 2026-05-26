"""온톨로지 로더 — antlr node type 의 역할 / Neo4j 라벨 분류 + stereotype 검증.

``ontology_roles.yaml`` 단일 진실:
- ``roles``    : 언어별 node type → 역할 (module/data_type/function/variable/constant/internal).
- ``paradigm`` : 언어 → oop / procedural.
- ``framework`` / ``dbms`` : LLM stereotype 풀.

node 분류 코드가 yaml 을 직접 읽지 않고 이 모듈만 거치게 한다 (단일 진입점).
DBMS(PL/SQL)는 sub-label 없음 — ``roles`` 는 Framework(java/c/python) 전용.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_ONTOLOGY_PATH = Path(__file__).resolve().parent / "ontology_roles.yaml"

# 역할 → Neo4j primary 라벨 (그래프 노드가 되는 역할만)
_ROLE_TO_LABEL: dict[str, str] = {
    "module":    "MODULE",
    "data_type": "DATA_TYPE",
    "function":  "FUNCTION",
    "variable":  "VARIABLE",
    "constant":  "CONSTANT",
}


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


@lru_cache(maxsize=None)
def primary_label_of(node_type: str, lang: str) -> str | None:
    """node type → Neo4j primary 라벨 (MODULE/DATA_TYPE/FUNCTION/VARIABLE/CONSTANT).

    internal·import·미분류는 None — 그래프 노드로 박지 않는다.
    """
    role = role_of(node_type, lang)
    return _ROLE_TO_LABEL.get(role) if role else None


@lru_cache(maxsize=None)
def sub_label_of(node_type: str, lang: str) -> str | None:
    """Framework sub-label — node type 이 primary 라벨과 다르면 그 node type.

    primary 와 같으면 None (단일 라벨). 예: ``METHOD`` → "METHOD" (:FUNCTION:METHOD),
    C ``FUNCTION`` → None (:FUNCTION 단일 라벨).
    """
    primary = primary_label_of(node_type, lang)
    if primary is None or node_type == primary:
        return None
    return node_type


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
