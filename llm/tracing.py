"""LLM 호출 prompt + 응답 적재 — 한 분석 단위 = 한 파일 (가독성 우선).

두 진입점:

1. ``write_rule_record(rule_name, ...)`` — yaml rule 단발 호출 (code_summary /
   module_summary 등). 같은 ``<rule_name>.json`` 한 파일에 ``records`` 배열로
   누적. ``sort_key`` (file:line / code_id) 로 자연 정렬 → 디버깅 시 소스 라인 순.

2. ``begin_agent_invoke`` / ``append_agent_turn`` / ``end_agent_invoke`` —
   Agent multi-turn (Scan / Linking). 한 invoke 전체가 한 ``.json`` 에. 시스템 지시 1번
   + turns 배열 (turn-by-turn user/response/tool_calls) + totals 집계.

``run`` 간 누적 X — 선택된 ``<run-output>/llm-prompts``를 사용한다. 미지정 시 기존
``logs/llm-prompts``이고, 별도 분리가 필요할 때만 CLI에서 다른 run-output을 명시한다.

옛 ``log_rule_call(rule_name, NNN.json)`` 분산 적재 정책에서 변경 이유:
- ``001.json`` ``002.json`` 분산 → 흐름 추적 어려움.
- ``system_prompt`` / ``user_content`` 본문 추출 버그 (빈 문자열 적재) 동시 fix.
- 옛 ``llm_audit_logger.py`` (data-analyzer 2108a14^) 의 "한 prompt 1 파일 records" 정책 차용 +
  Agent multi-turn 보완.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from shared.config.settings import SETTINGS
from llm.messages import content_to_text
from shared.events.runtime_logger import log_warning
from pipeline.execution.run_output_context import get_run_dir

_FILE_LOCK = threading.Lock()
_ATOMIC_REPLACE_ATTEMPTS = 6
_ATOMIC_REPLACE_BASE_DELAY_S = 0.01


def _prompts_dir() -> Path:
    # 분석 중에는 선택된 <run-output>/llm-prompts. 독립 프롬프트 도구는 기존 설정
    # 루트를 fallback으로 사용한다.
    return get_run_dir(fallback=SETTINGS.run_output.dir) / "llm-prompts"


# ── yaml rule 단발 호출 적재 ──────────────────────────────────


def _sanitize_filename(name: str) -> str:
    """파일명 안전 문자만 — 비-식별자는 ``_`` 로."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("_")
    return safe or "unnamed"


def write_rule_record(
    rule_name: str,
    target_id: str | None,
    system_prompt: str,
    user_content: str,
    response: Any,
    metadata: dict | None = None,
    sort_key: Any = None,
) -> None:
    """yaml rule 호출 1회 적재 — ``<rule_name>.json`` 의 ``records`` 배열에 append.

    파일 형식::

        {
          "rule_name": "code_summary",
          "records": [
            {"target_id": "OrderService.save", "sort_key": "OrderService.java:42",
             "system_prompt": "...", "user_content": "...", "response": {...},
             "metadata": {...}},
            ...
          ]
        }

    같은 run 안 같은 rule 가 N 번 호출되면 한 파일에 N 개 record 가 누적.
    ``sort_key`` 가 있으면 정렬 (예: ``file:line`` → 소스 라인 순 자연 정렬).

    Args:
        rule_name: yaml rule 이름 (예: "code_summary").
        target_id: 이 호출의 대상 식별자 (code_id / procedure_id / table_fqn / case_id).
            None 이면 ``unknown``. ctrl+F 검색에 쓰임.
        system_prompt: 시스템 메시지 전문.
        user_content: 치환 완료된 user 메시지 전문.
        response: LLM 응답 (Pydantic / dict / str).
        metadata: model / tokens / cached / latency_ms / cost_usd 등.
        sort_key: 정렬 키 (``file:line`` 형태 권장). None 이면 호출 순서.
    """
    out_path = _prompts_dir() / f"{_sanitize_filename(rule_name)}.json"
    record = _build_record(
        target_id=target_id or "unknown",
        sort_key=sort_key,
        system_prompt=system_prompt,
        user_content=user_content,
        response=response,
        metadata=metadata or {},
    )

    with _FILE_LOCK:
        existing = _load_existing(out_path)
        existing["rule_name"] = rule_name
        existing["records"].append(record)
        if any(r.get("sort_key") is not None for r in existing["records"]):
            existing["records"].sort(key=_sort_key_for_record)
        _write_json(out_path, existing)


