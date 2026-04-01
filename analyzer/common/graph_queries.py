"""공통 Cypher 쿼리 템플릿

DBMS, Framework 양쪽에서 사용하는 Table/Column/Schema/FK 관련
UNWIND MERGE 쿼리를 한 곳에서 관리합니다.
"""

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_MERGE_QUERY = """
UNWIND $items AS item
MERGE (__cy_s__:Schema {db: item.db, name: item.name})
SET __cy_s__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_s__.datasource END
RETURN __cy_s__
"""

# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------
TABLE_MERGE_QUERY = """
UNWIND $items AS item
MERGE (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
SET __cy_t__.description = item.description,
    __cy_t__.description_source = item.description_source,
    __cy_t__.table_type = item.table_type,
    __cy_t__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_t__.datasource END
RETURN __cy_t__
"""

BELONGS_TO_QUERY = """
UNWIND $items AS item
MATCH (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
MATCH (__cy_s__:Schema {db: item.db, name: item.schema})
MERGE (__cy_t__)-[__cy_r__:BELONGS_TO]->(__cy_s__)
RETURN __cy_t__, __cy_r__, __cy_s__
"""

# ---------------------------------------------------------------------------
# Column
# ---------------------------------------------------------------------------
COLUMN_MERGE_QUERY = """
UNWIND $items AS item
MERGE (__cy_c__:Column {fqn: item.fqn})
SET __cy_c__.name = item.name,
    __cy_c__.dtype = item.dtype,
    __cy_c__.description = item.description,
    __cy_c__.description_source = item.description_source,
    __cy_c__.nullable = item.nullable,
    __cy_c__.is_primary_key = item.is_primary_key,
    __cy_c__.is_unique = CASE WHEN item.is_unique IS NOT NULL THEN item.is_unique ELSE __cy_c__.is_unique END,
    __cy_c__.default_value = CASE WHEN item.default_value IS NOT NULL THEN item.default_value ELSE __cy_c__.default_value END,
    __cy_c__.pk_constraint = CASE WHEN item.pk_constraint IS NOT NULL THEN item.pk_constraint ELSE __cy_c__.pk_constraint END,
    __cy_c__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_c__.datasource END
RETURN __cy_c__
"""

HAS_COLUMN_QUERY = """
UNWIND $items AS item
MATCH (__cy_t__:Table {db: item.table_db, schema: item.table_schema, name: item.table_name})
MATCH (__cy_c__:Column {fqn: item.fqn})
MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)
RETURN __cy_t__, __cy_r__, __cy_c__
"""

# ---------------------------------------------------------------------------
# FK
# ---------------------------------------------------------------------------
FK_REF_TABLE_QUERY = """
UNWIND $items AS item
MERGE (__cy_rt__:Table {db: item.to_db, schema: item.to_schema, name: item.to_table})
RETURN __cy_rt__
"""

FK_TO_TABLE_QUERY = """
UNWIND $items AS item
MATCH (__cy_t__:Table {db: item.from_db, schema: item.from_schema, name: item.from_table})
MATCH (__cy_rt__:Table {db: item.to_db, schema: item.to_schema, name: item.to_table})
MERGE (__cy_t__)-[__cy_r__:FK_TO_TABLE {sourceColumn: item.from_column, targetColumn: item.to_column}]->(__cy_rt__)
ON CREATE SET __cy_r__.type = 'many_to_one', __cy_r__.source = 'ddl'
RETURN __cy_t__, __cy_r__, __cy_rt__
"""

FK_TO_COLUMN_QUERY = """
UNWIND $items AS item
MATCH (c1:Column) WHERE toLower(c1.fqn) = toLower(item.from_schema + '.' + item.from_table + '.' + item.from_column)
MATCH (c2:Column) WHERE toLower(c2.fqn) = toLower(item.to_schema + '.' + item.to_table + '.' + item.to_column)
MERGE (c1)-[__cy_r__:FK_TO_COLUMN]->(c2)
ON CREATE SET __cy_r__.source = 'ddl'
RETURN c1, __cy_r__, c2
"""

# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------
DATASOURCE_CREATE_QUERY = """
MERGE (ds:DataSource {name: $datasource_name})
ON CREATE SET ds.created_at = datetime(), ds.source = 'ddl_ingestion'
RETURN ds
"""

DATASOURCE_SCHEMA_QUERY = """
UNWIND $items AS item
MATCH (ds:DataSource {name: item.datasource})
MATCH (__cy_s__:Schema {db: item.db, name: item.name})
MERGE (ds)-[__cy_r__:HAS_SCHEMA]->(__cy_s__)
RETURN ds, __cy_r__, __cy_s__
"""
