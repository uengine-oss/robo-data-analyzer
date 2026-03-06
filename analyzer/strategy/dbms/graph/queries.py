"""Cypher 쿼리 빌더 통합

DDL UNWIND 쿼리 템플릿 및 User Story 쿼리 빌더.
phase_ddl.py, phase_post.py에서 추출.
"""

# DDL UNWIND 쿼리는 phase_ddl.py의 process_ddl() 내에 인라인으로 존재
# User Story 쿼리는 phase_post.py의 _build_user_story_queries()에 존재
# 여기서는 공통 쿼리 상수만 정의

SCHEMA_MERGE_QUERY = """
UNWIND $items AS item
MERGE (__cy_s__:Schema {db: item.db, name: item.name})
SET __cy_s__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_s__.datasource END
RETURN __cy_s__
"""

TABLE_MERGE_QUERY = """
UNWIND $items AS item
MERGE (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
SET __cy_t__.description = item.description,
    __cy_t__.description_source = item.description_source,
    __cy_t__.table_type = item.table_type,
    __cy_t__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_t__.datasource END
RETURN __cy_t__
"""

COLUMN_MERGE_QUERY = """
UNWIND $items AS item
MERGE (__cy_c__:Column {fqn: item.fqn})
SET __cy_c__.name = item.name,
    __cy_c__.dtype = item.dtype,
    __cy_c__.description = item.description,
    __cy_c__.description_source = item.description_source,
    __cy_c__.nullable = item.nullable,
    __cy_c__.is_primary_key = item.is_primary_key,
    __cy_c__.pk_constraint = CASE WHEN item.pk_constraint IS NOT NULL THEN item.pk_constraint ELSE __cy_c__.pk_constraint END,
    __cy_c__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_c__.datasource END
RETURN __cy_c__
"""

BELONGS_TO_QUERY = """
UNWIND $items AS item
MATCH (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
MATCH (__cy_s__:Schema {db: item.db, name: item.schema})
MERGE (__cy_t__)-[__cy_r__:BELONGS_TO]->(__cy_s__)
RETURN __cy_t__, __cy_r__, __cy_s__
"""

HAS_COLUMN_QUERY = """
UNWIND $items AS item
MATCH (__cy_t__:Table {db: item.table_db, schema: item.table_schema, name: item.table_name})
MATCH (__cy_c__:Column {fqn: item.fqn})
MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)
RETURN __cy_t__, __cy_r__, __cy_c__
"""
