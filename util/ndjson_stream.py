"""NDJSON 스트리밍 유틸리티

NDJSON 스트리밍 이벤트 생성 및 처리 함수들.
기존 stream_event.py를 리네이밍.

이벤트 타입:
- message: 사용자 메시지
- data: 그래프 데이터
- error: 오류
- node_event: 노드 생성/수정 이벤트
- relationship_event: 관계 생성/수정 이벤트
- phase_event: 단계 진행 이벤트
- complete: 완료

메시지 설계 원칙 (pdb.md):
- 이 메시지는 개발자 로그가 아니라, 사용자에게 보여지는 진행 설명이다
- 조건 분기 결과가 명확히 드러날 것
- 실패/스킵/대체 상황이 숨겨지지 않을 것
- "무슨 일이 일어났는지"를 자연어로 설명할 것
"""

import json
import uuid
import logging
from typing import Any, Optional, AsyncGenerator


def emit_bytes(payload: dict) -> bytes:
    """NDJSON 형식으로 바이트 생성"""
    return json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8") + b"\n"


def emit_message(content: str) -> bytes:
    """메시지 이벤트 전송"""
    return emit_bytes({"type": "message", "content": content})


def emit_error(
    content: str,
    error_type: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> bytes:
    """에러 이벤트 전송"""
    payload = {"type": "error", "content": content}
    if error_type:
        payload["errorType"] = error_type
    if trace_id:
        payload["traceId"] = trace_id
    return emit_bytes(payload)


def emit_data(**fields) -> bytes:
    """데이터 이벤트 전송"""
    payload = {"type": "data"}
    payload.update({k: v for k, v in fields.items() if v is not None})
    return emit_bytes(payload)


def emit_node_event(
    action: str,
    node_type: str,
    node_name: str,
    details: Optional[dict[str, Any]] = None,
) -> bytes:
    """노드 생성/수정 이벤트 전송"""
    payload = {
        "type": "node_event",
        "action": action,
        "nodeType": node_type,
        "nodeName": node_name,
    }
    if details:
        payload["details"] = details
    return emit_bytes(payload)


def emit_relationship_event(
    action: str,
    rel_type: str,
    source: str,
    target: str,
    details: Optional[dict[str, Any]] = None,
) -> bytes:
    """관계 생성/수정 이벤트 전송"""
    payload = {
        "type": "relationship_event",
        "action": action,
        "relType": rel_type,
        "source": source,
        "target": target,
    }
    if details:
        payload["details"] = details
    return emit_bytes(payload)


def emit_complete(summary: Optional[str] = None) -> bytes:
    """완료 이벤트 전송"""
    payload = {"type": "complete"}
    if summary:
        payload["summary"] = summary
    return emit_bytes(payload)


def emit_phase_event(
    phase_num: int,
    phase_name: str,
    status: str,
    progress: int = 0,
    details: Optional[dict[str, Any]] = None,
) -> bytes:
    """단계 진행 이벤트 전송"""
    payload = {
        "type": "phase_event",
        "phase": phase_num,
        "name": phase_name,
        "status": status,
        "progress": progress,
    }
    if details:
        payload["details"] = details
    return emit_bytes(payload)


def build_error_body(
    exc: Exception,
    trace_id: Optional[str] = None,
    message: Optional[str] = None,
) -> dict:
    """비스트리밍 500 응답용 표준 에러 바디 생성"""
    return {
        "errorType": exc.__class__.__name__,
        "message": message or str(exc),
        "traceId": trace_id or f"req-{uuid.uuid4()}",
    }


async def stream_with_error_boundary(
    async_gen: AsyncGenerator[bytes, None],
) -> AsyncGenerator[bytes, None]:
    """스트리밍 처리 경계"""
    trace_id = f"stream-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in async_gen:
            yield chunk
        yield emit_complete()
    except GeneratorExit:
        logging.info(f"[{trace_id}] 클라이언트 연결 종료")
    except Exception as e:
        error_msg = f"{e.__class__.__name__}: {str(e)}"
        logging.error(f"[{trace_id}] 스트림 에러: {error_msg}", exc_info=True)
        yield emit_error(error_msg, error_type=e.__class__.__name__, trace_id=trace_id)
