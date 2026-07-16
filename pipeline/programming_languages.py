"""지원 언어 정책의 단일 정의처.

확장자, 분석 전략 감지, AST 역할과 stereotype 검증을 함께 소유한다. 세 기능은 모두
``지원 언어가 무엇인가``라는 같은 변경 이유를 가지며 ``ontology_roles.yaml``을 인접 데이터로
사용한다. antlr-parser가 실제로 emit하는 언어만 등록한다.

★ 왜 단일 정의처여야 하나 (spec 046 FR-003 — 실제로 갈라져 있던 사고):
``strategy_detector`` 가 자기 목록(``.kt .go .ts .js .rb .rs .php .scala .cpp .hpp`` 포함
14종)을 따로 들고 있어서, ``.kt`` 만 있는 프로젝트가 "framework" 로 판정된 뒤 파싱에서
전 파일 스킵 → **노드 0개짜리 분석이 "성공"으로 끝났다.** 두 곳이 같은 목록을 봐야 한다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml


_ONTOLOGY_PATH = Path(__file__).resolve().parent / "ontology_roles.yaml"

# ── Framework 소스: 확장자(점 없음) → 언어 ──
# antlr-parser 가 emit 하고 ontology_roles.yaml 이 roles 를 아는 언어만.
EXT_TO_LANG: dict[str, str] = {
    "java": "java",
    "c":    "c",
    "h":    "c",
    "py":   "python",
}

# ── DBMS 소스 확장자 (점 없음 — EXT_TO_LANG 과 표기 통일) ──
DBMS_EXTS: frozenset[str] = frozenset({"sql", "pks", "pkb", "pls", "ddl"})


# ── 파생 (점 있는 표기 — Path.suffix 비교용). 정의처는 위 두 개뿐이다. ──
FRAMEWORK_SUFFIXES: frozenset[str] = frozenset(f".{e}" for e in EXT_TO_LANG)
DBMS_SUFFIXES: frozenset[str] = frozenset(f".{e}" for e in DBMS_EXTS)


def is_supported(path_suffix: str) -> bool:
    """``Path.suffix`` (예 ``".java"``) 가 분석 가능한 소스인가."""
    s = path_suffix.lower()
    return s in FRAMEWORK_SUFFIXES or s in DBMS_SUFFIXES


class NoSupportedSourceError(RuntimeError):
    """분석 가능한 소스가 하나도 없어 전략을 결정할 수 없다."""


def detect_strategy(project_dir: Path) -> Literal["framework", "dbms"]:
    """프로젝트 확장자로 분석 전략을 결정하며, 지원 소스가 없으면 명시적으로 실패한다."""
    has_framework = False
    has_dbms = False
    for source_path in project_dir.rglob("*"):
        if not source_path.is_file():
            continue
        suffix = source_path.suffix.lower()
        if suffix in FRAMEWORK_SUFFIXES:
            has_framework = True
        elif suffix in DBMS_SUFFIXES:
            has_dbms = True
    if has_framework:
        return "framework"
    if has_dbms:
        return "dbms"
    raise NoSupportedSourceError(
        f"분석 가능한 소스가 없다: {project_dir} — "
        f"지원 확장자 = {sorted(FRAMEWORK_SUFFIXES | DBMS_SUFFIXES)} "
        "(pipeline/programming_languages.py가 단일 정의처)."
    )


# 호출 가능한 AST 루틴 라벨. 일반 제어문과 구조 노드는 포함하지 않는다.
ROUTINE_LABELS: frozenset[str] = frozenset({"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"})


@lru_cache(maxsize=1)
def _load_ontology() -> dict:
    """언어 역할과 stereotype 풀을 한 번 로드한다."""
    if not _ONTOLOGY_PATH.is_file():
        raise FileNotFoundError(f"ontology_roles.yaml 없음: {_ONTOLOGY_PATH}")
    return yaml.safe_load(_ONTOLOGY_PATH.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=None)
def role_of(node_type: str, lang: str) -> str | None:
    """ANTLR 노드 타입의 언어별 역할을 반환한다."""
    for role, languages in (_load_ontology().get("roles") or {}).items():
        if node_type in (languages.get(lang) or []):
            return role
    return None


@lru_cache(maxsize=1)
def _stereotype_pools() -> dict[str, frozenset]:
    data = _load_ontology()
    return {
        "framework": frozenset(data.get("framework") or []),
        "dbms": frozenset(data.get("dbms") or []),
    }


def is_valid_stereotype(stereotype: str, strategy: str) -> bool:
    """LLM stereotype이 전략별 허용 목록에 속하는지 판정한다."""
    return not stereotype or stereotype in _stereotype_pools().get(strategy, frozenset())


def coerce_stereotype(stereotype: str, strategy: str) -> str:
    """허용 목록 밖의 LLM stereotype을 빈 값으로 정규화한다."""
    return stereotype if is_valid_stereotype(stereotype, strategy) else ""
