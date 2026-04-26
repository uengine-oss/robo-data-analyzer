"""LangChain Agent 공용 미들웨어 — frentis(gpt-oss) 환경 대응.

scanning / linking 양쪽 에이전트가 동일한 미들웨어 세트를 쓴다. 본 모듈의 책임:

  1. **Harmony 누수 → tool_call 재주입** (`recover_harmony_tool_calls`)
     gpt-oss 서버가 Harmony 포맷을 OpenAI tool_calls 로 변환 못 하고 content 로
     흘려보내는 케이스를 직접 파싱해 `AIMessage.tool_calls` 로 재주입.
     세션 실측: 부재 시 Profile Agent `tool_calls=1, sections=0` (실패) →
     부착 후 `tool_calls=3, sections=2`.

  2. **frentis 32k 컨텍스트 보호 임계치**
     tiktoken 과소추정 + 32k 컨텍스트 → LangChain 기본값보다 공격적인 트리거.

  3. **상태 핸드오프 요약 프롬프트** (`TASK_AWARE_SUMMARY_PROMPT`)
     기본 SummarizationMiddleware 는 "끝났음" 톤이라 작업 도중 발동되면 에이전트가
     멈춘다. 진행 중 상태(등록된 관계 / 후보 / 다음 키워드) 보존 지시문으로 교체.

모델 무관한 제어토큰 청소·도구호출 카운트는 `util.llm_guardrails` 가 단일 출처.
본 모듈은 LangChain agent 경로 전용 미들웨어이므로 분리.
"""

from __future__ import annotations

import json as _json
import logging
import re as _re
import uuid as _uuid
from typing import Any, List

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    wrap_model_call,
)
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Harmony tool_call 재주입 패턴
# ─────────────────────────────────────────────────────────────────────
# Harmony tool_call 풀 패턴 — name + args + <|call|> 까지 추출해 재주입에 사용.
# 일반 제어토큰 청소(`<|...|>`) 는 util.llm_guardrails.strip_control_tokens 가 담당.
_HARMONY_CALL_RE = _re.compile(
    r"<\|channel\|>\w+\s+to=functions\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+code)?\s*<\|message\|>\s*(?P<args>\{.*?\})\s*<\|call\|>",
    _re.DOTALL,
)


# ─────────────────────────────────────────────────────────────────────
# frentis 컨텍스트 보호 임계치
# ─────────────────────────────────────────────────────────────────────

TOOL_RESULT_CLEAR_TRIGGER_TOKENS = 10_000  # ClearToolUsesEdit
TOOL_RESULT_KEEP_LAST = 5
SUMMARIZE_TRIGGER_TOKENS = 15_000          # SummarizationMiddleware
SUMMARIZE_KEEP_LAST_MESSAGES = 6


# ─────────────────────────────────────────────────────────────────────
# 상태 핸드오프 요약 프롬프트
# ─────────────────────────────────────────────────────────────────────

TASK_AWARE_SUMMARY_PROMPT = (
    "⚠ IMPORTANT — the agent is NOT done. It is paused mid-analysis because context filled up. "
    "This summary REPLACES old messages so the agent can CONTINUE tracing patterns and "
    "registering relationships via tools. Work is NOT finished until every pattern, "
    "every candidate, every trail has been exhausted.\n\n"
    "Compact state dump (markdown, no prose):\n"
    "```\n"
    "## Registered so far\n"
    "- <edge_type> <table/key>  (source_function_id, access)\n"
    "## Candidates still pending (from last bundle_search, NOT yet registered)\n"
    "- <chunk_id>  — <brief reason it looks relevant>\n"
    "## Visited chunks (skip re-reading)\n"
    "- <chunk_id>\n"
    "## Keywords to search next\n"
    "- <pattern>  (why)\n"
    "## Immediate next action\n"
    "- <one sentence: the single next tool call the agent must make>\n"
    "```\n"
    "Then end with: **⏭ CONTINUE. Do NOT stop until all candidates are processed and "
    "no new keywords emerge.**"
)


# ─────────────────────────────────────────────────────────────────────
# 표준 미들웨어 세트
# ─────────────────────────────────────────────────────────────────────


def build_common_middleware(model: Any) -> List[Any]:
    """scanning / linking 공용 미들웨어 세트.

    구성:
      1. recover_harmony_tool_calls — Harmony 누수 → tool_call 재주입 (frentis)
      2. TodoListMiddleware         — 진행 추적
      3. ContextEditingMiddleware   — 오래된 도구 결과 정리
      4. SummarizationMiddleware    — 컨텍스트 압축 + 상태 핸드오프 프롬프트
      5. PatchToolCallsMiddleware   — deepagents 도구 호출 패치
    """

    @wrap_model_call
    def recover_harmony_tool_calls(request, handler):
        # frentis(gpt-oss) 호환 레이어 — Harmony 누수 → tool_call 재주입.
        # 본 미들웨어는 LLM call 후처리이므로 예외를 raise 하면 LLM 호출 체인 자체가
        # 깨진다. 정책의 "정당화 가능한 silent_fail" 케이스로 errlog + 원본 response
        # 그대로 반환. 단, 모든 실패 케이스를 errlog 로 의무 기록한다.
        response = handler(request)
        try:  # noqa: SILENT_FAIL — frentis 호환 후처리. raise 하면 LLM 체인 전체가 깨짐
            msg = getattr(response, "result", None) or response
            target = msg[0] if isinstance(msg, list) and msg else msg
            if not isinstance(target, AIMessage):
                return response
            if target.tool_calls:
                return response
            content = str(target.content or "")
            if not content or "<|" not in content:
                return response
            match = _HARMONY_CALL_RE.search(content)
            if not match:
                return response
            try:
                args = _json.loads(match.group("args"))
            except Exception as json_exc:  # noqa: SILENT_FAIL — Harmony args 파싱 실패 시 recovery 포기, 원본 response 반환 (frentis 호환 옵션)
                logger.error(
                    "[HARMONY-RECOVER:ERROR] Harmony args JSON 파싱 실패 — recovery 포기 | name=%s | %s: %s | raw=%r",
                    match.group("name"), type(json_exc).__name__, json_exc, match.group("args")[:200],
                )
                return response
            target.tool_calls = [{
                "name": match.group("name"),
                "args": args,
                "id": f"recovered_{_uuid.uuid4().hex[:8]}",
                "type": "tool_call",
            }]
            target.content = ""
            logger.warning("[HARMONY-RECOVER] content → tool_call | name=%s", match.group("name"))
        except Exception as exc:  # noqa: SILENT_FAIL — frentis 호환 후처리, raise 하면 LLM 체인 전체가 깨짐
            logger.error(
                "[HARMONY-RECOVER:ERROR] 미들웨어 처리 중 예외 — 원본 response 반환 | %s: %s",
                type(exc).__name__, exc, exc_info=True,
            )
        return response

    return [
        recover_harmony_tool_calls,
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(
                trigger=TOOL_RESULT_CLEAR_TRIGGER_TOKENS,
                keep=TOOL_RESULT_KEEP_LAST,
            ),
        ]),
        SummarizationMiddleware(
            model=model,
            trigger=("tokens", SUMMARIZE_TRIGGER_TOKENS),
            keep=("messages", SUMMARIZE_KEEP_LAST_MESSAGES),
            summary_prompt=TASK_AWARE_SUMMARY_PROMPT,
        ),
        PatchToolCallsMiddleware(),
    ]
