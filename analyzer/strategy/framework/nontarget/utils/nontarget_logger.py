"""비타겟 파일 분석 전용 로거 (Phase 3)

기존 util/logger.py를 활용하며, LangGraph 워크플로우 상태와
DeepAgents 액션을 구조화된 형식으로 로깅합니다.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from util.text_utils import log_process


def log_nontarget(phase: str, message: str, level: int = logging.INFO, **extra: Any) -> None:
    """nontarget 전용 로그
    
    Args:
        phase: 단계명 (WORKFLOW, AGENT, CHUNK, SUMMARY, LINK, CORRECTION)
        message: 로그 메시지
        level: 로그 레벨
        **extra: 추가 메타데이터
    """
    log_process("NONTARGET", phase, message, level, **extra)


def log_workflow_state(node_name: str, state: Dict[str, Any]) -> None:
    """LangGraph 워크플로우 상태 로그
    
    Args:
        node_name: 현재 워크플로우 노드 이름
        state: 상태 딕셔너리
    """
    file_path = state.get("file_path", "unknown")
    phase = state.get("phase", "unknown")
    blocks_count = len(state.get("blocks", []))
    
    log_nontarget(
        "WORKFLOW", 
        f"Node: {node_name} | file={file_path} | phase={phase} | blocks={blocks_count}"
    )


def log_agent_action(agent: str, action: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """DeepAgents 액션 로그
    
    Args:
        agent: 에이전트 이름 (Chunker, Summarizer, LinkReviewer)
        action: 수행 액션
        detail: 상세 정보
    """
    detail_str = json.dumps(detail, ensure_ascii=False)[:100] if detail else ""
    log_nontarget("AGENT", f"[{agent}] {action} | {detail_str}")


def log_correction(
    correction_type: str,
    action: str,
    file_path: str,
    reason: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """A/B/C 수정 작업 로그 (JSONL 파일에도 기록)
    
    Args:
        correction_type: 수정 유형 (A=블록, B=요약, C=연결)
        action: 수행 액션 (DELETE, CREATE, UPDATE)
        file_path: 대상 파일 경로
        reason: 수정 사유
        detail: 상세 정보
    """
    log_nontarget(
        "CORRECTION",
        f"[{correction_type}] {action} | file={file_path} | reason={reason}"
    )
    
    # JSONL 파일에 이력 기록
    record = {
        "ts": datetime.now().isoformat(),
        "type": correction_type,
        "action": action,
        "file": file_path,
        "reason": reason,
    }
    if detail:
        record["detail"] = detail
    
    try:
        with open("logs/nontarget_modifications.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 파일 쓰기 실패는 무시