def _load_existing(path: Path) -> dict:
    """기존 파일 load. 없으면 빈 구조. 파싱 실패 시 빈 구조 (운영자가 raw 파일 직접 검토 가능하도록 백업)."""
    if not path.exists():
        return {"rule_name": "", "records": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise json.JSONDecodeError("prompt record root must contain a records list", "", 0)
        return data
    except (OSError, json.JSONDecodeError):
        # 손상된 JSON — 백업 후 빈 구조 (run 내 누적이라 손실 작음)
        try:
            path.rename(path.with_suffix(f".broken.{int(time.time())}"))
        except OSError as backup_error:
            log_warning(
                "llm.tracing",
                "backup_broken_prompt_log",
                "손상된 프롬프트 로그 백업 실패 — 빈 구조로 계속",
                path=str(path),
                error=f"{type(backup_error).__name__}: {backup_error}",
            )
        return {"rule_name": "", "records": []}


def _build_record(
    target_id: str,
    sort_key: Any,
    system_prompt: str,
    user_content: str,
    response: Any,
    metadata: dict,
) -> dict:
    """record 1개 직렬화 (Pydantic / dict / str 자동 처리)."""
    if hasattr(response, "model_dump"):
        response_data: Any = response.model_dump()
    elif isinstance(response, dict):
        response_data = response
    elif isinstance(response, str):
        response_data = response
    else:
        response_data = {"raw": str(response)}

    return {
        "target_id": target_id,
        "sort_key": sort_key,
        "system_prompt": system_prompt,
        "user_content": user_content,
        "response": response_data,
        "metadata": metadata,
    }


def _sort_key_for_record(r: dict) -> tuple:
    """sort_key 정렬 — None 은 맨 뒤. 문자열은 자연 비교."""
    k = r.get("sort_key")
    if k is None:
        return (1, "")
    return (0, str(k))


def _write_json(path: Path, data: dict) -> None:
    """고유 임시 파일을 닫은 뒤 원자 교체한다.

    Windows에서는 바이러스 검사기나 로그 열람기가 대상 파일을 아주 짧게 열어 둔 동안
    ``os.replace``가 ``PermissionError``를 낼 수 있다. 같은 run의 프롬프트 증거를
    버리지 않도록 제한된 지수 백오프로 그 순간적인 공유 위반만 재시도한다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        json.dump(data, temporary_file, ensure_ascii=False, indent=2)
        temporary_path = Path(temporary_file.name)

    try:
        for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary_path, path)
                return
            except PermissionError:
                if attempt + 1 == _ATOMIC_REPLACE_ATTEMPTS:
                    raise
                time.sleep(_ATOMIC_REPLACE_BASE_DELAY_S * (2**attempt))
    finally:
        temporary_path.unlink(missing_ok=True)


# ── Agent multi-turn 적재 (contextvars 격리) ──────────────────


@dataclass
class _AgentInvokeState:
    agent_name: str
    invoke_id: str
    out_path: Path
    system_prompt: str
    turns: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        totals = _aggregate_totals(self.turns)
        totals["elapsed_s"] = round(time.monotonic() - self.started_at, 2)
        totals["turn_count"] = len(self.turns)
        return {
            "agent_name": self.agent_name,
            "invoke_id": self.invoke_id,
            "system_prompt": self.system_prompt,
            "turns": self.turns,
            "totals": totals,
        }


_invoke_var: contextvars.ContextVar[Optional[_AgentInvokeState]] = contextvars.ContextVar(
    "agent_invoke", default=None
)


def begin_agent_invoke(agent_name: str, invoke_id: str, system_prompt: str) -> Path:
    """Agent invoke 시작 — contextvars 에 invoke 상태 set + 초기 파일 생성.

    파일명:
    - Scan (invoke 1번): ``agent_scan.json``
    - Linking (case 별 invoke): ``agent_linking/<invoke_id>.json``

    Args:
        agent_name: ``scan`` / ``linking`` 등.
        invoke_id: invoke 식별자 (Scan=run / Linking=case_id 등).
        system_prompt: 시스템 지시 전문 (invoke 동안 1번만 적재).

    Returns:
        적재 파일 경로.
    """
    out_path = _resolve_agent_path(agent_name, invoke_id)
    state = _AgentInvokeState(
        agent_name=agent_name,
        invoke_id=invoke_id,
        out_path=out_path,
        system_prompt=system_prompt,
    )
    # 초기 파일 — 도중 죽어도 시스템 지시는 확인 가능.
    with _FILE_LOCK:
        _write_json(out_path, state.to_dict())
    _invoke_var.set(state)
    return out_path


def append_agent_turn(
    user_content: str,
    response: Any,
    metadata: dict | None = None,
) -> None:
    """매 LLM turn 적재 — turns 배열에 append + 파일 즉시 overwrite.

    중간 죽어도 마지막 turn 까지 적재됨. invoke 미시작 상태 호출은 silent skip
    (yaml rule 단발 호출도 이 미들웨어 통과하므로 — 거기엔 invoke 컨텍스트 없음).
    """
    state = _invoke_var.get()
    if state is None:
        return            # invoke 컨텍스트 X (단발 호출 — write_rule_record 가 별도 적재)

    turn = {
        "turn": len(state.turns) + 1,
        "user_content": user_content,
        "response": _serialize_response(response),
        "metadata": metadata or {},
    }
    state.turns.append(turn)

    with _FILE_LOCK:
        _write_json(state.out_path, state.to_dict())


def end_agent_invoke() -> None:
    """invoke 종료 — totals 최종 집계 후 파일 finalize + contextvars 클리어."""
    state = _invoke_var.get()
    if state is None:
        return
    try:
        with _FILE_LOCK:
            _write_json(state.out_path, state.to_dict())
    finally:
        _invoke_var.set(None)


def _resolve_agent_path(agent_name: str, invoke_id: str) -> Path:
    """Agent + invoke 별 파일 경로 결정.

    invoke_id 가 ``"run"`` / 빈 문자열이면 단일 파일 (Scan 1번 invoke).
    그 외엔 ``agent_<name>/<invoke_id>.json`` (Linking case 별).
    """
    safe_agent = _sanitize_filename(agent_name)
    safe_invoke = _sanitize_filename(invoke_id) if invoke_id else "run"
    if safe_invoke == "run":
        return _prompts_dir() / f"agent_{safe_agent}.json"
    return _prompts_dir() / f"agent_{safe_agent}" / f"{safe_invoke}.json"


def _serialize_response(response: Any) -> dict:
    """LangChain message 또는 dict → 직렬화."""
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    content = getattr(response, "content", None)
    tool_calls = getattr(response, "tool_calls", None)
    msg_type = getattr(response, "type", response.__class__.__name__)
    if content is not None or tool_calls is not None:
        return {
            "type": msg_type,
            "content": _content_to_str(content) if content is not None else "",
            "tool_calls": tool_calls or [],
        }
    return {"raw": str(response)}


def serialize_response(response: Any) -> dict:
    """실제 모델 메시지의 content/tool_calls를 감사 로그용 JSON으로 보존한다."""
    return _serialize_response(response)


def _content_to_str(content: Any) -> str:
    if isinstance(content, list):
        return content_to_text(content)
    return content if isinstance(content, str) else str(content)


def _aggregate_totals(turns: list[dict]) -> dict:
    """turns 의 metadata 누적 집계."""
    total_input = 0
    total_output = 0
    total_cached = 0
    total_cost = 0.0
    total_latency_ms = 0
    for t in turns:
        m = t.get("metadata") or {}
        total_input += int(m.get("input_tokens") or 0)
        total_output += int(m.get("output_tokens") or 0)
        total_cached += int(m.get("cached_tokens") or 0)
        total_cost += float(m.get("cost_usd") or 0.0)
        total_latency_ms += int(m.get("latency_ms") or 0)
    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cached_tokens": total_cached,
        "cost_usd": round(total_cost, 6),
        "latency_ms": total_latency_ms,
    }


def message_content_text(message: Any) -> str:
    """Normalize a LangChain message body for the prompt transcript."""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return content_to_text(content)
    return content if isinstance(content, str) else str(content)


def append_conversation_turn(
    *, request_messages: list, response_message: Any, last_message_count: int,
    model: str, latency_ms: int, cost_usd: float, usage: Any,
) -> int:
    """Append one new conversation turn and return the next message offset."""
    if not request_messages:
        return last_message_count
    start = last_message_count if last_message_count > 0 else 1
    new_messages = request_messages[start:]
    user_content = "\n\n".join(
        f"[{getattr(message, 'type', message.__class__.__name__)}]\n"
        f"{message_content_text(message)}"
        for message in new_messages
    )
    append_agent_turn(
        user_content=user_content,
        response={
            "type": getattr(response_message, "type", response_message.__class__.__name__),
            "content": message_content_text(response_message),
            "tool_calls": getattr(response_message, "tool_calls", None) or [],
        },
        metadata={
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
        },
    )
    return len(request_messages) + 1
