"""TodoTrackerMiddleware — Agent todos 변화를 프론트 NDJSON 스트림 + jsonl 로깅.

**주된 가치**: 프론트 NDJSON 스트림에 ``todo_update`` 이벤트 push.
사용자가 Agent 의 todo 진행을 실시간으로 봄 (예: "1. 가설 수립 → 2. SKILL 매칭 시도 ...").

**부수**: ``logs/todos.jsonl`` 적재 (사후 분석).

LangChain ``TodoListMiddleware`` 가 자동 제공하는 ``state.todos`` 변화를 감지.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from langchain.agents.middleware import AgentMiddleware


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

        # logs/todos.jsonl 적재 (부수)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return state
