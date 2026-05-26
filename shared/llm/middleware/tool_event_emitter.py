"""ToolEventEmitterMiddleware — 도구 호출마다 친화적 메시지 NDJSON push.

**주된 가치**: 프론트 NDJSON 스트림에 ``tool_event`` push.
사용자가 Agent 의 도구 호출을 실시간 봄 (예: "DDL 테이블 USERS 조회 중...").

**부수**: ``logs/tool_events.jsonl`` 적재 (사후 분석).

도구 이름 → 친화 메시지 template 는 ``shared/config/tool_messages.py`` 가 정의.

★ LangChain 1.x AgentMiddleware 의 도구 hook = ``wrap_tool_call(request, handler)``.
   ``before_tool`` / ``after_tool`` 는 존재 X — 안 부르면 jsonl 0 줄.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command


class ToolEventEmitterMiddleware(AgentMiddleware):
    """도구 호출 wrap → 친화 메시지 emit (시작/끝 두 번)."""

    def __init__(
        self,
        agent_name: str,
        log_path: Path,
        stream_emit: Optional[Callable[[dict], None]] = None,
        tool_messages: Optional[dict[str, str]] = None,
    ):
        super().__init__()
        self.agent_name = agent_name
        self.log_path = Path(log_path)
        self.stream_emit = stream_emit
        # 예: {"grep": "{pattern} 정규식 검색 중..."}
        self.tool_messages = tool_messages or {}

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """LangChain 1.x 표준 hook — 도구 호출 wrap.

        흐름: tool_start emit (인자 풍부) → handler 실행 → tool_end emit (결과 통계 + raw preview).
        예외 발생 시 tool_error emit 후 re-raise (silent fail 금지).
        """
        tool_call = request.tool_call
        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", "?")
        args = tool_call.get("args") if isinstance(tool_call, dict) else getattr(tool_call, "args", {})

        started = time.monotonic()
        self._emit("tool_start", tool_name, args, elapsed_ms=None, error=None,
                   result_stats=None, result_content=None)

        try:
            result = handler(request)
        except Exception as e:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._emit("tool_error", tool_name, args, elapsed_ms=elapsed_ms,
                       error=f"{type(e).__name__}: {e}",
                       result_stats=None, result_content=None)
            raise

        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._emit("tool_end", tool_name, args, elapsed_ms=elapsed_ms, error=None,
                   result_stats=_summarize_result(tool_name, result),
                   result_content=_extract_content(result))
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage | Command[Any]:
        """비동기 버전 — Agent 가 async invoke 일 때 호출."""
        tool_call = request.tool_call
        tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", "?")
        args = tool_call.get("args") if isinstance(tool_call, dict) else getattr(tool_call, "args", {})

        started = time.monotonic()
        self._emit("tool_start", tool_name, args, elapsed_ms=None, error=None,
                   result_stats=None, result_content=None)

        try:
            result = await handler(request)
        except Exception as e:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._emit("tool_error", tool_name, args, elapsed_ms=elapsed_ms,
                       error=f"{type(e).__name__}: {e}",
                       result_stats=None, result_content=None)
            raise

        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._emit("tool_end", tool_name, args, elapsed_ms=elapsed_ms, error=None,
                   result_stats=_summarize_result(tool_name, result),
                   result_content=_extract_content(result))
        return result

    def _emit(
        self,
        event_type: str,
        tool_name: str,
        args: Any,
        elapsed_ms: Optional[int],
        error: Optional[str],
        result_stats: Optional[dict],
        result_content: Optional[str],
    ) -> None:
        """tool_start / tool_end / tool_error emit.

        - **tool_start**: friendly message (인자 풍부) — pattern/base/path 등 검색 의도 보임.
        - **tool_end**: 결과 통계 (찾은 파일 수 / 매치 수 / 줄 수 등) + raw content.
        - **tool_error**: error 메시지 + elapsed.

        ★ stream / analyzer.log 에는 통계만 (한 줄 가독성). raw content 는 jsonl 에만.
        """
        # 시작 메시지: TOOL_MESSAGES template 치환 (인자 풍부)
        template = self.tool_messages.get(tool_name, f"{tool_name} 실행")
        try:
            friendly = template.format(**(args or {}))
        except (KeyError, IndexError, ValueError, AttributeError):
            friendly = template

        # stream/log 용 event — raw content 제외 (한 줄 가독성)
        stream_event = {
            "agent": self.agent_name,
            "event": event_type,
            "tool": tool_name,
            "message": friendly,
            "args": args,
            "elapsed_ms": elapsed_ms,
            "error": error,
        }
        if result_stats:
            stream_event.update(result_stats)

        # 프론트 NDJSON 스트림 push (주된 가치) — logger mirror 도 자동.
        if self.stream_emit is not None:
            self.stream_emit({"type": "tool_event", **stream_event})

        # logs/tool_events.jsonl 적재 — raw content 도 함께 (디버깅 사후 분석용).
        # Agent 가 잘못 결정한 시점에 "도구가 무엇을 반환했는지" 그대로 확인 가능.
        # 큰 본문 (read_file 100 줄 ≈ 3KB) 도 그대로 적재 — latest/ 가 매 run rmtree 되므로
        # 영구 누적 X. preview 자르기 X (정확한 사후 분석 우선).
        jsonl_event = dict(stream_event)
        if result_content is not None:
            jsonl_event["result_content"] = result_content
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_event, ensure_ascii=False, default=str) + "\n")


# ── 도구별 결과 통계 추출 — Agent 가 "뭐를 찾았는지" 한 줄로 ──

def _summarize_result(tool_name: str, result: Any) -> dict:
    """도구 호출 결과를 한 줄 통계 dict 로.

    Agent 가 검색 패턴 (start 라인) 으로 무엇을 했고 → 결과 (end 라인) 가 어땠는지
    한 줄에 핵심만. 본문은 ``tool_events.jsonl`` 의 다른 필드에 그대로 보존.

    도구별 통계:
    - ``ls``           → 항목수 (DIR / FILE 분리)
    - ``glob_search``  → 파일수 (또는 NO_MATCHES)
    - ``grep``         → 매치수 (또는 NO_MATCHES)
    - ``read_file``    → 읽은줄
    - ``read_file_range`` → 읽은줄
    - ``list_functions`` → 함수수
    - ``list_skills``  → SKILL수
    - ``read_skill``   → 줄수
    - ``write_todos``  → 갱신 (status counts)
    그 외: ``줄수`` 만.
    """
    content = _extract_content(result)
    if content is None:
        return {"결과": "(빈응답)"}

    # 명시적 빈/에러 응답 — 첫줄에 단서 박혀있음
    head = content.lstrip().split("\n", 1)[0] if content else ""
    if head.startswith(("NOT_FOUND", "NOT_A_DIR", "NOT_A_FILE", "NO_MATCHES",
                        "INVALID_GLOB", "INVALID_REGEX", "READ_ERROR")):
        return {"결과": head[:80]}

    lines = content.count("\n") + 1 if content else 0

    if tool_name == "ls":
        dirs = sum(1 for ln in content.splitlines() if ln.startswith("DIR"))
        files = sum(1 for ln in content.splitlines() if ln.startswith("FILE"))
        return {"항목": dirs + files, "DIR": dirs, "FILE": files}
    if tool_name == "glob_search":
        return {"파일": lines}
    if tool_name == "grep":
        return {"매치": lines}
    if tool_name in ("read_file", "read_file_range"):
        return {"읽은줄": lines}
    if tool_name == "list_functions":
        return {"함수": lines}
    if tool_name == "list_skills":
        return {"SKILL": lines}
    if tool_name == "read_skill":
        return {"줄수": lines}
    if tool_name == "write_todos":
        # 결과는 보통 "Todos updated" 또는 todo 본문. status counts 는 args 에서 이미 emit.
        return {"결과": "갱신완료"}
    # 그 밖의 도구 (verify_table_regex 등) — 결과 줄 수만.
    return {"줄수": lines}


def _extract_content(result: Any) -> Optional[str]:
    """ToolMessage / Command / 기타 → string content 추출 (실패 시 None)."""
    if result is None:
        return None
    # ToolMessage: .content 가 str
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # multi-part content (이미지 등 포함 가능) — 텍스트 부분만 join
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts) if parts else None
    if isinstance(result, str):
        return result
    return None
