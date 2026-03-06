"""Framework 공통 유틸리티. FQN 분할, 중복 제거, 키 매칭 등 순수 함수."""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Set, Tuple


def split_fqn(fqn: str) -> Tuple[str, str]:
    """'com.a.Foo.bar' -> ('com.a.Foo', 'bar'). dot 없으면 (fqn, '')."""
    if not fqn or "." not in fqn:
        return (fqn or "", "")
    return fqn.rsplit(".", 1)


def deduplicate(items: list, key_fn: Callable) -> list:
    """key_fn 기준 중복 제거, 순서 유지."""
    seen: set = set()
    result = []
    for item in items:
        k = key_fn(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result


def is_valid_table(schema: str, name: str) -> bool:
    """테이블명 유효성 검사. excluded names 제외."""
    from analyzer.strategy.framework.common.constants import EXCLUDED_TABLE_NAMES
    return bool(name) and name.strip().lower() not in EXCLUDED_TABLE_NAMES


def key_in_code(code: str, key: str) -> bool:
    """인덱스 키가 코드에 등장하는지 검사. key 또는 'key' / "key" 형태."""
    if not code or not key:
        return False
    if key in code:
        return True
    if ('"' + key + '"') in code or ("'" + key + "'") in code:
        return True
    return False


def sql_op_to_method_relation(sql_relation: str) -> str:
    """SqlStatement->Table 연산(SELECT/INSERT/UPDATE/DELETE)을 Method->Table 관계(READS/WRITES)로 변환."""
    r = (sql_relation or "").strip().upper()
    if r in ("INSERT", "UPDATE", "DELETE"):
        return "WRITES"
    return "READS"


def external_callee_class(callee_method_id: str) -> Optional[str]:
    """external:ClassName#methodName -> ClassName. 아니면 None."""
    from analyzer.strategy.framework.common.constants import EXTERNAL_PREFIX, EXTERNAL_SEP
    s = (callee_method_id or "").strip()
    if not s.startswith(EXTERNAL_PREFIX):
        return None
    rest = s[len(EXTERNAL_PREFIX):]
    if EXTERNAL_SEP in rest:
        return rest.split(EXTERNAL_SEP, 1)[0].strip() or None
    return rest.strip() or None


def infer_table_name_from_entity_fqn(fqn: str) -> str:
    """자바 엔티티 FQN에서 테이블명 추론. CollateralBean -> COLLATERAL."""
    import re
    if not fqn or not fqn.strip():
        return ""
    simple = fqn.strip().rsplit(".", 1)[-1] if "." in fqn else fqn.strip()
    for suffix in ("Bean", "Local", "LocalHome", "DTO", "Entity", "Impl"):
        if simple.endswith(suffix) and len(simple) > len(suffix):
            simple = simple[: -len(suffix)]
            break
    if not simple:
        return ""
    return re.sub(r"([A-Z])", r"_\1", simple).strip("_").upper() or simple.upper()
