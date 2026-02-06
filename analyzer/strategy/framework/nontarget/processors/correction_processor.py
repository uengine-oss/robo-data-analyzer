"""A/B/C 수정 로직

[변경됨] 임계값 기반 detect() 제거 - 에이전트가 자율 판단
수정 실행 함수(correct())만 유지합니다.

에이전트가 분석 중 "아 이게 아니었구나" 판단하면
직접 이 모듈의 correct() 함수를 호출합니다.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import (
    log_nontarget,
    log_correction,
)
from analyzer.strategy.framework.nontarget.processors.chunk_processor import (
    chunk_by_rules,
    save_rules_to_memory,
    discover_patterns_iteratively,
)
from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    generate_summary_with_agent,
)

if TYPE_CHECKING:
    from analyzer.strategy.framework.nontarget.workflow import RuntimeCtx

# 수정 이력 파일 경로 (새 폴더 구조: mem/corrections/)
MODIFICATIONS_FILE = "mem/corrections/modifications.jsonl"


def _log_modification(
    correction_type: str,
    file_path: str,
    action: str,
    details: Dict[str, Any],
) -> None:
    """수정 이력 기록 (modifications.jsonl)"""
    os.makedirs(os.path.dirname(MODIFICATIONS_FILE), exist_ok=True)
    
    record = {
        "timestamp": datetime.now().isoformat(),
        "type": correction_type,
        "file_path": file_path,
        "action": action,
        "details": details,
    }
    
    try:
        with open(MODIFICATIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log_nontarget("CORRECTION", f"이력 기록: {correction_type} - {action}")
    except Exception as e:
        log_nontarget("CORRECTION", f"이력 기록 실패: {e}")


# ============================================================================
# A 수정: 블록화 오류 (Re-chunking)
# ============================================================================

class ACorrection:
    """A 수정: 블록화 오류 수정
    
    [제거됨] detect() - 에이전트가 자율 판단
    correct()만 유지 - 에이전트가 필요시 호출
    """
    
    # [제거됨] detect() - 하드코딩된 임계값 기반 감지 제거
    # 에이전트가 분석 중 "블록 경계가 이상하다" 판단하면 직접 correct() 호출
    
    @staticmethod
    async def correct(
        file_path: str,
        ctx: "RuntimeCtx",
        new_max_lines: int = 80,
    ) -> Dict[str, Any]:
        """A 수정 수행: 재블록화
        
        에이전트가 블록 경계 문제를 발견했을 때 호출합니다.
        """
        log_correction("A", "RE-CHUNK", file_path, f"max_lines={new_max_lines}")
        
        # 1. 패턴 재발견
        new_rules = discover_patterns_iteratively(file_path, ctx)
        new_rules["max_block_lines"] = new_max_lines
        new_rules["version"] = new_rules.get("version", 1) + 1
        
        # 2. 규칙 저장
        save_rules_to_memory(file_path, new_rules)
        
        # 3. 새 블록화
        new_blocks = chunk_by_rules(file_path, new_rules)
        
        # 4. Neo4j 업데이트 (기존 블록 삭제)
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if client:
            delete_query = """
                MATCH (b:NONTARGET_BLOCK {file_path: $file_path})
                DETACH DELETE b
            """
            try:
                if cypher_lock:
                    async with cypher_lock:
                        await client.execute_with_params(delete_query, {"file_path": file_path})
                else:
                    await client.execute_with_params(delete_query, {"file_path": file_path})
            except Exception as e:
                log_nontarget("A-CORRECT", f"기존 블록 삭제 실패: {e}")
        
        # 5. 이력 기록
        _log_modification("A", file_path, "RE-CHUNK", {
            "new_rules": new_rules,
            "blocks_count": len(new_blocks),
        })
        
        return {
            "success": True,
            "new_blocks": new_blocks,
            "new_rules": new_rules,
        }


# ============================================================================
# B 수정: 요약 오류 (Re-summarization)
# ============================================================================

class BCorrection:
    """B 수정: 요약 오류 수정
    
    [제거됨] detect() - 에이전트가 자율 판단
    correct()만 유지 - 에이전트가 필요시 호출
    """
    
    # [제거됨] detect() - 하드코딩된 키워드 비교 감지 제거
    # 에이전트가 "키워드가 누락됐다" 판단하면 직접 correct() 호출
    
    @staticmethod
    async def correct(
        block_id: str,
        block_code: str,
        file_type: str,
        ctx: "RuntimeCtx",
        feedback: str = "",
    ) -> Dict[str, Any]:
        """B 수정 수행: Summary 재생성
        
        에이전트가 요약 문제를 발견했을 때 호출합니다.
        """
        log_correction("B", "RE-SUMMARIZE", block_id, feedback or "summary 재생성")
        
        # 1. 새 Summary 생성
        new_summary = generate_summary_with_agent(block_code, file_type, ctx)
        
        # 2. Neo4j 업데이트
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if client and block_id:
            update_query = """
                MATCH (b:NONTARGET_BLOCK)
                WHERE elementId(b) = $block_id
                SET b.kind = $kind,
                    b.keywords = $keywords,
                    b.summary = $summary,
                    b.evidence = $evidence,
                    b.summary_version = COALESCE(b.summary_version, 0) + 1
                RETURN b
            """
            params = {
                "block_id": block_id,
                "kind": new_summary.get("kind", "unknown"),
                "keywords": new_summary.get("keywords", []),
                "summary": new_summary.get("summary", ""),
                "evidence": new_summary.get("evidence", ""),
            }
            
            try:
                if cypher_lock:
                    async with cypher_lock:
                        await client.execute_with_params(update_query, params)
                else:
                    await client.execute_with_params(update_query, params)
            except Exception as e:
                log_nontarget("B-CORRECT", f"Summary 업데이트 실패: {e}")
                return {"success": False, "error": str(e)}
        
        # 3. 이력 기록
        _log_modification("B", block_id, "RE-SUMMARIZE", {
            "feedback": feedback,
            "new_summary": new_summary,
        })
        
        return {
            "success": True,
            "new_summary": new_summary,
        }


# ============================================================================
# C 수정: 연결 오류 (Re-linking)
# ============================================================================

class CCorrection:
    """C 수정: 연결 오류 수정
    
    [제거됨] detect() - 에이전트가 자율 판단
    correct()만 유지 - 에이전트가 필요시 호출
    """
    
    # [제거됨] detect() - 하드코딩된 confidence 임계값 감지 제거
    # 에이전트가 "오연결이다" 판단하면 직접 correct() 호출
    
    @staticmethod
    async def correct(
        block_id: str,
        remove_link_ids: List[str],
        new_links: List[Dict[str, Any]],
        ctx: "RuntimeCtx",
    ) -> Dict[str, Any]:
        """C 수정 수행: 링크 교체
        
        에이전트가 연결 문제를 발견했을 때 호출합니다.
        """
        log_correction("C", "RE-LINK", block_id, f"remove={len(remove_link_ids)}, add={len(new_links)}")
        
        removed_count = 0
        created_count = 0
        
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if not client:
            return {"success": False, "error": "Neo4j 클라이언트 없음"}
        
        # 1. 기존 링크 삭제
        for link_id in remove_link_ids:
            delete_query = """
                MATCH ()-[r:REFERS_TO]->()
                WHERE elementId(r) = $link_id
                DELETE r
            """
            try:
                if cypher_lock:
                    async with cypher_lock:
                        await client.execute_with_params(delete_query, {"link_id": link_id})
                else:
                    await client.execute_with_params(delete_query, {"link_id": link_id})
                removed_count += 1
            except Exception as e:
                log_nontarget("C-CORRECT", f"링크 삭제 실패: {link_id} - {e}")
        
        # 2. 새 링크 생성
        for link in new_links:
            target_id = link.get("target_node_id")
            if not target_id:
                continue
            
            create_query = """
                MATCH (b:NONTARGET_BLOCK), (t)
                WHERE elementId(b) = $block_id AND elementId(t) = $target_id
                CREATE (t)-[r:REFERS_TO {
                    role: $role,
                    keyword: $keyword,
                    confidence: $confidence,
                    evidence: $evidence,
                    created_at: datetime()
                }]->(b)
                RETURN r
            """
            params = {
                "block_id": block_id,
                "target_id": target_id,
                "role": link.get("role", "refers"),
                "keyword": link.get("keyword", ""),
                "confidence": link.get("confidence", 0.5),
                "evidence": link.get("evidence", ""),
            }
            
            try:
                if cypher_lock:
                    async with cypher_lock:
                        await client.execute_with_params(create_query, params)
                else:
                    await client.execute_with_params(create_query, params)
                created_count += 1
            except Exception as e:
                log_nontarget("C-CORRECT", f"링크 생성 실패: {e}")
        
        # 3. 이력 기록
        _log_modification("C", block_id, "RE-LINK", {
            "removed_link_ids": remove_link_ids,
            "new_links": new_links,
            "removed_count": removed_count,
            "created_count": created_count,
        })
        
        return {
            "success": True,
            "removed": removed_count,
            "created": created_count,
        }


# ============================================================================
# 통합 수정 함수
# ============================================================================

async def run_correction(
    correction_type: str,
    file_path: str,
    ctx: "RuntimeCtx",
    **kwargs,
) -> Dict[str, Any]:
    """통합 수정 실행
    
    에이전트가 수정 필요 판단 시 이 함수를 호출합니다.
    """
    if correction_type == "A":
        return await ACorrection.correct(
            file_path=file_path,
            ctx=ctx,
            new_max_lines=kwargs.get("new_max_lines", 80),
        )
    
    elif correction_type == "B":
        return await BCorrection.correct(
            block_id=kwargs.get("block_id", ""),
            block_code=kwargs.get("block_code", ""),
            file_type=kwargs.get("file_type", "unknown"),
            ctx=ctx,
            feedback=kwargs.get("feedback", ""),
        )
    
    elif correction_type == "C":
        return await CCorrection.correct(
            block_id=kwargs.get("block_id", ""),
            remove_link_ids=kwargs.get("remove_link_ids", []),
            new_links=kwargs.get("new_links", []),
            ctx=ctx,
        )
    
    else:
        return {"success": False, "error": f"알 수 없는 수정 유형: {correction_type}"}


def get_modification_history(
    file_path: Optional[str] = None,
    correction_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """수정 이력 조회"""
    if not os.path.exists(MODIFICATIONS_FILE):
        return []
    
    history: List[Dict[str, Any]] = []
    
    try:
        with open(MODIFICATIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                
                if file_path and record.get("file_path") != file_path:
                    continue
                if correction_type and record.get("type") != correction_type:
                    continue
                
                history.append(record)
                
                if len(history) >= limit:
                    break
    except Exception:
        pass
    
    return history
