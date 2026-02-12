"""비타겟 파일 분석 전용 로거 (Phase 3)

기존 util/logger.py를 활용하며, DeepAgents 액션을
구조화된 형식으로 로깅합니다.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from util.text_utils import log_process


def log_nontarget(
    phase: str,
    message: str,
    level: int = logging.INFO,
    file_path: Optional[str] = None,
    **extra: Any,
) -> None:
    """nontarget 전용 로그 (파일별 구분 지원)

    Args:
        phase: 단계명 (WORKFLOW, AGENT, CHUNK, SUMMARY, LINK, CYPHER 등)
        message: 로그 메시지
        level: 로그 레벨
        file_path: 파일 경로 (있으면 파일명을 메시지에 포함)
        **extra: 추가 메타데이터
    """
    if file_path:
        file_name = os.path.basename(file_path)
        message = f"[{file_name}] {message}"
    log_process("NONTARGET", phase, message, level, **extra)


def log_agent_action(agent: str, action: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """DeepAgents 액션 로그

    Args:
        agent: 에이전트 이름 (Chunker)
        action: 수행 액션
        detail: 상세 정보
    """
    detail_str = json.dumps(detail, ensure_ascii=False)[:100] if detail else ""
    log_nontarget("AGENT", f"[{agent}] {action} | {detail_str}")
