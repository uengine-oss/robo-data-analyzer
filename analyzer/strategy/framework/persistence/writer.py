"""Persistence writer - Convert PersistenceResult to phase_ddl 형식 데이터."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from analyzer.strategy.framework.persistence.models import PersistenceResult


def to_ddl_format(
    result: PersistenceResult,
    datasource: str = "",
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """PersistenceResult를 phase_ddl과 동일한 schemas/tables/columns/fks 형식으로 변환.

    Returns:
        (schemas_data, tables_data, columns_data, fks_data)
    """
    schemas_data: list[dict] = []
    tables_data: list[dict] = []
    columns_data: list[dict] = []
    fks_data: list[dict] = []
    seen_schemas: set[tuple[str, str]] = set()

    for t in result.tables:
        key = (t.db, t.schema or "public")
        if key not in seen_schemas:
            seen_schemas.add(key)
            schemas_data.append({
                "db": t.db,
                "name": t.schema or "public",
                "datasource": datasource or t.datasource,
            })

        tables_data.append({
            "db": t.db,
            "schema": t.schema or "public",
            "name": t.name,
            "description": t.description,
            "description_source": t.description_source,
            "table_type": t.table_type,
            "datasource": datasource or t.datasource,
        })

    for c in result.columns:
        schema = c.table_schema or "public"
        fqn = c.fqn or ".".join(
            filter(None, [schema, c.table_name, c.name])
        ).lower()
        columns_data.append({
            "fqn": fqn,
            "name": c.name,
            "dtype": c.dtype,
            "description": c.description,
            "description_source": c.description_source,
            "nullable": c.nullable,
            "is_primary_key": c.is_primary_key,
            "pk_constraint": c.pk_constraint,
            "table_db": c.table_db,
            "table_schema": schema,
            "table_name": c.table_name,
            "datasource": c.datasource or datasource,
        })

    for r in result.relations:
        fks_data.append({
            "from_db": r.from_db,
            "from_schema": r.from_schema,
            "from_table": r.from_table,
            "from_column": r.from_column,
            "to_db": r.to_db,
            "to_schema": r.to_schema,
            "to_table": r.to_table,
            "to_column": r.to_column,
        })

    return schemas_data, tables_data, columns_data, fks_data


async def write_to_neo4j(
    result: PersistenceResult,
    client: Any,
    datasource: str = "",
    cypher_lock: asyncio.Lock | None = None,
) -> AsyncGenerator[bytes, None]:
    """PersistenceResult를 Neo4j에 저장 (phase_ddl과 동일한 스키마)."""
    from util.stream_event import emit_data, emit_message

    schemas_data, tables_data, columns_data, fks_data = to_ddl_format(result, datasource)
    if not schemas_data and not tables_data and not columns_data and not fks_data:
        return

    lock = cypher_lock or asyncio.Lock()
    all_nodes: dict = {}
    all_relationships: dict = {}

    if schemas_data:
        schema_query = """
        UNWIND $items AS item
        MERGE (__cy_s__:Schema {db: item.db, name: item.name})
        SET __cy_s__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_s__.datasource END
        RETURN __cy_s__
        """
        async with lock:
            res = await client.run_batch_unwind(schema_query, schemas_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if tables_data:
        table_query = """
        UNWIND $items AS item
        MERGE (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
        SET __cy_t__.description = item.description,
            __cy_t__.description_source = item.description_source,
            __cy_t__.table_type = item.table_type,
            __cy_t__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_t__.datasource END,
            __cy_t__.node_id = CASE WHEN item.schema <> '' AND item.schema <> 'public'
                THEN 'table:' + item.schema + '.' + item.name
                ELSE 'table:' + item.name END
        RETURN __cy_t__
        """
        async with lock:
            res = await client.run_batch_unwind(table_query, tables_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if tables_data and schemas_data:
        belongs_query = """
        UNWIND $items AS item
        MATCH (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
        MATCH (__cy_s__:Schema {db: item.db, name: item.schema})
        MERGE (__cy_t__)-[__cy_r__:BELONGS_TO]->(__cy_s__)
        RETURN __cy_t__, __cy_r__, __cy_s__
        """
        async with lock:
            res = await client.run_batch_unwind(belongs_query, tables_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in res.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if columns_data:
        column_query = """
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
        async with lock:
            res = await client.run_batch_unwind(column_query, columns_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if columns_data:
        has_column_query = """
        UNWIND $items AS item
        MATCH (__cy_t__:Table {db: item.table_db, schema: item.table_schema, name: item.table_name})
        MATCH (__cy_c__:Column {fqn: item.fqn})
        MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)
        RETURN __cy_t__, __cy_r__, __cy_c__
        """
        async with lock:
            res = await client.run_batch_unwind(has_column_query, columns_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in res.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if fks_data:
        ref_tables_query = """
        UNWIND $items AS item
        MERGE (__cy_rt__:Table {db: item.to_db, schema: item.to_schema, name: item.to_table})
        RETURN __cy_rt__
        """
        async with lock:
            res = await client.run_batch_unwind(ref_tables_query, fks_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        fk_query = """
        UNWIND $items AS item
        MATCH (__cy_t__:Table {db: item.from_db, schema: item.from_schema, name: item.from_table})
        MATCH (__cy_rt__:Table {db: item.to_db, schema: item.to_schema, name: item.to_table})
        MERGE (__cy_t__)-[__cy_r__:FK_TO_TABLE {sourceColumn: item.from_column, targetColumn: item.to_column}]->(__cy_rt__)
        ON CREATE SET __cy_r__.type = 'many_to_one', __cy_r__.source = 'persistence'
        RETURN __cy_t__, __cy_r__, __cy_rt__
        """
        async with lock:
            res = await client.run_batch_unwind(fk_query, fks_data)
        for node in res.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in res.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    yield emit_message(f"   ✓ Persistence 테이블 {len(result.tables)}개, 컬럼 {len(result.columns)}개 저장 완료")
    yield emit_data(graph={
        "Nodes": list(all_nodes.values()),
        "Relationships": list(all_relationships.values()),
    })
