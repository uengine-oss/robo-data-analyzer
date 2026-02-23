"""Phase 0: DDL 처리 - 테이블/컬럼/스키마 노드 생성

dbms_pipeline에서 호출하는 DDL 처리 단계.
"""

import asyncio
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Set, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.ddl_static_parser import parse_ddl as regex_parse_ddl
from util.encoding_utils import read_text_with_fallback
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import (
    log_process,
    parse_table_identifier,
    calculate_code_token,
)


def list_ddl_files(orchestrator: Any) -> List[str]:
    """DDL 파일 목록 조회
    
    DDL 디렉토리가 없거나 파일이 없으면 빈 리스트 반환 (경고 처리, 에러 아님)
    """
    ddl_dir = orchestrator.dirs.get("ddl", "")
    if not ddl_dir:
        log_process("ANALYZE", "DDL", "DDL 디렉토리 설정 없음 - DDL 처리 생략")
        return []
    if not os.path.isdir(ddl_dir):
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 없음: {ddl_dir} - DDL 처리 생략")
        return []
    try:
        files = sorted(
            f for f in os.listdir(ddl_dir)
            if os.path.isfile(os.path.join(ddl_dir, f))
        )
        if not files:
            log_process("ANALYZE", "DDL", f"DDL 디렉토리에 파일 없음: {ddl_dir} - DDL 처리 생략")
            return []
        log_process("ANALYZE", "DDL", f"DDL 파일 발견: {len(files)}개")
        return files
    except OSError as e:
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 읽기 실패: {ddl_dir} - {e}")
        return []


def apply_name_case(name: str, name_case: str) -> str:
    """메타데이터 대소문자 변환 적용"""
    if not name:
        return name
    if name_case == "uppercase":
        return name.upper()
    elif name_case == "lowercase":
        return name.lower()
    return name


