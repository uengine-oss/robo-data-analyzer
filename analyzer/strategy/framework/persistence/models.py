"""Persistence layer data models.

DBMS phase_ddl과 동일한 Table/Column/FK 스키마 사용.
MERGE 키: db, schema, name (Table), fqn (Column)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TableSpec:
    """테이블 스펙 - phase_ddl tables_data 형식 호환"""
    db: str
    schema: str
    name: str
    description: str = ""
    description_source: str = "persistence"
    table_type: str = "TABLE"
    datasource: str = ""


@dataclass
class ColumnSpec:
    """컬럼 스펙 - phase_ddl columns_data 형식 호환"""
    table_db: str
    table_schema: str
    table_name: str
    name: str
    dtype: str = ""
    description: str = ""
    description_source: str = "persistence"
    nullable: bool = True
    is_primary_key: bool = False
    pk_constraint: Optional[str] = None
    datasource: str = ""
    fqn: str = ""  # schema.table.column lower


@dataclass
class PersistenceRelation:
    """FK 관계 스펙 - phase_ddl fks_data 형식 호환"""
    from_db: str
    from_schema: str
    from_table: str
    from_column: str
    to_db: str
    to_schema: str
    to_table: str
    to_column: str


@dataclass
class PersistenceResult:
    """어댑터 실행 결과"""
    tables: list[TableSpec] = field(default_factory=list)
    columns: list[ColumnSpec] = field(default_factory=list)
    relations: list[PersistenceRelation] = field(default_factory=list)
