"""Schema 결정 위계 — 모든 전략 공통.

Table 노드의 schema 가 LLM 결과에서 빈값일 때 채워주는 단일 진입점.

위계:
  1) explicit (LLM 명시 schema) — 코드 진실, 가장 신뢰
  2) own_schema (DBMS PROCEDURE 등 자기 schema) — Oracle/SQL Server/DB2/PG 공통 규칙:
     unprefixed 테이블 = 자기 schema. Framework 는 "".
  3) "" (빈값) — 정직하게 모름 표시 (강제 'public' X)

DDL 매칭은 schema 결정에 들어와선 안 됨:
  schema 명시 없는 테이블 참조는 SQL 표준상 자기 schema (또는 search_path 1순위)
  로만 해석된다. DDL 카탈로그에 동명 다른-schema 테이블이 있다고 그쪽으로 가는
  DBMS 는 없다. DDL 은 인벤토리 조회용이지 name resolution 규칙이 아님.

ENV `DEFAULT_DB_SCHEMA` fallback:
  본 모듈은 ENV 를 보지 않음 — 정확도 우선. DB schema 정보가 정말 필요하면
  datasource/catalog 인프라로 보강 (별도 작업).
"""

from __future__ import annotations

from typing import Any, Mapping


def resolve_schema(
    explicit_schema: str,
    own_schema: str = "",
) -> str:
    """Schema 결정 위계 적용.

    Args:
        explicit_schema: LLM/코드 명시 schema (있으면 그대로 신뢰)
        own_schema: PROCEDURE 등 자기 schema (DBMS only). Framework 는 ""

    Returns:
        결정된 schema. 모르면 "".
    """
    explicit = (explicit_schema or "").strip()
    if explicit:
        return explicit
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


__all__ = ["resolve_schema", "extract_own_schema_from_ast"]