def resolve_default_schema(
    directory: str,
    ddl_schemas: Set[str],
    name_case: str = 'original'
) -> str:
    """파일 경로에서 기본 스키마를 결정합니다."""
    if not directory:
        return apply_name_case("public", name_case)
    
    parts = directory.replace("\\", "/").split("/")
    parts = [p for p in parts if p]
    
    if not parts:
        return apply_name_case("public", name_case)
    
    if ddl_schemas:
        ddl_schemas_lower_map = {s.lower(): s for s in ddl_schemas}
        for folder in reversed(parts):
            matched = ddl_schemas_lower_map.get(folder.lower())
            if matched:
                return matched
    
    return apply_name_case(parts[-1], name_case)


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
    db_name = (orchestrator.target or 'postgres').lower()
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
        schema_query = """
        UNWIND $items AS item
        MERGE (__cy_s__:Schema {db: item.db, name: item.name})
        SET __cy_s__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_s__.datasource END
        RETURN __cy_s__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(schema_query, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
    
    if schemas_data and datasource_name:
        if emit_progress:
            yield emit_message(f"      🔗 DataSource({datasource_name}) 노드 생성 및 스키마 연결 중...")
        ds_create_query = """
        MERGE (ds:DataSource {name: $datasource_name})
        ON CREATE SET ds.created_at = datetime(), ds.source = 'ddl_ingestion'
        RETURN ds
        """
        async with cypher_lock:
            await client.execute_queries([ds_create_query], {"datasource_name": datasource_name})
        ds_schema_query = """
        UNWIND $items AS item
        MATCH (ds:DataSource {name: item.datasource})
        MATCH (__cy_s__:Schema {db: item.db, name: item.name})
        MERGE (ds)-[__cy_r__:HAS_SCHEMA]->(__cy_s__)
        RETURN ds, __cy_r__, __cy_s__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(ds_schema_query, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel
    
    if tables_data:
        if emit_progress:
            yield emit_message(f"      📦 [2/7] 테이블 {len(tables_data)}개 생성 중...")
        table_query = """
        UNWIND $items AS item
        MERGE (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
        SET __cy_t__.description = item.description,
            __cy_t__.description_source = item.description_source,
            __cy_t__.table_type = item.table_type,
            __cy_t__.datasource = CASE WHEN item.datasource <> '' THEN item.datasource ELSE __cy_t__.datasource END
        RETURN __cy_t__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(table_query, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
    
    if tables_data:
        if emit_progress:
            yield emit_message(f"      📦 [3/7] 테이블-스키마 관계 {len(tables_data)}개 생성 중...")
        belongs_query = """
        UNWIND $items AS item
        MATCH (__cy_t__:Table {db: item.db, schema: item.schema, name: item.name})
        MATCH (__cy_s__:Schema {db: item.db, name: item.schema})
        MERGE (__cy_t__)-[__cy_r__:BELONGS_TO]->(__cy_s__)
        RETURN __cy_t__, __cy_r__, __cy_s__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(belongs_query, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel
    
    if columns_data:
        if emit_progress:
            yield emit_message(f"      📦 [4/7] 컬럼 {len(columns_data)}개 생성 중...")
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
        async with cypher_lock:
            result = await client.run_batch_unwind(column_query, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
    
    if columns_data:
        if emit_progress:
            yield emit_message(f"      📦 [5/7] 테이블-컬럼 관계 {len(columns_data)}개 생성 중...")
        has_column_query = """
        UNWIND $items AS item
        MATCH (__cy_t__:Table {db: item.table_db, schema: item.table_schema, name: item.table_name})
        MATCH (__cy_c__:Column {fqn: item.fqn})
        MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)
        RETURN __cy_t__, __cy_r__, __cy_c__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(has_column_query, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel
    
    if fks_data:
        if emit_progress:
            yield emit_message(f"      📦 [6/7] FK 관계 {len(fks_data)}개 생성 중...")
        ref_tables_query = """
        UNWIND $items AS item
        MERGE (__cy_rt__:Table {db: item.to_db, schema: item.to_schema, name: item.to_table})
        RETURN __cy_rt__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(ref_tables_query, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        fk_query = """
        UNWIND $items AS item
        MATCH (__cy_t__:Table {db: item.from_db, schema: item.from_schema, name: item.from_table})
        MATCH (__cy_rt__:Table {db: item.to_db, schema: item.to_schema, name: item.to_table})
        MERGE (__cy_t__)-[__cy_r__:FK_TO_TABLE {sourceColumn: item.from_column, targetColumn: item.to_column}]->(__cy_rt__)
        ON CREATE SET __cy_r__.type = 'many_to_one', __cy_r__.source = 'ddl'
        RETURN __cy_t__, __cy_r__, __cy_rt__
        """
        async with cypher_lock:
            result = await client.run_batch_unwind(fk_query, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
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
    
    result = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
    if emit_progress:
        yield emit_message(f"   ✅ Neo4j 저장 완료: {len(result['Nodes'])}개 노드, {len(result['Relationships'])}개 관계 생성")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "neo4j_saved", "tables": ddl_stats['tables'], "columns": ddl_stats['columns'], "fks": ddl_stats['fks'], "nodes_created": len(result['Nodes']), "relationships_created": len(result['Relationships'])}
        )
    
    log_process("ANALYZE", "DDL", f"DDL 처리 완료: {file_name} (T:{ddl_stats['tables']}, C:{ddl_stats['columns']}, FK:{ddl_stats['fks']})")
    yield (result, ddl_stats)


async def run_phase_ddl(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 처리 - 테이블/컬럼/스키마 노드 생성
    
    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    ddl_files = list_ddl_files(orchestrator)
    if not ddl_files:
        yield pipeline.emit_skip("DDL 파일 없음 → 스키마 처리 건너뜀")
        return
    
    ddl_count = len(ddl_files)
    yield emit_message("")
    yield pipeline.emit_separator()
    yield pipeline.emit_phase_header(0, "📋 DDL 스키마 수집", f"{ddl_count}개 DDL")
    yield pipeline.emit_separator()
    ddl_dir = orchestrator.dirs["ddl"]
    
    for idx, ddl_file in enumerate(ddl_files, 1):
        yield emit_message("")
        yield pipeline.emit_file_start(idx, ddl_count, ddl_file)
        file_base_progress = int(((idx - 1) / ddl_count) * 100)
        file_end_progress = int((idx / ddl_count) * 100)
        ddl_graph = None
        ddl_stats_file = {"tables": 0, "columns": 0, "fks": 0}
        ds_name = getattr(orchestrator, 'datasource_name', '') or ''
        
        async for item in process_ddl(
            ddl_path=os.path.join(ddl_dir, ddl_file),
            client=client,
            file_name=ddl_file,
            orchestrator=orchestrator,
            cypher_lock=pipeline._cypher_lock,
            ddl_schemas=pipeline._ddl_schemas,
            ddl_table_metadata=pipeline._ddl_table_metadata,
            emit_progress=True,
            file_base_progress=file_base_progress,
            file_end_progress=file_end_progress,
            datasource_name=ds_name,
        ):
            if isinstance(item, tuple):
                ddl_graph, ddl_stats_file = item
            else:
                yield item
        
        if ddl_stats_file["tables"]:
            yield emit_message(f"   ✓ Table 노드: {ddl_stats_file['tables']}개")
        if ddl_stats_file["columns"]:
            yield emit_message(f"   ✓ Column 노드: {ddl_stats_file['columns']}개")
        if ddl_stats_file["fks"]:
            yield emit_message(f"   ✓ FK 관계: {ddl_stats_file['fks']}개")
        yield emit_phase_event(0, "DDL 처리", "running", file_end_progress)
        stats.add_ddl_result(ddl_stats_file["tables"], ddl_stats_file["columns"], ddl_stats_file["fks"])
        if ddl_graph and (ddl_graph.get("Nodes") or ddl_graph.get("Relationships")):
            yield emit_data(graph=ddl_graph, line_number=0, analysis_progress=0, current_file=f"DDL-{ddl_file}")
    
    yield emit_message("")
    yield emit_message("📊 DDL 처리 완료:")
    yield emit_message(f"   • 테이블: {stats.ddl_tables}개")
    yield emit_message(f"   • 컬럼: {stats.ddl_columns}개")
    yield emit_message(f"   • FK: {stats.ddl_fks}개")
