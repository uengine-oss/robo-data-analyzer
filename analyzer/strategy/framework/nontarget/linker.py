"""REFERS_TO 관계 생성

비타겟 블록과 타겟 노드 간의 연결을 생성합니다.
linker_agent.yaml 프롬프트와 Neo4j/검색 도구를 사용하여 
LLM 기반 연결 품질 평가 및 관계 생성을 수행합니다.

도구 활용:
- lex_search_node_code: 키워드 기반 후보 탐색
- semantic_search_summary: 의미적 후보 탐색
- neo4j_cypher: 관계 검증 및 생성
"""

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.nontarget_logger import log_nontarget, log_agent_action
from analyzer.strategy.framework.nontarget.deepagents_client import (
    DeepAgentsClient,
    _extract_agent_output,
    _parse_json_from_output,
)


# ============================================================================
# 연결 생성 함수
# ============================================================================

async def create_links(
    blocks: List[Dict[str, Any]],
    client: Any,
    cypher_lock: Optional[Any] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """블록에 대한 REFERS_TO 관계 생성 - 에이전트가 도구로 자율 탐색
    
    ⚠️ linker_agent가 도구를 직접 사용하여 자율적으로:
    1. read_memory로 기존 정보 확인
    2. lex_search_node_code로 키워드 기반 후보 탐색
    3. semantic_search_summary로 의미적 후보 탐색
    4. neo4j_cypher로 관계 추적 (USES/CALLS/PARENT_OF)
    5. neo4j_cypher로 REFERS_TO 관계 생성
    6. write_memory로 결과 저장
    
    Args:
        blocks: 블록 리스트 (summary 포함)
        client: Neo4j 클라이언트
        cypher_lock: Cypher 쿼리 락
        ctx: RuntimeCtx (도구 사용에 필요)
        
    Returns:
        생성 결과 {created_count, relations: [...]}
    """
    if not client:
        log_nontarget("LINK", "Neo4j 클라이언트 없음 - 스킵")
        return {"created_count": 0, "relations": []}
    
    # ctx가 없으면 생성
    if ctx is None:
        from analyzer.strategy.framework.nontarget.workflow import RuntimeCtx
        ctx = RuntimeCtx(client=client, cypher_lock=cypher_lock)
    
    created_count = 0
    all_relations = []
    
    for block in blocks:
        block_id = block.get("id")
        summary_data = block.get("summary", {})
        keywords = summary_data.get("keywords", [])
        
        if not block_id or not keywords:
            continue
        
        # ✅ 에이전트가 도구를 사용하여 자율적으로 후보 탐색 + 평가 + 관계 생성
        try:
            result = await _agent_autonomous_link(
                block=block,
                ctx=ctx,
            )
            
            created_count += result.get("created_count", 0)
            all_relations.extend(result.get("relations", []))
            
        except Exception as e:
            log_nontarget("LINK", f"linker_agent 자율 탐색 실패 - 중단: {e}")
            raise RuntimeError(f"linker_agent 자율 탐색 실패: {e}") from e
    
    log_nontarget("LINK", f"REFERS_TO 관계 생성 완료: {created_count}개")
    return {"created_count": created_count, "relations": all_relations}


async def _agent_autonomous_link(
    block: Dict[str, Any],
    ctx: Any,
) -> Dict[str, Any]:
    """linker_agent가 도구를 자율적으로 사용하여 관계 생성
    
    에이전트가 yaml 프롬프트에 따라:
    1. 메모리에서 기존 정보 확인 (read_memory)
    2. 키워드로 관련 노드 탐색 (lex_search_node_code, semantic_search_summary)
    3. 관계 체인 추적 (neo4j_cypher로 USES/CALLS/PARENT_OF)
    4. REFERS_TO 관계 생성 (neo4j_cypher)
    5. 결과 저장 (write_memory)
    
    Args:
        block: 비타겟 블록 정보
        ctx: RuntimeCtx (도구 사용에 필요)
        
    Returns:
        {created_count, relations: [...]}
    """
    import time
    
    log_agent_action("linker_agent", "autonomous_link", {"block_id": block.get("id", "")})
    
    block_id = block.get("id", "")
    summary_data = block.get("summary", {})
    keywords = summary_data.get("keywords", [])
    summary_text = summary_data.get("summary", "")
    kind = summary_data.get("kind", "unknown")
    code = block.get("code", "")[:4000]
    
    # yaml 템플릿 변수 준비
    template_vars = {
        "block_id": block_id,
        "kind": kind,
        "summary_text": summary_text,
        "keywords": keywords,
        "code": code,
    }
    
    client = DeepAgentsClient()
    agent = client.create_agent("linker_agent", ctx=ctx, template_vars=template_vars)
    
    # 에이전트 실행 - yaml 프롬프트에 이미 모든 지침이 포함됨
    user_input = "위 입력 데이터를 기반으로 REFERS_TO 관계를 탐색하고 생성하세요."
    
    start_time = time.time()
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_input)]})
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    output = _extract_agent_output(result)
    
    try:
        parsed = _parse_json_from_output(output)
        created_relations = parsed.get("created_relations", [])
        
        # 트레이싱
        if ctx and hasattr(ctx, 'trace_logger') and ctx.trace_logger:
            ctx.trace_logger.log_agent_call(
                agent_name="linker_agent",
                prompt=user_input,
                response=output,
                tokens_used=0,
                duration_ms=elapsed_ms,
                purpose="NONTARGET_BLOCK 관련 노드 자율 탐색 및 REFERS_TO 생성",
                input_context=f"블록 ID: {block_id}, 키워드: {len(keywords)}개",
                parsed_result=parsed,
                reasoning=parsed.get("notes", ""),
            )
            
            # 생성된 관계 로깅
            for rel in created_relations:
                ctx.trace_logger.log_refers_to_created(
                    source_id=rel.get("target_node_id", ""),
                    source_type=rel.get("target_type", "unknown"),
                    target_id=block_id,
                    target_type="NONTARGET_BLOCK",
                    role=rel.get("role", "reads"),
                    confidence=rel.get("confidence", 0.5),
                    evidence=rel.get("evidence", ""),
                )
        
        log_nontarget("LINKER", f"자율 탐색 완료: {len(created_relations)}개 관계 생성")
        
        return {
            "created_count": len(created_relations),
            "relations": created_relations,
        }
        
    except Exception as e:
        log_nontarget("LINKER", f"JSON 파싱 실패: {e}")
        
        if ctx and hasattr(ctx, 'trace_logger') and ctx.trace_logger:
            ctx.trace_logger.log_error(
                phase="linker_autonomous",
                error=str(e),
                detail={"block_id": block_id}
            )
        
        raise RuntimeError(f"linker_agent 결과 파싱 실패: {e}") from e


