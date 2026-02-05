"""LangGraph 워크플로우 + Checkpoint

비타겟 파일 분석 파이프라인을 LangGraph로 구현합니다.
단계: load_rules → sample → chunk → summarize → upsert → link → save_run

RuntimeCtx 패턴:
- State는 순수 데이터만 포함 (직렬화 가능)
- client, cypher_lock은 RuntimeCtx로 분리하여 클로저로 주입
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from analyzer.strategy.framework.nontarget.nontarget_logger import (
    log_nontarget,
    log_workflow_state,
    log_correction,
)
from analyzer.strategy.framework.nontarget.chunker import (
    chunk_by_rules,
    load_rules_from_memory,
    save_rules_to_memory,
    discover_patterns_iteratively,
)
from analyzer.strategy.framework.nontarget.linker import create_links, enrich_connected_targets, create_from_writes_relations
from analyzer.strategy.framework.nontarget.deepagents_client import (
    generate_summary_with_agent,
    save_run_record,
    should_update_overview,
    summarize_runs_and_update_overview,
    update_topic_memory,
)
from analyzer.strategy.framework.nontarget.trace_logger import TraceLogger
from analyzer.strategy.framework.nontarget.table_processor import TableProcessor


# ============================================================================
# State 정의 (순수 데이터만 - 직렬화 가능)
# ============================================================================

class NontargetState(TypedDict, total=False):
    """워크플로우 상태 (직렬화 가능한 순수 데이터만)"""
    file_path: str
    rules: Optional[Dict[str, Any]]
    blocks: List[Dict[str, Any]]
    summaries: List[Dict[str, Any]]
    phase: str
    error: Optional[str]
    # client, cypher_lock은 RuntimeCtx로 분리!
    # sample 필드 제거 - 이후 노드에서 사용되지 않음


# ============================================================================
# Runtime Context (직렬화 불가능한 객체 - 클로저로 주입)
# ============================================================================

@dataclass
class RuntimeCtx:
    """런타임 컨텍스트 (직렬화하지 않음)
    
    Neo4j 클라이언트와 락 등 직렬화 불가능한 객체를 담습니다.
    노드 함수에 클로저로 주입됩니다.
    """
    client: Any = None
    cypher_lock: Optional[asyncio.Lock] = None
    trace_logger: Optional[TraceLogger] = None


# ============================================================================
# 노드 함수 팩토리 (클로저 패턴)
# ============================================================================

def make_node_functions(ctx: RuntimeCtx) -> Dict[str, Callable]:
    """RuntimeCtx를 클로저로 캡처한 노드 함수들 생성
    
    Args:
        ctx: 런타임 컨텍스트 (client, cypher_lock)
        
    Returns:
        노드 함수 딕셔너리
    """
    
    def node_load_rules(state: NontargetState) -> NontargetState:
        """mem/rules/에서 기존 규칙 로드 시도"""
        log_workflow_state("load_rules", state)
        
        file_path = state.get("file_path", "")
        rules = load_rules_from_memory(file_path)
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("load_rules", state)
        
        return {**state, "rules": rules, "phase": "load_rules"}

    def node_sample(state: NontargetState) -> NontargetState:
        """반복 패턴 발견
        
        대용량 파일(만 라인 이상)에서도 정확한 패턴을 파악하기 위해
        반복 샘플링 방식으로 패턴을 발견합니다.
        """
        log_workflow_state("sample", state)
        
        file_path = state.get("file_path", "")
        rules = state.get("rules")
        
        if not rules:
            # 반복 샘플링으로 패턴 발견 (ctx 전달)
            rules = discover_patterns_iteratively(file_path, ctx)
            
            # 규칙 저장
            save_rules_to_memory(file_path, rules)
            
            log_nontarget("SAMPLE", f"규칙 생성 완료: boundary={rules.get('boundary')}, "
                         f"confidence={rules.get('confidence', 0):.2f}")
            
            return {**state, "rules": rules, "phase": "sample"}
        
        return {**state, "phase": "sample"}

    def node_chunk(state: NontargetState) -> NontargetState:
        """블록화 + 자동 A 오류 감지"""
        log_workflow_state("chunk", state)
        
        file_path = state.get("file_path", "")
        rules = state.get("rules") or {}
        
        blocks = chunk_by_rules(file_path, rules)
        
        # A 오류 자동 감지: 블록 수가 너무 많거나 평균 크기가 너무 작음
        if len(blocks) > 300:
            log_nontarget("AUTO-DETECT", f"A 오류: 블록 수 초과 ({len(blocks)} > 300)")
            raise RuntimeError(f"블록 경계 오류 (A): 블록 수 {len(blocks)}개 초과. 재블록화 필요.")
        
        if blocks:
            avg_lines = sum(
                b.get("end_line", 0) - b.get("start_line", 0) + 1
                for b in blocks
            ) / len(blocks)
            if avg_lines < 10:
                log_nontarget("AUTO-DETECT", f"A 오류: 평균 블록 크기 너무 작음 ({avg_lines:.1f}줄)")
                raise RuntimeError(f"블록 경계 오류 (A): 평균 블록 크기 {avg_lines:.1f}줄. 재블록화 필요.")
        
        if ctx.trace_logger:
            ctx.trace_logger.log_chunk_result(blocks, rules)
        
        return {**state, "blocks": blocks, "phase": "chunk"}

    def node_summarize(state: NontargetState) -> NontargetState:
        """DeepAgents로 Summary 생성 + 자동 B 오류 감지"""
        log_workflow_state("summarize", state)
        
        blocks = state.get("blocks", [])
        rules = state.get("rules") or {}
        file_type = rules.get("file_type", "unknown")
        block_roles = rules.get("block_roles", [])  # 청킹에서 발견한 블록 역할
        
        summaries: List[Dict[str, Any]] = []
        for i, block in enumerate(blocks):
            code = block.get("code", "")
            start_line = block.get("start_line", 0)
            end_line = block.get("end_line", 0)
            
            # 블록 역할 찾기: 라인 범위가 겹치는 역할 정보
            matched_role = None
            for role_info in block_roles:
                lines_str = role_info.get("lines", "")
                if "-" in lines_str:
                    parts = lines_str.split("-")
                    try:
                        role_start = int(parts[0])
                        role_end = int(parts[1])
                        # 범위 겹침 확인
                        if role_start <= end_line and role_end >= start_line:
                            matched_role = role_info
                            break
                    except (ValueError, IndexError):
                        pass
            
            # DeepAgents로 Summary 생성 (ctx + block_role 전달)
            summary = generate_summary_with_agent(code, file_type, ctx, block_role=matched_role)
            
            # B 오류 자동 감지: 키워드가 너무 적거나 kind가 unknown
            keywords = summary.get("keywords", [])
            kind = summary.get("kind", "unknown")
            
            if len(keywords) < 2:
                log_nontarget("AUTO-DETECT", f"B 오류: 블록 {i+1} 키워드 부족 ({len(keywords)}개)")
                raise RuntimeError(f"Summary 오류 (B): 블록 {i+1}의 키워드가 {len(keywords)}개로 부족. 재요약 필요.")
            
            if kind == "unknown" and len(code) > 100:
                log_nontarget("AUTO-DETECT", f"B 오류: 블록 {i+1} kind 분류 실패")
                raise RuntimeError(f"Summary 오류 (B): 블록 {i+1}의 kind 분류 실패. 재요약 필요.")
            
            summaries.append(summary)
            log_nontarget("SUMMARIZE", f"블록 {i+1}/{len(blocks)} summary 생성: kind={kind}, keywords={len(keywords)}")
            
            if ctx.trace_logger:
                ctx.trace_logger.log_summarize_result(i, summary)
        
        return {**state, "summaries": summaries, "phase": "summarize"}

    async def node_upsert(state: NontargetState) -> NontargetState:
        """Neo4j에 적재 (기존 삭제 + 새로 생성)"""
        log_workflow_state("upsert", state)
        
        file_path = state.get("file_path", "")
        blocks = state.get("blocks", [])
        summaries = state.get("summaries", [])
        
        # ctx에서 client, cypher_lock 가져오기 (클로저)
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if not client:
            log_nontarget("UPSERT", "Neo4j 클라이언트 없음 - 스킵")
            return {**state, "phase": "upsert"}
        
        queries: List[Dict[str, Any]] = []
        
        # 1. NONTARGET_FILE 노드 생성
        queries.append({
            "query": """
                MERGE (__cy_f__:NONTARGET_FILE {file_path: $file_path})
                RETURN __cy_f__
            """,
            "params": {"file_path": file_path}
        })
        
        # 2. 기존 블록 삭제 (이 파일에 속한)
        queries.append({
            "query": """
                MATCH (__cy_b__:NONTARGET_BLOCK {file_path: $file_path})
                DETACH DELETE __cy_b__
            """,
            "params": {"file_path": file_path}
        })
        
        # 3. FILE 노드 생성 및 기존 블록 삭제 먼저 실행
        try:
            if cypher_lock:
                async with cypher_lock:
                    for q in queries:
                        await client.execute_with_params(q["query"], q["params"])
            else:
                for q in queries:
                    await client.execute_with_params(q["query"], q["params"])
        except Exception as e:
            log_nontarget("UPSERT", f"Neo4j 초기화 실패: {e}")
            return {**state, "phase": "upsert", "error": str(e)}
        
        # 4. 새 블록 생성 및 elementId 저장
        create_query = """
            MATCH (__cy_f__:NONTARGET_FILE {file_path: $file_path})
            CREATE (__cy_b__:NONTARGET_BLOCK {
                name: $block_name,
                file_path: $file_path,
                start_line: $start_line,
                end_line: $end_line,
                node_code: $code,
                kind: $kind,
                keywords: $keywords,
                summary: $summary,
                evidence: $evidence,
                active: true,
                chunk_version: 1
            })
            MERGE (__cy_f__)-[:HAS_BLOCK]->(__cy_b__)
            RETURN elementId(__cy_b__) AS block_id
        """
        
        try:
            for i, (block, summary) in enumerate(zip(blocks, summaries)):
                block_name = f"NONTARGET_BLOCK[{block.get('start_line', 0)}]"
                params = {
                    "file_path": file_path,
                    "block_name": block_name,
                    "start_line": block.get("start_line", 0),
                    "end_line": block.get("end_line", 0),
                    "code": block.get("code", ""),
                    "kind": summary.get("kind", "unknown"),
                    "keywords": summary.get("keywords", []),
                    "summary": summary.get("summary", ""),
                    "evidence": summary.get("evidence", ""),
                }
                
                if cypher_lock:
                    async with cypher_lock:
                        result = await client.execute_with_params(create_query, params)
                else:
                    result = await client.execute_with_params(create_query, params)
                
                # elementId 추출하여 블록에 저장
                if result and len(result) > 0:
                    record = result[0]
                    if hasattr(record, "data"):
                        block_id = record.data().get("block_id")
                    elif isinstance(record, dict):
                        block_id = record.get("block_id")
                    else:
                        block_id = None
                    
                    if block_id:
                        blocks[i]["id"] = block_id
            
            log_nontarget("UPSERT", f"Neo4j 적재 완료: {file_path} → {len(blocks)}개 블록")
        except Exception as e:
            log_nontarget("UPSERT", f"Neo4j 적재 실패: {e}")
            return {**state, "phase": "upsert", "error": str(e)}
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("upsert", state)
        
        return {**state, "blocks": blocks, "phase": "upsert"}

    async def node_process_tables(state: NontargetState) -> NontargetState:
        """테이블/컬럼 자동 생성/보강
        
        Summary에서 추출된 테이블 정보를:
        1. 기존 테이블 존재 시: description, columns 보강
        2. 테이블 없음: Table, Column 노드 신규 생성
        3. REFERS_TO 관계 생성 (Table/Column → NONTARGET_BLOCK)
        """
        log_workflow_state("process_tables", state)
        
        blocks = state.get("blocks", [])
        summaries = state.get("summaries", [])
        
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if not client:
            log_nontarget("TABLE", "Neo4j 클라이언트 없음 - 테이블 처리 스킵")
            return {**state, "phase": "process_tables"}
        
        processor = TableProcessor(
            client=client,
            cypher_lock=cypher_lock,
            trace_logger=ctx.trace_logger,
        )
        
        total_created = 0
        total_updated = 0
        total_refers_to = 0
        
        for block, summary in zip(blocks, summaries):
            block_id = block.get("id")
            if not block_id:
                continue
            
            # tables 필드가 있는 경우에만 처리
            if summary.get("tables"):
                result = await processor.process_tables_from_summary(summary, block_id)
                total_created += len(result.get("created", []))
                total_updated += len(result.get("updated", []))
                total_refers_to += len(result.get("refers_to", []))
        
        log_nontarget("TABLE", f"테이블 처리 완료: 생성 {total_created}, 보강 {total_updated}, REFERS_TO {total_refers_to}")
        
        # 테이블 정보 메모리 동기화
        if total_created > 0 or total_updated > 0:
            # summaries에서 테이블 정보 수집
            all_tables = []
            all_relations = []
            for summary in summaries:
                for tbl in summary.get("tables", []):
                    all_tables.append(tbl)
                for rel in summary.get("relations", []):
                    all_relations.append(rel)
            
            # tables.md 업데이트
            update_topic_memory(
                topic="tables",
                new_info={"tables": all_tables, "relations": all_relations},
                trace_logger=ctx.trace_logger,
                merge_strategy="merge",
            )
            log_nontarget("MEMORY", f"tables.md 동기화 완료: {len(all_tables)}개 테이블, {len(all_relations)}개 관계")
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("process_tables", {
                **state,
                "tables_created": total_created,
                "tables_updated": total_updated,
                "refers_to_created": total_refers_to,
            })
        
        return {**state, "phase": "process_tables"}

    async def node_link(state: NontargetState) -> NontargetState:
        """REFERS_TO 관계 생성 (도구 연동)
        
        linker_agent가 도구를 사용하여:
        - lex_search_node_code로 키워드 기반 후보 탐색
        - semantic_search_summary로 의미적 후보 탐색
        - neo4j_cypher로 관계 검증 및 생성
        """
        log_workflow_state("link", state)
        
        blocks = state.get("blocks", [])
        summaries = state.get("summaries", [])
        
        # ctx에서 client, cypher_lock 가져오기 (클로저)
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if not client:
            return {**state, "phase": "link"}
        
        # 블록에 summary 정보 합치기
        blocks_with_summary = []
        for block, summary in zip(blocks, summaries):
            blocks_with_summary.append({**block, "summary": summary})
        
        # REFERS_TO 관계 생성 (ctx 전달하여 도구 사용)
        await create_links(blocks_with_summary, client, cypher_lock, ctx)
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("link", state)
        
        return {**state, "phase": "link"}

    async def node_enrich_targets(state: NontargetState) -> NontargetState:
        """연결된 타겟 노드 summary 보강
        
        REFERS_TO로 연결된 타겟 노드(METHOD, CLASS 등)의 summary를
        비타겟 분석 정보로 업데이트합니다.
        """
        log_workflow_state("enrich_targets", state)
        
        blocks = state.get("blocks", [])
        summaries = state.get("summaries", [])
        file_path = state.get("file_path", "")
        
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        trace_logger = ctx.trace_logger
        
        if not client:
            return {**state, "phase": "enrich_targets"}
        
        total_enriched = 0
        for i, (block, summary) in enumerate(zip(blocks, summaries)):
            block_id = block.get("id")
            if not block_id:
                continue
            
            # 타겟 노드 summary 보강
            result = await enrich_connected_targets(
                block_id=block_id,
                block_summary=summary,
                file_path=file_path,
                client=client,
                cypher_lock=cypher_lock,
                trace_logger=trace_logger,
            )
            total_enriched += len(result.get("updated_nodes", []))
        
        log_nontarget("ENRICH", f"타겟 노드 보강 완료: 총 {total_enriched}개 노드")
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("enrich_targets", state)
        
        return {**state, "phase": "enrich_targets"}

    async def node_from_writes(state: NontargetState) -> NontargetState:
        """FROM/WRITES 관계 생성
        
        REFERS_TO로 연결된 METHOD와 테이블 간의 관계를 생성:
        - access_mode: "r" → METHOD -[:FROM]-> Table
        - access_mode: "w" → METHOD -[:WRITES]-> Table
        """
        log_workflow_state("from_writes", state)
        
        blocks = state.get("blocks", [])
        summaries = state.get("summaries", [])
        
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        trace_logger = ctx.trace_logger
        
        if not client:
            return {**state, "phase": "from_writes"}
        
        total_from = 0
        total_writes = 0
        
        for block, summary in zip(blocks, summaries):
            block_id = block.get("id")
            tables = summary.get("tables", [])
            
            if not block_id or not tables:
                continue
            
            # FROM/WRITES 관계 생성
            result = await create_from_writes_relations(
                block_id=block_id,
                tables=tables,
                client=client,
                cypher_lock=cypher_lock,
                trace_logger=trace_logger,
            )
            total_from += result.get("from_count", 0)
            total_writes += result.get("writes_count", 0)
        
        log_nontarget("FROM-WRITES", f"FROM/WRITES 관계 생성 완료: FROM={total_from}, WRITES={total_writes}")
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("from_writes", {
                **state,
                "from_count": total_from,
                "writes_count": total_writes,
            })
        
        return {**state, "phase": "from_writes"}

    async def node_infer_fk(state: NontargetState) -> NontargetState:
        """컬럼명 기반 FK 관계 추론
        
        명시적 FK 정의가 없어도 컬럼명 패턴으로 FK 관계 추론:
        - {테이블명}Id 패턴: orderId → ORDER_HEADER 테이블 참조
        - 동일 컬럼명: ORDER_ITEM.orderId → ORDER_HEADER.orderId (PK)
        """
        log_workflow_state("infer_fk", state)
        
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if not client:
            log_nontarget("FK-INFER", "Neo4j 클라이언트 없음 - FK 추론 스킵")
            return {**state, "phase": "infer_fk"}
        
        processor = TableProcessor(
            client=client,
            cypher_lock=cypher_lock,
            trace_logger=ctx.trace_logger,
        )
        
        result = await processor.infer_fk_relations()
        
        log_nontarget("FK-INFER", 
                     f"FK 추론 완료: {len(result.get('inferred', []))}개 추론, "
                     f"{len(result.get('created', []))}개 생성")
        
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_node("infer_fk", {
                **state,
                "fk_inferred": len(result.get("inferred", [])),
                "fk_created": len(result.get("created", [])),
            })
        
        return {**state, "phase": "infer_fk"}

    def node_save_run(state: NontargetState) -> NontargetState:
        """mem/runs/에 실행 기록 저장"""
        log_workflow_state("save_run", state)
        
        file_path = state.get("file_path", "")
        rules = state.get("rules") or {}
        blocks_count = len(state.get("blocks", []))
        summaries = state.get("summaries", [])
        error = state.get("error")
        
        # 실행 기록 저장
        save_run_record(file_path, blocks_count, error)
        
        log_nontarget("RUN", f"실행 완료: {file_path} → {blocks_count}개 블록")
        
        # 파일 정보 메모리 업데이트
        import os
        update_topic_memory(
            topic="files",
            new_info={"files": [{
                "name": os.path.basename(file_path),
                "type": rules.get("file_type", "unknown"),
                "blocks": blocks_count,
            }]},
            trace_logger=ctx.trace_logger,
            merge_strategy="append",
        )
        
        # 관계 정보 수집 및 업데이트
        all_relations = []
        for summary in summaries:
            for rel in summary.get("relations", []):
                all_relations.append({
                    "type": "FK",
                    "source": rel.get("from_table", ""),
                    "target": rel.get("to_table", ""),
                })
        
        if all_relations:
            update_topic_memory(
                topic="relations",
                new_info={"relations": all_relations},
                trace_logger=ctx.trace_logger,
                merge_strategy="append",
            )
        
        # runs 30개 초과 시 요약 및 아카이브
        if should_update_overview():
            log_nontarget("RUN", "runs 30개 초과 - 요약 시작")
            summarize_runs_and_update_overview()
        
        # 트레이스 보고서 생성
        if ctx.trace_logger:
            ctx.trace_logger.log_workflow_complete(state, error)
            report = ctx.trace_logger.generate_report()
            log_nontarget("RUN", f"트레이스 보고서 생성: {ctx.trace_logger.get_trace_dir()}")
        
        return {**state, "phase": "complete"}
    
    return {
        "load_rules": node_load_rules,
        "sample": node_sample,
        "chunk": node_chunk,
        "summarize": node_summarize,
        "upsert": node_upsert,
        "process_tables": node_process_tables,
        "link": node_link,
        "enrich_targets": node_enrich_targets,
        "from_writes": node_from_writes,
        "infer_fk": node_infer_fk,
        "save_run": node_save_run,
    }


# ============================================================================
# LangGraph 앱 빌더
# ============================================================================

def build_langgraph_app(ctx: RuntimeCtx):
    """LangGraph 앱 구성 (Checkpoint 포함)
    
    Args:
        ctx: 런타임 컨텍스트
        
    Returns:
        컴파일된 LangGraph 앱
    """
    # 노드 함수 생성 (ctx 클로저)
    nodes = make_node_functions(ctx)
    
    # 그래프 정의
    graph = StateGraph(NontargetState)
    
    graph.add_node("load_rules", nodes["load_rules"])
    graph.add_node("sample", nodes["sample"])
    graph.add_node("chunk", nodes["chunk"])
    graph.add_node("summarize", nodes["summarize"])
    graph.add_node("upsert", nodes["upsert"])
    graph.add_node("process_tables", nodes["process_tables"])
    graph.add_node("link", nodes["link"])
    graph.add_node("enrich_targets", nodes["enrich_targets"])
    graph.add_node("from_writes", nodes["from_writes"])
    graph.add_node("infer_fk", nodes["infer_fk"])
    graph.add_node("save_run", nodes["save_run"])
    
    # 엣지 정의
    # load_rules → sample → chunk → summarize → upsert → process_tables → link → enrich_targets → from_writes → infer_fk → save_run
    graph.add_edge(START, "load_rules")
    graph.add_edge("load_rules", "sample")
    graph.add_edge("sample", "chunk")
    graph.add_edge("chunk", "summarize")
    graph.add_edge("summarize", "upsert")
    graph.add_edge("upsert", "process_tables")
    graph.add_edge("process_tables", "link")
    graph.add_edge("link", "enrich_targets")
    graph.add_edge("enrich_targets", "from_writes")
    graph.add_edge("from_writes", "infer_fk")
    graph.add_edge("infer_fk", "save_run")
    graph.add_edge("save_run", END)
    
    # Checkpoint로 상태 저장 (메모리 기반)
    checkpointer = MemorySaver()
    
    return graph.compile(checkpointer=checkpointer)


# ============================================================================
# 워크플로우 실행 함수
# ============================================================================

async def run_workflow(
    file_path: str,
    ctx: RuntimeCtx,
    thread_id: Optional[str] = None,
    enable_tracing: bool = True,
) -> NontargetState:
    """워크플로우 실행 (LangGraph + Checkpoint)
    
    Args:
        file_path: 대상 파일 경로
        ctx: 런타임 컨텍스트 (client, cypher_lock)
        thread_id: LangGraph 스레드 ID (재개용)
        enable_tracing: 상세 트레이스 로깅 활성화 (기본값: True)
        
    Returns:
        최종 상태
    """
    # TraceLogger 초기화 (트레이싱 활성화 시)
    if enable_tracing and ctx.trace_logger is None:
        ctx.trace_logger = TraceLogger(file_path)
    
    # State는 순수 데이터만 (client/lock 없음)
    initial_state: NontargetState = {
        "file_path": file_path,
        "rules": None,
        "blocks": [],
        "summaries": [],
        "phase": "init",
        "error": None,
    }
    
    # 워크플로우 시작 로깅
    if ctx.trace_logger:
        ctx.trace_logger.log_workflow_start(file_path, initial_state)
    
    # ctx를 사용해서 앱 빌드
    app = build_langgraph_app(ctx)
    
    log_nontarget("WORKFLOW", f"LangGraph 워크플로우 실행: {file_path}")
    
    # thread_id 생성 (없으면 파일 경로 기반)
    if not thread_id:
        safe_path = file_path.replace('/', '_').replace('\\', '_')
        thread_id = f"nontarget-{safe_path}"
    
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # ainvoke는 async 노드를 지원
        result = await app.ainvoke(initial_state, config=config)
        return result
    except Exception as e:
        # 에러 트레이싱
        if ctx.trace_logger:
            ctx.trace_logger.log_error(
                phase="workflow",
                error=str(e),
                detail={"file_path": file_path, "thread_id": thread_id}
            )
        raise


async def resume_workflow(
    thread_id: str,
    from_phase: str,
    ctx: RuntimeCtx,
) -> NontargetState:
    """워크플로우 재개 (A/B/C 수정용)
    
    Checkpoint에서 상태를 복원하고 지정된 단계부터 재실행합니다.
    
    Args:
        thread_id: 재개할 스레드 ID
        from_phase: 재개 시작 단계 ("load_rules", "chunk", "summarize", "link")
        ctx: 런타임 컨텍스트 (client, cypher_lock)
        
    Returns:
        최종 상태
        
    Raises:
        RuntimeError: 상태 조회/재개 실패
    """
    app = build_langgraph_app(ctx)
    config = {"configurable": {"thread_id": thread_id}}
    
    # 현재 상태 조회
    current_state = app.get_state(config)
    
    if not current_state:
        raise RuntimeError(f"스레드 {thread_id} 상태 없음")
    
    # 상태 업데이트 (재개 단계 설정) - client/lock은 더 이상 state에 없음
    app.update_state(config, {"phase": from_phase})
    
    # 재개 실행
    log_nontarget("WORKFLOW", f"워크플로우 재개: thread={thread_id}, from={from_phase}")
    result = await app.ainvoke(None, config=config)
    
    log_correction(
        correction_type={"load_rules": "A", "summarize": "B", "link": "C"}.get(from_phase, "?"),
        action="RESUME",
        file_path=result.get("file_path", "unknown"),
        reason=f"from_phase={from_phase}",
    )
    
    return result
