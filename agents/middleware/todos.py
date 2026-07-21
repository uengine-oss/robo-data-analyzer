"""TodoTrackerMiddleware — Agent todos 변화를 NDJSON 스트림 + 진단 JSONL로 기록.

``todo_update``는 CLI와 runtime event mirror가 소비한다. 현재 Analyzer 프론트는 이 타입을
명시적으로 렌더하지 않으므로 프론트 표시 기능이라고 주장하지 않는다.

**부수**: run 디렉터리의 ``todos.jsonl`` 적재 (사후 분석).

LangChain ``TodoListMiddleware`` 가 자동 제공하는 ``state.todos`` 변화를 감지.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from langchain.agents.middleware import AgentMiddleware

from agents.middleware.diagnostics import (
    append_optional_diagnostic_event,
)


class TodoTrackerMiddleware(AgentMiddleware):
    """state.todos 변화 감지 → 스트림 push + jsonl 적재."""

    def __init__(
        self,
        agent_name: str,
        log_path: Path,
        stream_emit: Optional[Callable[[dict], None]] = None,
    ):
        super().__init__()
        self.agent_name = agent_name
        self.log_path = Path(log_path)
        self.stream_emit = stream_emit
        self._last_todos: list = []

    def after_model(self, state: Any, runtime: Any) -> Any:
        if not isinstance(state, dict):
            return state
        todos = state.get("todos", [])
        if todos == self._last_todos:
            return state
        self._last_todos = list(todos)

        event = {
            "agent": self.agent_name,
            "todos": todos,
        }

        # 프론트 NDJSON 스트림 push (주된 가치)
        if self.stream_emit is not None:
            self.stream_emit({"type": "todo_update", **event})

        append_optional_diagnostic_event(
            self.log_path,
            event,
            component="agents.middleware.todos",
        )
        return state
