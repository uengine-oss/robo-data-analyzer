"""Framework 공통 유틸리티. FQN 분할, 중복 제거, 키 매칭 등 순수 함수."""

from __future__ import annotations


def key_in_code(code: str, key: str) -> bool:
    """인덱스 키가 코드에 등장하는지 검사.

    따옴표 감싼 형태("key", 'key')를 우선 체크하고,
    따옴표 없는 경우 단어 경계(\b)를 확인하여 부분 매칭 방지.
    예: key="name" → "userName"에 매칭되지 않음.
    """
    if not code or not key:
        return False
    # 1) 따옴표 감싼 형태 (가장 정확)
    if ('"' + key + '"') in code or ("'" + key + "'") in code:
        return True
    # 2) 단어 경계 체크 (짧은 키의 부분 매칭 방지)
    import re
    if re.search(r'\b' + re.escape(key) + r'\b', code):
        return True
    return False


def sql_op_to_method_relation(sql_relation: str) -> str:
    """SqlStatement->Table 연산(SELECT/INSERT/UPDATE/DELETE)을 Method->Table 관계(READS/WRITES)로 변환."""
    r = (sql_relation or "").strip().upper()
    if r in ("INSERT", "UPDATE", "DELETE"):
        return "WRITES"
    return "READS"



