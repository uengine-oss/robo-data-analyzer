"""테이블/컬럼 설명 요약 - DBMS

llm_phase.py에서 분리된 Phase 2.4 로직입니다.
모든 파일의 테이블/컬럼 설명을 요약하고 Neo4j에 저장합니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from config.settings import settings
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process


async def summarize_table(
    table_key: Tuple[str, str],
    data: Dict[str, Any],
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]],
    dbms: str,
    api_key: str,
    locale: str,
) -> List[Dict[str, Any]]:
    """테이블 요약 처리"""
    from analyzer.strategy.dbms.ast_processor import summarize_table_metadata
    
    schema_key, name_key = table_key
    
    # --- 메타데이터 수집 ---
    ddl_key = (schema_key.lower(), name_key.lower())
    ddl_meta = ddl_table_metadata.get(ddl_key, {})
    ddl_description = (ddl_meta.get('description') or '').strip()
    ddl_columns = ddl_meta.get('columns') or {}
    
    summaries = list(data.get('summaries') or [])
    columns_map = data.get('columns') or {}
    column_sentences = {
        entry['name']: list(entry['summaries'])
        for entry in columns_map.values()
        if entry.get('summaries')
    }
    
    if ddl_description:
        summaries.insert(0, f"[DDL 메타데이터] {ddl_description}")
    
    for col_name, ddl_col in ddl_columns.items():
        ddl_col_desc = (ddl_col.get('description') or '').strip()
        if ddl_col_desc and col_name not in column_sentences:
            column_sentences[col_name] = [f"[DDL 메타데이터] {ddl_col_desc}"]
        elif ddl_col_desc and col_name in column_sentences:
            column_sentences[col_name].insert(0, f"[DDL 메타데이터] {ddl_col_desc}")
    
    if not summaries and not column_sentences:
        return []
    
    table_display = f"{schema_key}.{name_key}" if schema_key else name_key
    column_metadata = {}
    for entry in columns_map.values():
        col_name = entry['name']
        ddl_col = ddl_columns.get(col_name, {})
        column_metadata[col_name] = {
            "dtype": entry.get("dtype") or ddl_col.get("dtype") or "",
            "nullable": bool(entry.get("nullable", True)) if entry.get("nullable") is not None else ddl_col.get("nullable", True),
            "examples": sorted(list(entry.get("examples") or []))[:5],
        }
    
    for col_name, ddl_col in ddl_columns.items():
        if col_name not in column_metadata:
            column_metadata[col_name] = {
                "dtype": ddl_col.get("dtype") or "",
                "nullable": ddl_col.get("nullable", True),
                "examples": [],
            }
    
    # --- LLM 호출 ---
    result = await asyncio.to_thread(
        summarize_table_metadata,
        table_display,
        summaries,
        column_sentences,
        column_metadata,
        api_key,
        locale,
    )
    
    if not isinstance(result, dict):
        raise RuntimeError(f"테이블 요약 결과가 dict가 아님 ({schema_key}.{name_key}): {type(result).__name__}")
    
    # --- 쿼리 생성 ---
    queries: List[Dict[str, Any]] = []
    llm_table_desc = (result.get('tableDescription') or '').strip()
    
    if llm_table_desc:
        queries.append({
            "query": (
                f"MATCH (__cy_t__:Table {{schema: $schema, name: $name, db: $db}})\n"
                f"SET __cy_t__.analyzed_description = $llm_desc\n"
                f"WITH __cy_t__\n"
                f"WHERE __cy_t__.description IS NULL OR __cy_t__.description = ''\n"
                f"SET __cy_t__.description = $llm_desc, __cy_t__.description_source = 'procedure'\n"
                f"RETURN __cy_t__"
            ),
            "params": {
                "schema": schema_key,
                "name": name_key,
                "db": dbms,
                "llm_desc": llm_table_desc,
            }
        })
    
    for column_info in result.get('columns', []) or []:
        column_name = (column_info.get('name') or '').strip()
        llm_column_desc = (column_info.get('description') or '').strip()
        if not column_name or not llm_column_desc:
            continue
        
        fqn = '.'.join(filter(None, [schema_key, name_key, column_name])).lower()
        queries.append({
            "query": (
                f"MATCH (__cy_c__:Column {{fqn: $fqn}})\n"
                f"SET __cy_c__.analyzed_description = $llm_desc\n"
                f"WITH __cy_c__\n"
                f"WHERE __cy_c__.description IS NULL OR __cy_c__.description = ''\n"
                f"SET __cy_c__.description = $llm_desc, __cy_c__.description_source = 'procedure'\n"
                f"RETURN __cy_c__"
            ),
            "params": {
                "fqn": fqn,
                "llm_desc": llm_column_desc,
            }
        })
    
    return queries


async def run_phase2_4_table_summary(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.4: 테이블/컬럼 설명 요약
    
    모든 파일의 테이블/컬럼 설명을 요약하고 Neo4j에 저장합니다.
    """
    # 모든 contexts의 processor에서 _table_summary_store와 _ddl_table_metadata 수집
    all_table_stores: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_ddl_metadata: Dict[Tuple[str, str], Dict[str, Any]] = {}
    dbms_name = None
    
    for ctx in contexts:
        if ctx.status == FileStatus.PH2_OK and ctx.processor:
            processor = ctx.processor
            if hasattr(processor, '_table_summary_store') and processor._table_summary_store:
                # _table_summary_store는 set을 사용하므로 list로 변환
                for table_key, data in processor._table_summary_store.items():
                    if table_key not in all_table_stores:
                        all_table_stores[table_key] = {
                            "summaries": set(),
                            "columns": {},
                        }
                    # summaries 합치기
                    all_table_stores[table_key]["summaries"].update(data.get("summaries", set()))
                    # columns 합치기
                    for col_name, col_data in data.get("columns", {}).items():
                        if col_name not in all_table_stores[table_key]["columns"]:
                            all_table_stores[table_key]["columns"][col_name] = col_data.copy()
                        else:
                            # summaries 합치기
                            existing = all_table_stores[table_key]["columns"][col_name]
                            existing["summaries"].update(col_data.get("summaries", set()))
            
            if hasattr(processor, '_ddl_table_metadata') and processor._ddl_table_metadata:
                all_ddl_metadata.update(processor._ddl_table_metadata)
            
            if not dbms_name and hasattr(processor, 'dbms'):
                dbms_name = processor.dbms
    
    if not all_table_stores:
        log_process("ANALYZE", "PHASE_2.4", "⚠️ 테이블 요약 대상 없음 (store 비어있음)")
        yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "completed", 100, {"tables": 0})
        return
    
    log_process("ANALYZE", "PHASE_2.4", f"📊 테이블 요약 시작: {len(all_table_stores)}개 테이블")
    
    # 모든 테이블 병렬 처리
    tasks = [
        summarize_table(
            table_key,
            {
                "summaries": list(data["summaries"]),  # set을 list로 변환
                "columns": data["columns"],
            },
            all_ddl_metadata,
            dbms_name or "",
            orchestrator.api_key,
            orchestrator.locale,
        )
        for table_key, data in all_table_stores.items()
    ]
    
    if not tasks:
        yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "completed", 100, {"tables": 0})
        return
    
    # Phase 2.4 시작 이벤트
    yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "started", 0, {"canPause": True, "tables": len(tasks)})
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_queries: List[Dict[str, Any]] = []
    
    completed = 0
    for result in results:
        completed += 1
        if isinstance(result, Exception):
            log_process("ANALYZE", "PHASE_2.4", f"❌ 테이블 요약 오류: {result}", logging.ERROR)
            yield emit_message(f"   ❌ [{completed}/{len(tasks)}] 테이블 요약 오류: {str(result)[:50]}")
        elif result:
            all_queries.extend(result)
            yield emit_message(f"   ✓ [{completed}/{len(tasks)}] 테이블 요약 완료")
        
        progress = int(completed / len(tasks) * 100)
        yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "progress", progress, {"completed": completed, "total": len(tasks)})
    
    # 쿼리 실행 및 스트리밍
    if all_queries:
        async with analyzer._cypher_lock:
            async for batch_result in client.run_graph_query(all_queries):
                yield emit_data(
                    graph={
                        "Nodes": batch_result.get("Nodes", []),
                        "Relationships": batch_result.get("Relationships", []),
                    }
                )
        log_process("ANALYZE", "PHASE_2.4", f"✅ 테이블 요약 완료: {len(all_queries)}개 쿼리")
    else:
        log_process("ANALYZE", "PHASE_2.4", "⚠️ 테이블 요약 쿼리 없음")
    
    # 모든 processor의 _table_summary_store 클리어
    for ctx in contexts:
        if ctx.processor and hasattr(ctx.processor, '_table_summary_store'):
            ctx.processor._table_summary_store.clear()
    
    yield emit_phase_event(2.4, "테이블/컬럼 설명 요약", "completed", 100, {"tables": len(tasks), "queries": len(all_queries)})

