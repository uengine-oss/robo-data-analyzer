"""A/B/C 수정 로직

블록화(A), 요약(B), 연결(C) 오류를 감지하고 수정합니다.
수정 이력은 modifications.jsonl에 기록됩니다.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from analyzer.strategy.framework.nontarget.nontarget_logger import (
    log_nontarget,
    log_correction,
)
from analyzer.strategy.framework.nontarget.chunker import (
    chunk_by_rules,
    save_rules_to_memory,
    discover_patterns_iteratively,
)
from analyzer.strategy.framework.nontarget.deepagents_client import (
    generate_summary_with_agent,
)
from analyzer.strategy.framework.nontarget.workflow import RuntimeCtx

# 수정 이력 파일 경로
MODIFICATIONS_FILE = "data/modifications.jsonl"


def _log_modification(
    correction_type: str,
    file_path: str,
    action: str,
    details: Dict[str, Any],
) -> None:
    """수정 이력 기록 (modifications.jsonl)
    
    Args:
        correction_type: 수정 유형 (A, B, C)
        file_path: 대상 파일 경로
        action: 수행된 작업
        details: 상세 정보
    """
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
    
    문제 유형:
    - 블록이 너무 큼 (문맥 섞임)
    - 블록이 너무 작음 (정보 분산)
    - 경계가 어색함 (키가 블록 사이에 찢어짐)
    """
    
    @staticmethod
    def detect(
        file_path: str,
        blocks: List[Dict[str, Any]],
        threshold_count: int = 300,
    ) -> bool:
        """A 오류 감지
        
        Args:
            file_path: 파일 경로
            blocks: 현재 블록 리스트
            threshold_count: 블록 수 임계값
            
        Returns:
            오류 감지 여부
        """
        # 블록 수가 너무 많음
        if len(blocks) > threshold_count:
            log_nontarget("A-DETECT", f"블록 수 초과: {len(blocks)} > {threshold_count}")
            return True
        
        # 평균 블록 크기가 너무 작음 (10줄 미만)
        if blocks:
            avg_lines = sum(
                b.get("end_line", 0) - b.get("start_line", 0) + 1
                for b in blocks
            ) / len(blocks)
            if avg_lines < 10:
                log_nontarget("A-DETECT", f"평균 블록 크기 너무 작음: {avg_lines:.1f}줄")
                return True
        
        return False
    
    @staticmethod
    async def correct(
        file_path: str,
        ctx: RuntimeCtx,
        new_max_lines: int = 80,
    ) -> Dict[str, Any]:
        """A 수정 수행: 재블록화
        
        Args:
            file_path: 파일 경로
            ctx: 런타임 컨텍스트 (client, cypher_lock)
            new_max_lines: 새 블록 최대 라인 수
            
        Returns:
            수정 결과 {success, blocks_before, blocks_after}
        """
        log_correction("A", "RE-CHUNK", file_path, f"max_lines={new_max_lines}")
        
        # 1. 반복 샘플링으로 패턴 재발견 (신 방식)
        new_rules = discover_patterns_iteratively(file_path, ctx)
        new_rules["max_block_lines"] = new_max_lines
        new_rules["version"] = new_rules.get("version", 1) + 1
        
        # 2. 규칙 저장
        save_rules_to_memory(file_path, new_rules)
        
        # 3. 새 블록화
        new_blocks = chunk_by_rules(file_path, new_rules)
        
        # 4. Neo4j 업데이트 (기존 블록 삭제 + 새 블록 생성)
        client = ctx.client
        cypher_lock = ctx.cypher_lock
        
        if client:
            # 기존 블록 삭제
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
            "old_rules": None,  # 기존 규칙 정보는 생략
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
    
    문제 유형:
    - 키워드 누락
    - kind 오분류
    - 의도 오해
    """
    
    @staticmethod
    def detect(
        block: Dict[str, Any],
        expected_keywords: List[str],
    ) -> bool:
        """B 오류 감지
        
        Args:
            block: 블록 정보
            expected_keywords: 기대되는 키워드 리스트
            
        Returns:
            오류 감지 여부
        """
        current_keywords = block.get("keywords", [])
        
        # 기대 키워드가 현재 키워드에 없음
        missing = [k for k in expected_keywords if k.lower() not in [
            ck.lower() for ck in current_keywords
        ]]
        
        if missing:
            log_nontarget("B-DETECT", f"키워드 누락: {missing}")
            return True
        
        return False
    
    @staticmethod
    async def correct(
        block_id: str,
        block_code: str,
        file_type: str,
        ctx: RuntimeCtx,
        feedback: str = "",
    ) -> Dict[str, Any]:
        """B 수정 수행: Summary 재생성
        
        Args:
            block_id: 블록 Neo4j elementId
            block_code: 블록 코드
            file_type: 파일 타입
            ctx: 런타임 컨텍스트 (client, cypher_lock)
            feedback: 수정 피드백
            
        Returns:
            수정 결과 {success, old_summary, new_summary}
        """
        log_correction("B", "RE-SUMMARIZE", block_id, feedback or "summary 재생성")
        
        # 1. 새 Summary 생성 (ctx 전달)
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
    
    문제 유형:
    - 흔한 키로 오연결
    - 스코프가 다른 변수로 연결
    - 테이블 오추론
    """
    
    @staticmethod
    def detect(
        links: List[Dict[str, Any]],
        confidence_threshold: float = 0.5,
    ) -> bool:
        """C 오류 감지
        
        Args:
            links: 관계 리스트
            confidence_threshold: confidence 임계값
            
        Returns:
            오류 감지 여부
        """
        # confidence가 낮은 링크가 있음
        low_confidence = [
            l for l in links
            if l.get("confidence", 1.0) < confidence_threshold
        ]
        
        if low_confidence:
            log_nontarget("C-DETECT", f"낮은 confidence 링크: {len(low_confidence)}개")
            return True
        
        return False
    
    @staticmethod
    async def correct(
        block_id: str,
        remove_link_ids: List[str],
        new_links: List[Dict[str, Any]],
        ctx: RuntimeCtx,
    ) -> Dict[str, Any]:
        """C 수정 수행: 링크 교체
        
        기존 링크를 삭제하고 새 링크를 생성합니다.
        
        Args:
            block_id: 블록 Neo4j elementId
            remove_link_ids: 삭제할 링크 ID 리스트
            new_links: 생성할 새 링크 정보 리스트
            ctx: 런타임 컨텍스트 (client, cypher_lock)
            
        Returns:
            수정 결과 {success, removed, created}
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
    ctx: RuntimeCtx,
    **kwargs,
) -> Dict[str, Any]:
    """통합 수정 실행
    
    Args:
        correction_type: 수정 유형 ("A", "B", "C")
        file_path: 대상 파일/블록 경로
        ctx: 런타임 컨텍스트 (client, cypher_lock)
        **kwargs: 수정 유형별 추가 파라미터
        
    Returns:
        수정 결과
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
    """수정 이력 조회
    
    Args:
        file_path: 필터링할 파일 경로
        correction_type: 필터링할 수정 유형 (A, B, C)
        limit: 최대 반환 개수
        
    Returns:
        수정 이력 리스트
    """
    if not os.path.exists(MODIFICATIONS_FILE):
        return []
    
    history: List[Dict[str, Any]] = []
    
    try:
        with open(MODIFICATIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                
                # 필터링
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
