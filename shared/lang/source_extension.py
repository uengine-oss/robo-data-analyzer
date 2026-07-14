"""소스 확장자 → 언어 — **지원 확장자의 단일 정의처** (헌법 III, spec 046 FR-003).

antlr-parser 가 실제로 emit 하는 언어만 여기 있다. Framework 언어는 ``ontology_roles.yaml``
의 roles 키와 일치해야 한다 (java / c / python). **새 언어 추가 = 이 파일 1곳.**

★ 왜 단일 정의처여야 하나 (spec 046 FR-003 — 실제로 갈라져 있던 사고):
``strategy_detector`` 가 자기 목록(``.kt .go .ts .js .rb .rs .php .scala .cpp .hpp`` 포함
14종)을 따로 들고 있어서, ``.kt`` 만 있는 프로젝트가 "framework" 로 판정된 뒤 파싱에서
전 파일 스킵 → **노드 0개짜리 분석이 "성공"으로 끝났다.** 두 곳이 같은 목록을 봐야 한다.
"""
from __future__ import annotations


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
