"""Schema 결정 위계 — 모든 전략 공통.

Table 노드의 schema 가 LLM 결과에서 빈값일 때 채워주는 단일 진입점.

위계:
  1) explicit (LLM 명시 schema) — 코드 진실, 가장 신뢰
  2) name_to_schema lookup (DDL 단일 매칭) — DB 진실
  3) own_schema (DBMS PROCEDURE 자기 schema) — 자기 schema 가정 (휴리스틱)
  4) "" (빈값) — 정직하게 모름 표시 (강제 'public' X)

배제 케이스 (정책):
  같은 이름 테이블이 여러 schema 에 있고 + 코드/own 도 schema 명시 X
  → 자동으로 4번 (빈값) 으로 떨어짐 (lookup 모호 시 빈값)

ENV `DEFAULT_DB_SCHEMA` fallback:
  본 모듈은 ENV 를 보지 않음 — 정확도 우선. DB schema 정보가 정말 필요하면
  datasource/catalog 인프라로 보강 (별도 작업).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Tuple


def build_name_to_schema(
    ddl_table_metadata: Mapping[Tuple[str, str], Any],
) -> Dict[str, str]:
    """DDL 메타에서 name → schema 역인덱스 생성.

    Args:
        ddl_table_metadata: ``{(schema_lower, name_lower): {...}}`` 구조.
            (`analyzer/common/cypher/ddl_handler.py` 가 생성)

    Returns:
        ``{name_lower: schema}`` map. 동명 여러 schema 면 lookup 결과 빈값
        (모호 → 위계 4번으로 떨어짐). schema 빈값이거나 name 빈값이면 제외.
    """
    name_to_schemas: Dict[str, set] = {}
    for (schema_lower, name_lower), meta in ddl_table_metadata.items():
        if not name_lower:
            continue
        # original_schema 가 우선 (case 보존). 없으면 lowered key 사용.
        schema = ""
        if isinstance(meta, dict):
            schema = (meta.get("original_schema") or "").strip()
        if not schema:
            schema = schema_lower
        if not schema:
            continue
        name_to_schemas.setdefault(name_lower, set()).add(schema)

    # 동명 여러 schema 는 모호 → 빈값 매핑 (위계 4번 떨어짐).
    result: Dict[str, str] = {}
    for name_lower, schemas in name_to_schemas.items():
        if len(schemas) == 1:
            result[name_lower] = next(iter(schemas))
        # else: 동명 여러 schema → 매핑 X (lookup 시 빈값 반환되어 위계 다음 단계로)
    return result


def resolve_schema(
    explicit_schema: str,
    table_name: str,
    name_to_schema: Mapping[str, str],
    own_schema: str = "",
) -> str:
    """Schema 결정 위계 적용.

    Args:
        explicit_schema: LLM 결과의 schema (있으면 그대로 신뢰)
        table_name: 테이블 이름 (lookup 키 — case-insensitive)
        name_to_schema: DDL name → schema 역인덱스 (`build_name_to_schema` 결과)
        own_schema: PROCEDURE 등 자기 schema (DBMS only). Framework 는 ""

    Returns:
        결정된 schema. 모르면 "".
    """
    explicit = (explicit_schema or "").strip()
    if explicit:
        return explicit

    name_key = (table_name or "").strip().lower()
    if name_key:
        ddl_match = name_to_schema.get(name_key, "")
        if ddl_match:
            return ddl_match

    return (own_schema or "").strip()


def extract_own_schema_from_ast(ast_data: Mapping[str, Any]) -> str:
    """ANTLR JSON 최상위 children 중 PROCEDURE/FUNCTION 노드의 schema 필드 추출.

    파일 단위 own_schema (자기 schema) — 이 SQL 안의 schema-less 테이블 참조의
    fallback 으로 사용. 일반적인 PROCEDURE 정의 케이스:

      CREATE OR REPLACE PROCEDURE "AMS_MIG"."SP_TD_AB00023"
                                      ↑
                                  schema 필드 = 'AMS_MIG'

    Returns:
        own schema 문자열. 없으면 "".
    """
    if not isinstance(ast_data, Mapping):
        return ""
    for child in (ast_data.get("children") or []):
        if not isinstance(child, Mapping):
            continue
        if child.get("type") in ("PROCEDURE", "FUNCTION", "PACKAGE", "TRIGGER"):
            schema = (child.get("schema") or "").strip().strip('"')
            if schema:
                return schema
    return ""


__all__ = ["build_name_to_schema", "resolve_schema", "extract_own_schema_from_ast"]
