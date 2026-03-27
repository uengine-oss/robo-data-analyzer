"""DDL 파일 처리 및 Neo4j 테이블/컬럼/스키마 노드 생성

공통 모듈 — Framework, DBMS 양쪽 strategy에서 사용 가능.
"""

import asyncio
from typing import Any, AsyncGenerator, Dict, Set, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.common.ddl_parser import parse_ddl as regex_parse_ddl
from util.encoding_utils import read_text_with_fallback
from util.ndjson_stream import (
    emit_message,
    emit_phase_event,
)
from util.logger import log_process
from util.text_utils import (
    parse_table_identifier,
    calculate_code_token,
)
from analyzer.common.ddl_utils import apply_name_case
from analyzer.common.graph_queries import (
    SCHEMA_MERGE_QUERY,
    TABLE_MERGE_QUERY,
    BELONGS_TO_QUERY,
    COLUMN_MERGE_QUERY,
    HAS_COLUMN_QUERY,
    FK_REF_TABLE_QUERY,
    FK_TO_TABLE_QUERY,
    FK_TO_COLUMN_QUERY,
    DATASOURCE_CREATE_QUERY,
    DATASOURCE_SCHEMA_QUERY,
)


async def process_ddl(
    ddl_path: str,
    client: Neo4jClient,
    file_name: str,
    orchestrator: Any,
    cypher_lock: asyncio.Lock,
    ddl_schemas: Set[str],
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]],
    emit_progress: bool = True,
    file_base_progress: int = 0,
    file_end_progress: int = 100,
    datasource_name: str = "",
) -> AsyncGenerator[bytes | Tuple[dict, dict], None]:
    """DDL 파일 처리 및 테이블/컬럼 노드 생성 (스트리밍)"""
    ddl_stats = {"tables": 0, "columns": 0, "fks": 0}
    file_range = file_end_progress - file_base_progress
    parsing_end = file_base_progress + int(file_range * 0.5)
    saving_start = parsing_end
    saving_end = file_end_progress

    ddl_content, encoding_used = await read_text_with_fallback(
        ddl_path,
        preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"),
    )
    if encoding_used != "utf-8":
        log_process("ANALYZE", "DDL", f"DDL 인코딩 fallback 적용: {file_name} ({encoding_used})")

    total_tokens = calculate_code_token(ddl_content)

    if emit_progress:
        yield emit_message(f"   ⚡ 정규식 파서 사용 (정적 파싱)")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=file_base_progress + int(file_range * 0.1),
            details={"mode": "regex", "tokens": total_tokens}
        )

    try:
        parsed = await asyncio.to_thread(regex_parse_ddl, ddl_content)
        all_parsed_results = parsed.get("analysis", [])
        table_count = len(all_parsed_results)
        if emit_progress:
            table_names = [t.get("table", {}).get("name", "?") for t in all_parsed_results[:5]]
            preview = ", ".join(table_names)
            if table_count > 5:
                preview += f" 외 {table_count - 5}개"
            yield emit_message(f"   ✅ 파싱 완료: {table_count}개 테이블 ({preview})")
            yield emit_phase_event(
                phase_num=0,
                phase_name="DDL 처리",
                status="in_progress",
                progress=parsing_end,
                details={"tables_parsed": table_count, "mode": "regex"}
            )
    except Exception as e:
        if emit_progress:
            yield emit_message(f"   ❌ 정규식 파싱 실패: {str(e)[:80]}")
        raise RuntimeError(f"DDL 정규식 파싱 실패: {e}")

    parsed = {"analysis": all_parsed_results}
    # target이 프로그래밍 언어(c, java 등)일 때는 DB 기본값 사용
    _LANG_TARGETS = {"c", "java", "python", "go", "kotlin", "scala", "rust"}
    _raw_target = (orchestrator.target or "postgres").lower()
    db_name = "postgres" if _raw_target in _LANG_TARGETS else _raw_target
    name_case = getattr(orchestrator, 'name_case', 'original')

    schemas_data = []
    tables_data = []
    columns_data = []
    fks_data = []
    seen_schemas = set()
    seen_tables = set()

    for table_info in parsed.get("analysis", []):
        table = table_info.get("table", {})
        columns = table_info.get("columns", [])
        foreign_keys = table_info.get("foreignKeys", [])
        primary_keys = [
            str(pk).strip().upper()
            for pk in (table_info.get("primaryKeys") or [])
            if pk
        ]

        schema_raw = (table.get("schema") or "").strip()
        table_name_raw = (table.get("name") or "").strip()
        comment = (table.get("comment") or "").strip()
        table_type = (table.get("table_type") or "BASE TABLE").strip().upper()
        qualified = f"{schema_raw}.{table_name_raw}" if schema_raw else table_name_raw
        parsed_schema, parsed_name, _ = parse_table_identifier(qualified)
        schema = apply_name_case(parsed_schema if parsed_schema else "public", name_case)
        parsed_name = apply_name_case(parsed_name, name_case)

        if schema and schema.lower() != 'public':
            ddl_schemas.add(schema)

        schema_key = (db_name, schema)
        if schema_key not in seen_schemas:
            seen_schemas.add(schema_key)
            schemas_data.append({
                "db": db_name,
                "name": schema,
                "datasource": datasource_name
            })

        table_key = (db_name, schema, parsed_name)
        if table_key not in seen_tables:
            seen_tables.add(table_key)
            tables_data.append({
                "db": db_name,
                "schema": schema,
                "name": parsed_name,
                "description": comment,
                "description_source": "ddl" if comment else "",
                "table_type": table_type,
                "datasource": datasource_name
            })
            ddl_stats["tables"] += 1

        column_metadata = {}
        for col in columns:
            col_name_raw = (col.get("name") or "").strip()
            if not col_name_raw:
                continue
            col_name = apply_name_case(col_name_raw, name_case)
            col_comment = (col.get("comment") or "").strip()
            column_metadata[col_name] = {
                "description": col_comment,
                "dtype": (col.get("dtype") or col.get("type") or "").strip(),
                "nullable": col.get("nullable", True),
            }
        cache_key = (schema.lower(), parsed_name.lower())
        ddl_table_metadata[cache_key] = {
            "description": comment,
            "columns": column_metadata,
            "original_schema": schema,
            "original_name": parsed_name,
        }

        for col in columns:
            col_name_raw = (col.get("name") or "").strip()
            if not col_name_raw:
                continue
            col_name = apply_name_case(col_name_raw, name_case)
            col_type = (col.get("dtype") or col.get("type") or "").strip()
            col_nullable = col.get("nullable", True)
            col_comment = (col.get("comment") or "").strip()
            fqn = ".".join(filter(None, [datasource_name, schema, parsed_name, col_name])).lower()
            is_primary_key = col.get("primary_key", False) or (col_name_raw.upper() in primary_keys)
            col_data = {
                "fqn": fqn,
                "name": col_name,
                "dtype": col_type,
                "description": col_comment,
                "description_source": "ddl" if col_comment else "",
                "nullable": col_nullable,
                "is_primary_key": is_primary_key,
                "table_db": db_name,
                "table_schema": schema,
                "table_name": parsed_name,
                "datasource": datasource_name
            }
            if is_primary_key:
                col_data["pk_constraint"] = f"{parsed_name}_pkey"
            columns_data.append(col_data)
            ddl_stats["columns"] += 1

        for fk in foreign_keys:
            src_col_raw = (fk.get("column") or "").strip()
            ref = (fk.get("ref") or "").strip()
            if not src_col_raw or not ref or "." not in ref:
                continue
            ref_table_part, ref_col_raw = ref.rsplit(".", 1)
            ref_schema_parsed, ref_table_raw, _ = parse_table_identifier(ref_table_part)
            ref_schema_final = apply_name_case(ref_schema_parsed or schema, name_case)
            ref_table = apply_name_case(ref_table_raw, name_case)
            src_col = apply_name_case(src_col_raw, name_case)
            ref_col = apply_name_case(ref_col_raw, name_case)
            fks_data.append({
                "from_db": db_name,
                "from_schema": schema,
                "from_table": parsed_name,
                "from_column": src_col,
                "to_db": db_name,
                "to_schema": ref_schema_final or "",
                "to_table": ref_table or "",
                "to_column": ref_col
            })
            ddl_stats["fks"] += 1

    if emit_progress:
        yield emit_message(f"   💾 UNWIND 배치 저장 시작: {ddl_stats['tables']}개 테이블, {ddl_stats['columns']}개 컬럼, {ddl_stats['fks']}개 FK")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_start,
            details={"step": "unwind_batch", "tables": ddl_stats['tables'], "columns": ddl_stats['columns'], "fks": ddl_stats['fks']}
        )

    all_nodes: dict = {}
    all_relationships: dict = {}

    if schemas_data:
        if emit_progress:
            yield emit_message(f"      📦 [1/7] 스키마 {len(schemas_data)}개 생성 중...")
        schema_query = SCHEMA_MERGE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(schema_query, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if schemas_data and datasource_name:
        if emit_progress:
            yield emit_message(f"      🔗 DataSource({datasource_name}) 노드 생성 및 스키마 연결 중...")
        ds_create_query = DATASOURCE_CREATE_QUERY
        async with cypher_lock:
            await client.execute_queries([ds_create_query], {"datasource_name": datasource_name})
        ds_schema_query = DATASOURCE_SCHEMA_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(ds_schema_query, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if tables_data:
        if emit_progress:
            yield emit_message(f"      📦 [2/7] 테이블 {len(tables_data)}개 생성 중...")
        table_query = TABLE_MERGE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(table_query, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if tables_data:
        if emit_progress:
            yield emit_message(f"      📦 [3/7] 테이블-스키마 관계 {len(tables_data)}개 생성 중...")
        belongs_query = BELONGS_TO_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(belongs_query, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if columns_data:
        if emit_progress:
            yield emit_message(f"      📦 [4/7] 컬럼 {len(columns_data)}개 생성 중...")
        column_query = COLUMN_MERGE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(column_query, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if columns_data:
        if emit_progress:
            yield emit_message(f"      📦 [5/7] 테이블-컬럼 관계 {len(columns_data)}개 생성 중...")
        has_column_query = HAS_COLUMN_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(has_column_query, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if fks_data:
        if emit_progress:
            yield emit_message(f"      📦 [6/7] FK 관계 {len(fks_data)}개 생성 중...")
        ref_tables_query = FK_REF_TABLE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(ref_tables_query, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        fk_query = FK_TO_TABLE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(fk_query, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

        fk_col_query = FK_TO_COLUMN_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(fk_col_query, fks_data)
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if emit_progress:
        yield emit_message(f"   ✅ UNWIND 배치 저장 완료: {len(all_nodes)}개 노드, {len(all_relationships)}개 관계")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "unwind_completed", "nodes_created": len(all_nodes), "relationships_created": len(all_relationships)}
        )

    graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
    if emit_progress:
        yield emit_message(f"   ✅ Neo4j 저장 완료: {len(graph['Nodes'])}개 노드, {len(graph['Relationships'])}개 관계 생성")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "neo4j_saved", "tables": ddl_stats['tables'], "columns": ddl_stats['columns'], "fks": ddl_stats['fks'], "nodes_created": len(graph['Nodes']), "relationships_created": len(graph['Relationships'])}
        )

    # 그래프 데이터를 bytes로 직접 스트리밍 (프론트에서 즉시 렌더링)
    from util.ndjson_stream import emit_data
    yield emit_data(graph=graph)

    log_process("ANALYZE", "DDL", f"DDL 처리 완료: {file_name} (T:{ddl_stats['tables']}, C:{ddl_stats['columns']}, FK:{ddl_stats['fks']})")
