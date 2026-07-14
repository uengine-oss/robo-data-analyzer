"""온톨로지 로더 — antlr node type 의 역할 분류 + stereotype 검증.

``ontology_roles.yaml`` 단일 진실:
- ``roles``    : 언어별 node type → 역할 (module/data_type/function/variable/constant/internal).
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


# ── 루틴(호출 가능 단위) 라벨 — **단일 정의처** (헌법 III / spec 046 FR-005) ──
# 라벨 = antlr type 이라 종류 집합. HAS_MEMBER(그릇→멤버) 자격 + 배치 계획의 루틴 판정에 쓴다.
# 일반 구문(SELECT/IF/LOOP…)·구조 노드(DECLARE/TRY…)는 루틴이 아니다.
#
# ★ 종전엔 antlr_json_reader(_ROUTINE_LABELS) 와 batch_analyze(_ROUTINE_TYPES) 가 목록을
#   따로 들었고 **실제로 갈라져 있었다** — batch_analyze 쪽엔 ``METHOD`` 가 빠져 있었다.
#   (DBMS 전용 경로라 지금은 무해했지만, 같은 개념의 정의가 두 벌인 것 자체가 결함.)
ROUTINE_LABELS: frozenset[str] = frozenset({"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"})


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


# (옛 ``is_internal`` / ``paradigm`` 삭제 — 호출부 0건, 4개 레포 전수 확인. spec 046 FR-010.
#  소비처는 ``role_of(...) == "internal"`` 을 직접 비교한다. yaml 의 ``paradigm:`` 섹션도
#  이 죽은 함수만 읽던 죽은 데이터라 함께 제거.)


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