async def _create_single_link(
    block_id: str,
    target_node_id: str,
    keyword: str,
    confidence: float,
    evidence: str,
    role: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> int:
    """단일 REFERS_TO 관계 생성
    
    Returns:
        생성된 관계 수 (0 또는 1)
    """
    query = """
        MATCH (b:NONTARGET_BLOCK), (t)
        WHERE elementId(b) = $block_id AND elementId(t) = $target_node_id
        MERGE (t)-[r:REFERS_TO {keyword: $keyword}]->(b)
        SET r.confidence = $confidence,
            r.evidence = $evidence,
            r.role = $role
        RETURN r
    """
    params = {
        "block_id": block_id,
        "target_node_id": target_node_id,
        "keyword": keyword,
        "confidence": confidence,
        "evidence": evidence,
        "role": role,
    }
    
    try:
        if cypher_lock:
            async with cypher_lock:
                result = await client.execute_with_params(query, params)
        else:
            result = await client.execute_with_params(query, params)
        
        return 1 if result else 0
    except Exception as e:
        log_nontarget("LINK", f"연결 생성 실패: {e}")
        return 0


async def _create_link_by_keyword(
    block_id: str,
    keyword: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> int:
    """키워드 기반 연결 생성 (relink용)
    
    Returns:
        생성된 관계 수
    """
    query = """
        MATCH (b:NONTARGET_BLOCK) WHERE elementId(b) = $block_id
        MATCH (n) 
        WHERE (n:METHOD OR n:CLASS OR n:FIELD)
          AND n.node_code CONTAINS $keyword
        WITH b, n, 
             CASE WHEN n.node_code CONTAINS $keyword THEN 0.6 ELSE 0.4 END AS conf
        MERGE (n)-[r:REFERS_TO {role: 'reads', keyword: $keyword}]->(b)
        SET r.confidence = conf
        RETURN r
    """
    params = {"block_id": block_id, "keyword": keyword}
    
    try:
        if cypher_lock:
            async with cypher_lock:
                result = await client.execute_with_params(query, params)
        else:
            result = await client.execute_with_params(query, params)
        
        return len(result) if result else 0
    except Exception as e:
        log_nontarget("LINK", f"키워드 연결 생성 실패: {e}")
        return 0


# ============================================================================
# 링크 재생성 (C 수정용)
# ============================================================================

async def relink(
    block_id: str,
    old_links: List[str],
    new_keywords: List[str],
    client: Any,
    cypher_lock: Optional[Any] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, int]:
    """링크 재생성 (C 수정)
    
    기존 링크를 삭제하고 새 링크를 생성합니다.
    
    Args:
        block_id: 블록 ID
        old_links: 삭제할 기존 링크 ID 리스트
        new_keywords: 새로 연결할 키워드 리스트
        client: Neo4j 클라이언트
        cypher_lock: Cypher 쿼리 락
        ctx: RuntimeCtx (선택)
        
    Returns:
        결과 딕셔너리 {removed: int, created: int}
    """
    if not client:
        log_nontarget("LINK", "Neo4j 클라이언트 없음 - 스킵")
        return {"removed": 0, "created": 0}
    
    removed_count = 0
    created_count = 0
    
    try:
        # 1. 기존 링크 삭제
        for link_id in old_links:
            delete_query = """
                MATCH ()-[r:REFERS_TO]->()
                WHERE elementId(r) = $link_id
                DELETE r
                RETURN count(r) AS cnt
            """
            if cypher_lock:
                async with cypher_lock:
                    await client.execute_with_params(delete_query, {"link_id": link_id})
            else:
                await client.execute_with_params(delete_query, {"link_id": link_id})
            removed_count += 1
        
        # 2. 새 링크 생성 (키워드 기반)
        for keyword in new_keywords:
            count = await _create_link_by_keyword(block_id, keyword, client, cypher_lock)
            created_count += count
        
        log_nontarget("LINK", f"링크 재생성 완료: 삭제 {removed_count}개, 생성 {created_count}개")
    except Exception as e:
        log_nontarget("LINK", f"링크 재생성 실패: {e}")
    
    return {"removed": removed_count, "created": created_count}


# ============================================================================
# 타겟 노드 Summary 업데이트
# ============================================================================

async def update_target_node_summaries(
    block_id: str,
    nontarget_file_path: str,
    nontarget_summary: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> Dict[str, Any]:
    """REFERS_TO로 연결된 타겟 노드의 summary를 보강
    
    비타겟 분석 결과를 타겟 노드의 summary에 추가하여
    타겟 노드가 어떤 외부 설정/매핑에서 사용되는지 기록합니다.
    
    Args:
        block_id: 비타겟 블록 elementId
        nontarget_file_path: 비타겟 파일 경로
        nontarget_summary: 비타겟 블록 summary
        client: Neo4j 클라이언트
        cypher_lock: Cypher 락
        
    Returns:
        업데이트 결과 {updated_nodes: [], failed_nodes: []}
    """
    result = {
        "updated_nodes": [],
        "failed_nodes": [],
    }
    
    # 1. REFERS_TO로 연결된 타겟 노드 조회
    find_targets_query = """
        MATCH (b:NONTARGET_BLOCK)-[r:REFERS_TO]->(target)
        WHERE elementId(b) = $block_id
          AND (target:METHOD OR target:CLASS OR target:FIELD OR target:INTERFACE)
        RETURN elementId(target) AS target_id,
               labels(target) AS labels,
               target.name AS name,
               target.summary AS current_summary,
               r.role AS role
    """
    
    try:
        if cypher_lock:
            async with cypher_lock:
                targets = await client.execute_with_params(find_targets_query, {"block_id": block_id})
        else:
            targets = await client.execute_with_params(find_targets_query, {"block_id": block_id})
    except Exception as e:
        log_nontarget("ENRICH", f"타겟 노드 조회 실패: {e}")
        return result
    
    if not targets:
        return result
    
    # 2. 각 타겟 노드의 summary 업데이트
    # 추가할 정보 형식: "[nontarget] {파일명}에서 {역할}로 참조"
    file_name = nontarget_file_path.split("/")[-1].split("\\")[-1]
    
    for target in targets:
        target_id = target.get("target_id")
        target_name = target.get("name", "")
        current_summary = target.get("current_summary", "") or ""
        role = target.get("role", "references")
        labels = target.get("labels", [])
        
        # 이미 이 파일에 대한 정보가 있으면 스킵
        if f"[nontarget] {file_name}" in current_summary:
            continue
        
        # 새로운 정보 추가
        new_info = f"[nontarget] {file_name}에서 {role}로 참조."
        
        # summary 업데이트
        update_query = """
            MATCH (target) WHERE elementId(target) = $target_id
            SET target.summary = CASE
                WHEN target.summary IS NULL OR target.summary = '' THEN $new_info
                ELSE target.summary + ' ' + $new_info
            END,
            target.nontarget_refs = CASE
                WHEN target.nontarget_refs IS NULL THEN [$file_name]
                WHEN NOT $file_name IN target.nontarget_refs THEN target.nontarget_refs + $file_name
                ELSE target.nontarget_refs
            END
            RETURN target.summary AS updated_summary
        """
        params = {
            "target_id": target_id,
            "new_info": new_info,
            "file_name": file_name,
        }
        
        try:
            if cypher_lock:
                async with cypher_lock:
                    update_result = await client.execute_with_params(update_query, params)
            else:
                update_result = await client.execute_with_params(update_query, params)
            
            result["updated_nodes"].append({
                "id": target_id,
                "name": target_name,
                "labels": labels,
                "new_info": new_info,
            })
            log_nontarget("ENRICH", f"타겟 노드 summary 업데이트: {labels[0] if labels else ''}.{target_name}")
            
        except Exception as e:
            log_nontarget("ENRICH", f"타겟 노드 summary 업데이트 실패: {target_name} - {e}")
            result["failed_nodes"].append({
                "id": target_id,
                "name": target_name,
                "error": str(e),
            })
    
    log_nontarget("ENRICH", f"타겟 노드 보강 완료: 업데이트 {len(result['updated_nodes'])}개")
    return result


# ============================================================================
# USES 관계 통합 (다중 → 단일 + 속성 배열)
# ============================================================================

async def create_merged_uses_relation(
    source_id: str,
    target_id: str,
    line: int,
    context: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> Dict[str, Any]:
    """USES 관계를 통합 방식으로 생성/업데이트
    
    기존 USES 관계가 있으면 속성만 추가하고,
    없으면 새로 생성합니다.
    
    Args:
        source_id: 소스 노드 elementId (METHOD 등)
        target_id: 타겟 노드 elementId (Column 등)
        line: 사용 위치 라인 번호
        context: 사용 컨텍스트 (예: "SELECT", "WHERE", "UPDATE")
        client: Neo4j 클라이언트
        cypher_lock: Cypher 락
        
    Returns:
        결과 {action: "created" | "updated", usage_count: int}
    """
    # MERGE로 관계 생성/업데이트, 속성 배열에 추가
    query = """
        MATCH (source) WHERE elementId(source) = $source_id
        MATCH (target) WHERE elementId(target) = $target_id
        MERGE (source)-[r:USES]->(target)
        ON CREATE SET
            r.usage_count = 1,
            r.lines = [$line],
            r.contexts = [$context],
            r.first_line = $line,
            r.last_line = $line,
            r.source = 'nontarget'
        ON MATCH SET
            r.usage_count = r.usage_count + 1,
            r.lines = CASE
                WHEN $line IN r.lines THEN r.lines
                ELSE r.lines + $line
            END,
            r.contexts = CASE
                WHEN $context IN r.contexts THEN r.contexts
                ELSE r.contexts + $context
            END,
            r.first_line = CASE
                WHEN r.first_line IS NULL OR $line < r.first_line THEN $line
                ELSE r.first_line
            END,
            r.last_line = CASE
                WHEN r.last_line IS NULL OR $line > r.last_line THEN $line
                ELSE r.last_line
            END
        RETURN r.usage_count AS usage_count,
               CASE WHEN r.usage_count = 1 THEN 'created' ELSE 'updated' END AS action
    """
    params = {
        "source_id": source_id,
        "target_id": target_id,
        "line": line,
        "context": context,
    }
    
    try:
        if cypher_lock:
            async with cypher_lock:
                result = await client.execute_with_params(query, params)
        else:
            result = await client.execute_with_params(query, params)
        
        if result and len(result) > 0:
            return {
                "action": result[0].get("action", "unknown"),
                "usage_count": result[0].get("usage_count", 1),
            }
        return {"action": "failed", "usage_count": 0}
        
    except Exception as e:
        log_nontarget("USES", f"USES 관계 생성/업데이트 실패: {e}")
        return {"action": "error", "usage_count": 0, "error": str(e)}


async def migrate_uses_relations(
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> Dict[str, Any]:
    """기존 다중 USES 관계를 통합 USES로 마이그레이션
    
    같은 소스-타겟 간의 여러 USES 관계를 하나로 통합합니다.
    
    Returns:
        마이그레이션 결과 {migrated_count, error_count}
    """
    # 1. 중복 USES 관계 찾기
    find_duplicates_query = """
        MATCH (source)-[r:USES]->(target)
        WITH source, target, collect(r) AS rels, count(r) AS rel_count
        WHERE rel_count > 1
        RETURN elementId(source) AS source_id,
               elementId(target) AS target_id,
               rel_count,
               [r IN rels | {id: elementId(r), line: r.line, context: r.context}] AS rel_infos
        LIMIT 100
    """
    
    result = {
        "migrated_count": 0,
        "error_count": 0,
    }
    
    try:
        if cypher_lock:
            async with cypher_lock:
                duplicates = await client.execute_with_params(find_duplicates_query, {})
        else:
            duplicates = await client.execute_with_params(find_duplicates_query, {})
    except Exception as e:
        log_nontarget("USES-MIGRATE", f"중복 USES 조회 실패: {e}")
        return result
    
    if not duplicates:
        log_nontarget("USES-MIGRATE", "중복 USES 관계 없음")
        return result
    
    # 2. 각 중복 그룹 통합
    for dup in duplicates:
        source_id = dup.get("source_id")
        target_id = dup.get("target_id")
        rel_infos = dup.get("rel_infos", [])
        
        if len(rel_infos) < 2:
            continue
        
        # 첫 번째 관계 유지, 나머지는 삭제하면서 정보 통합
        lines = []
        contexts = []
        rel_ids_to_delete = []
        
        for i, rel in enumerate(rel_infos):
            if rel.get("line"):
                lines.append(rel.get("line"))
            if rel.get("context"):
                contexts.append(rel.get("context"))
            if i > 0:  # 첫 번째 제외
                rel_ids_to_delete.append(rel.get("id"))
        
        # 첫 번째 관계 업데이트
        first_rel_id = rel_infos[0].get("id")
        update_query = """
            MATCH ()-[r:USES]->()
            WHERE elementId(r) = $rel_id
            SET r.usage_count = $usage_count,
                r.lines = $lines,
                r.contexts = $contexts,
                r.first_line = $first_line,
                r.last_line = $last_line,
                r.migrated = true
            RETURN r
        """
        update_params = {
            "rel_id": first_rel_id,
            "usage_count": len(rel_infos),
            "lines": [l for l in lines if l],
            "contexts": list(set([c for c in contexts if c])),
            "first_line": min([l for l in lines if l]) if lines else 0,
            "last_line": max([l for l in lines if l]) if lines else 0,
        }
        
        try:
            if cypher_lock:
                async with cypher_lock:
                    await client.execute_with_params(update_query, update_params)
            else:
                await client.execute_with_params(update_query, update_params)
            
            # 나머지 관계 삭제
            for rel_id in rel_ids_to_delete:
                delete_query = """
                    MATCH ()-[r:USES]->()
                    WHERE elementId(r) = $rel_id
                    DELETE r
                """
                if cypher_lock:
                    async with cypher_lock:
                        await client.execute_with_params(delete_query, {"rel_id": rel_id})
                else:
                    await client.execute_with_params(delete_query, {"rel_id": rel_id})
            
            result["migrated_count"] += 1
            
        except Exception as e:
            log_nontarget("USES-MIGRATE", f"USES 통합 실패: {e}")
            result["error_count"] += 1
    
    log_nontarget("USES-MIGRATE", f"USES 마이그레이션 완료: {result['migrated_count']}개 통합")
    return result


# ============================================================================
# FROM/WRITES 관계 생성 (테이블 ↔ METHOD)
# ============================================================================

async def create_from_writes_relations(
    block_id: str,
    tables: List[Dict[str, Any]],
    client: Any,
    cypher_lock: Optional[Any] = None,
    trace_logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """비타겟 분석 기반 FROM/WRITES 관계 생성
    
    NONTARGET_BLOCK → REFERS_TO → METHOD 관계를 기반으로
    METHOD → FROM/WRITES → Table 관계를 생성합니다.
    
    Args:
        block_id: 비타겟 블록 elementId
        tables: summarizer에서 추출한 테이블 정보 (access_mode 포함)
        client: Neo4j 클라이언트
        cypher_lock: Cypher 락
        trace_logger: 트레이스 로거
        
    Returns:
        생성 결과 {from_count, writes_count, relations: [...]}
    """
    result = {
        "from_count": 0,
        "writes_count": 0,
        "relations": [],
    }
    
    if not tables or not client:
        return result
    
    for table_data in tables:
        table_name = table_data.get("name", "").strip()
        entity_name = table_data.get("entity_name", "").strip()
        access_mode = table_data.get("access_mode", "r").lower()
        
        if not table_name:
            continue
        
        # 1. 테이블 노드 찾기 (name 또는 entity_name으로)
        find_table_query = """
            MATCH (t:Table)
            WHERE toLower(t.name) = toLower($table_name)
               OR toLower(coalesce(t.entity_name, '')) = toLower($table_name)
               OR ($entity_name <> '' AND toLower(coalesce(t.entity_name, '')) = toLower($entity_name))
            RETURN elementId(t) AS table_id, t.name AS table_name
            LIMIT 1
        """
        params = {"table_name": table_name, "entity_name": entity_name or ""}
        
        try:
            if cypher_lock:
                async with cypher_lock:
                    table_result = await client.execute_with_params(find_table_query, params)
            else:
                table_result = await client.execute_with_params(find_table_query, params)
            
            if not table_result:
                continue
            
            table_id = table_result[0].get("table_id")
            actual_table_name = table_result[0].get("table_name", table_name)
            
        except Exception as e:
            log_nontarget("FROM-WRITES", f"테이블 조회 실패: {table_name} - {e}")
            continue
        
        # 2. NONTARGET_BLOCK과 REFERS_TO로 연결된 METHOD 찾기
        find_methods_query = """
            MATCH (b:NONTARGET_BLOCK) WHERE elementId(b) = $block_id
            MATCH (m:METHOD)-[:REFERS_TO]->(b)
            RETURN elementId(m) AS method_id, m.name AS method_name
        """
        
        try:
            if cypher_lock:
                async with cypher_lock:
                    methods = await client.execute_with_params(find_methods_query, {"block_id": block_id})
            else:
                methods = await client.execute_with_params(find_methods_query, {"block_id": block_id})
        except Exception as e:
            log_nontarget("FROM-WRITES", f"METHOD 조회 실패: {e}")
            methods = []
        
        # 연결된 METHOD가 없으면 스킵
        if not methods:
            log_nontarget("FROM-WRITES", f"테이블 {actual_table_name}에 연결된 METHOD 없음")
            continue
        
        # 3. access_mode에 따라 FROM/WRITES 관계 생성
        for method in methods:
            method_id = method.get("method_id")
            method_name = method.get("method_name", "")
            
            if not method_id:
                continue
            
            # "r" 포함 → FROM 관계
            if "r" in access_mode:
                from_created = await _create_from_relation(
                    method_id=method_id,
                    table_id=table_id,
                    source="nontarget",
                    client=client,
                    cypher_lock=cypher_lock,
                )
                if from_created:
                    result["from_count"] += 1
                    result["relations"].append({
                        "type": "FROM",
                        "method": method_name,
                        "table": actual_table_name,
                    })
                    log_nontarget("FROM-WRITES", f"FROM 관계 생성: {method_name} → {actual_table_name}")
                    
                    if trace_logger:
                        trace_logger.log_refers_to_created(
                            source_id=method_id,
                            source_type="METHOD",
                            target_id=table_id,
                            target_type="Table",
                            role="FROM",
                            confidence=0.8,
                            evidence=f"비타겟 분석: access_mode={access_mode}",
                        )
            
            # "w" 포함 → WRITES 관계
            if "w" in access_mode:
                writes_created = await _create_writes_relation(
                    method_id=method_id,
                    table_id=table_id,
                    source="nontarget",
                    client=client,
                    cypher_lock=cypher_lock,
                )
                if writes_created:
                    result["writes_count"] += 1
                    result["relations"].append({
                        "type": "WRITES",
                        "method": method_name,
                        "table": actual_table_name,
                    })
                    log_nontarget("FROM-WRITES", f"WRITES 관계 생성: {method_name} → {actual_table_name}")
                    
                    if trace_logger:
                        trace_logger.log_refers_to_created(
                            source_id=method_id,
                            source_type="METHOD",
                            target_id=table_id,
                            target_type="Table",
                            role="WRITES",
                            confidence=0.8,
                            evidence=f"비타겟 분석: access_mode={access_mode}",
                        )
    
    log_nontarget("FROM-WRITES", f"FROM/WRITES 관계 생성 완료: FROM={result['from_count']}, WRITES={result['writes_count']}")
    return result


async def _create_from_relation(
    method_id: str,
    table_id: str,
    source: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> bool:
    """METHOD → FROM → Table 관계 생성"""
    query = """
        MATCH (m:METHOD) WHERE elementId(m) = $method_id
        MATCH (t:Table) WHERE elementId(t) = $table_id
        MERGE (m)-[r:FROM]->(t)
        ON CREATE SET r.source = $source, r.created_at = datetime()
        ON MATCH SET r.source = CASE
            WHEN r.source IS NULL THEN $source
            WHEN NOT r.source CONTAINS $source THEN r.source + ',' + $source
            ELSE r.source
        END
        RETURN r
    """
    params = {"method_id": method_id, "table_id": table_id, "source": source}
    
    try:
        if cypher_lock:
            async with cypher_lock:
                result = await client.execute_with_params(query, params)
        else:
            result = await client.execute_with_params(query, params)
        return bool(result)
    except Exception as e:
        log_nontarget("FROM-WRITES", f"FROM 관계 생성 실패: {e}")
        return False


async def _create_writes_relation(
    method_id: str,
    table_id: str,
    source: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
) -> bool:
    """METHOD → WRITES → Table 관계 생성"""
    query = """
        MATCH (m:METHOD) WHERE elementId(m) = $method_id
        MATCH (t:Table) WHERE elementId(t) = $table_id
        MERGE (m)-[r:WRITES]->(t)
        ON CREATE SET r.source = $source, r.created_at = datetime()
        ON MATCH SET r.source = CASE
            WHEN r.source IS NULL THEN $source
            WHEN NOT r.source CONTAINS $source THEN r.source + ',' + $source
            ELSE r.source
        END
        RETURN r
    """
    params = {"method_id": method_id, "table_id": table_id, "source": source}
    
    try:
        if cypher_lock:
            async with cypher_lock:
                result = await client.execute_with_params(query, params)
        else:
            result = await client.execute_with_params(query, params)
        return bool(result)
    except Exception as e:
        log_nontarget("FROM-WRITES", f"WRITES 관계 생성 실패: {e}")
        return False


async def enrich_connected_targets(
    block_id: str,
    block_summary: Dict[str, Any],
    file_path: str,
    client: Any,
    cypher_lock: Optional[Any] = None,
    trace_logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """연결된 모든 타겟 노드 보강 (summary 업데이트)
    
    Args:
        block_id: 비타겟 블록 elementId
        block_summary: 비타겟 블록 summary
        file_path: 비타겟 파일 경로
        client: Neo4j 클라이언트
        cypher_lock: Cypher 락
        trace_logger: 트레이스 로거
        
    Returns:
        보강 결과
    """
    summary_text = block_summary.get("summary", "")
    
    result = await update_target_node_summaries(
        block_id=block_id,
        nontarget_file_path=file_path,
        nontarget_summary=summary_text,
        client=client,
        cypher_lock=cypher_lock,
    )
    
    if trace_logger and result.get("updated_nodes"):
        for node in result["updated_nodes"]:
            trace_logger.log_refers_to_created(
                source_id=block_id,
                source_type="NONTARGET_BLOCK",
                target_id=node.get("id", ""),
                target_type=node.get("labels", ["unknown"])[0],
                role="summary_enriched",
                confidence=1.0,
                evidence=node.get("new_info", ""),
            )
    
    return result
