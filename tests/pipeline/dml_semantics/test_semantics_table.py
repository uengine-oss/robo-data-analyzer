"""spec 106 — 의미표 로딩·파생·계약 테스트 (T004) + grep 게이트 (T005)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.unittest_function_cases import load_function_cases

from pipeline.dml_semantics import (
    DmlSemanticsError,
    _parse_statement,
    dml_semantics,
    dml_type_of,
)

REPO = Path(__file__).resolve().parents[3]


# ── 로딩·파생 ────────────────────────────────────────────────


def test_core_statements_load():
    sem = dml_semantics()
    assert sem.relation_of("SELECT") == "READS"
    assert sem.relation_of("TRUNCATE") == "WRITES"
    assert sem.relation_of("CREATE") == "WRITES"
    assert sem.relation_of("없는것") is None


def test_dml_type_of_contract():
    """구 derive_dml_type 계약 — 표준 op 는 자기 이름, WITH 는 현행 보존(""), 표 밖은 ""."""
    for op in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
        assert dml_type_of(op) == op
        assert dml_type_of(op.lower()) == op          # 대소문자 무시
    assert dml_type_of("TRUNCATE") == "TRUNCATE"      # 신규 — 이중 누수 해소
    assert dml_type_of("CREATE") == "CREATE"
    assert dml_type_of("WITH") == ""                  # 현행 보존 (research D3)
    assert dml_type_of("DROP") == ""                  # 표 밖 = 비표준 차단
    assert dml_type_of("") == ""
    assert dml_type_of(None) == ""


def test_first_dml_detection():
    sem = dml_semantics()
    assert sem.first_dml("EXECUTE IMMEDIATE 'TRUNCATE TABLE AMS_MIG.TD_AB00008';") == "TRUNCATE"
    assert sem.first_dml("SELECT 1 FROM DUAL") == "SELECT"
    assert sem.first_dml("v_x := 1; RAISE e;") is None
    # CREATE 단독은 감지 금지 — PL/SQL 헤더 오탐 차단 (detect 구문 계약)
    assert sem.first_dml("CREATE OR REPLACE PROCEDURE P AS BEGIN NULL; END;") is None
    assert sem.first_dml("CREATE TABLE T AS SELECT * FROM S") == "CREATE"


def test_write_targets_longest_phrase_first():
    """`DELETE FROM t` 와 `DELETE t` (FROM 생략 — D4) 모두 대상 추출, 이중 매칭 없음."""
    sem = dml_semantics()
    assert sem.write_target_tables("DELETE FROM ORDERS WHERE 1=1") == {"ORDERS": "DELETE"}
    assert sem.write_target_tables("DELETE SGIOS.ARREO_SMS_KAKAO WHERE X=1") == {
        "ARREO_SMS_KAKAO": "DELETE"
    }
    assert sem.write_target_tables("TRUNCATE TABLE AMS_MIG.TD_AB00008") == {
        "TD_AB00008": "TRUNCATE"
    }


def test_collection_method_delete_not_a_target():
    """PL/SQL 컬렉션 메서드 `v_tab.DELETE(i)` — 대상 추출 불발 (D4 회귀 고정)."""
    sem = dml_semantics()
    assert sem.write_target_tables("v_tab.DELETE(3);") == {}
    assert sem.write_target_tables("v_tab.DELETE;") == {}


def test_merge_and_ctas_semantics():
    sem = dml_semantics()
    body = "MERGE INTO T_DST D USING T_SRC S ON (D.ID=S.ID) WHEN MATCHED THEN UPDATE SET D.X=S.X"
    targets = sem.write_target_tables(body)
    assert targets["T_DST"] == "MERGE"
    assert "T_SRC" not in targets                     # USING 소스는 write 대상 아님
    ctas = sem.write_target_tables("CREATE TABLE T_NEW AS SELECT * FROM T_OLD")
    assert ctas == {"T_NEW": "CREATE"}


def test_read_clauses_include_using():
    """C 경로 read 절 — FROM·JOIN·USING (D6: MERGE 소스 READS)."""
    sem = dml_semantics()
    reads = {m.group("entity").upper() for m in sem.read_target_regex.finditer(
        "MERGE INTO A USING B ON (1=1) SELECT X FROM C JOIN D"
    )}
    assert reads == {"B", "C", "D"}


def test_first_statement_c_outer():
    """구 _C_SQL_START_RE 계약 — 첫 토큰 판정, WITH 접두 허용."""
    sem = dml_semantics()
    assert sem.first_statement("SELECT * FROM T") == "SELECT"
    assert sem.first_statement("WITH CTE AS (SELECT 1) SELECT * FROM CTE") == "WITH" or \
        sem.first_statement("WITH CTE AS (SELECT 1) SELECT * FROM CTE") == "SELECT"
    assert sem.first_statement("TRUNCATE TABLE T") == "TRUNCATE"
    assert sem.first_statement("printf log line") is None


# ── 형식 오류 즉시 raise (FR-009 / 계약 4) ───────────────────


def test_invalid_relation_raises():
    with pytest.raises(DmlSemanticsError):
        _parse_statement("BAD", {"relation": "MUTATES"})


def test_writes_without_target_raises():
    with pytest.raises(DmlSemanticsError):
        _parse_statement("BAD", {"relation": "WRITES"})


def test_unknown_field_raises():
    """표는 미니 언어가 아니다 (D7) — 모르는 필드는 오타로 간주하고 즉시 실패."""
    with pytest.raises(DmlSemanticsError):
        _parse_statement("BAD", {"relation": "READS", "callback": "x"})


def test_empty_phrase_raises():
    with pytest.raises(DmlSemanticsError):
        _parse_statement("BAD", {"relation": "WRITES", "target_after": [" "]})


# ── grep 게이트 (T005 / 계약 1 / SC-003) ─────────────────────

_BANNED = [
    "_DML_KW_RE", "_WRITE_TARGET_RE", "_SQL_KEYWORD_RE",
    "_C_SQL_START_RE", "_C_SQL_WRITE_TARGET_RE", "_C_SQL_READ_TARGET_RE",
]
# DML alternation 정의 (예: r"...(SELECT|INSERT|...)") — 의미표 밖 재도입 금지.
_DML_ALTERNATION = re.compile(r"SELECT\|.*INSERT|INSERT\|.*UPDATE\|.*DELETE")


def _production_py_files():
    for base in ("pipeline", "graph", "agents", "search", "api", "shared"):
        yield from (REPO / base).rglob("*.py")


def test_no_dml_knowledge_outside_semantics_table():
    offenders: list[str] = []
    for path in _production_py_files():
        if path.name == "dml_semantics.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for symbol in _BANNED:
            if symbol in text:
                offenders.append(f"{path}:{symbol}")
        if _DML_ALTERNATION.search(text):
            offenders.append(f"{path}:DML-alternation")
    assert offenders == [], f"의미표 밖 DML 지식 재도입: {offenders}"
def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
