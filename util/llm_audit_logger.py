"""LLM 호출 감사 로깅 (sync).

LLM 호출 기록 + 토큰 사용량 누적. prompt 별 JSON 로그 누적 저장.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_community.callbacks import get_openai_callback

from config.settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "log_llm_interaction",
    "invoke_with_audit",
]

_PROMPT_LOG_DIR = settings.path.prompt_log_dir
_FILE_LOCK = threading.Lock()
_CLEARED_PROMPTS: set[str] = set()

# 실행 ID — 각 프로세스 시작 시 고유값. 캐시 검증 시 1차/2차 실행 구분용.
_RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")


def _sanitize_prompt_name(prompt_name: str) -> str:
    """파일명 안전 보장 — 모든 문자가 비-식별자라 sanitize 후 빈 문자열이면 'prompt' 사용."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt_name)
    safe = safe.strip("_")
    return safe or "prompt"  # noqa: FALLBACK_DEFAULT — 파일명 안전 보장 utility


def _prompt_log_path(prompt_name: str) -> str:
    os.makedirs(_PROMPT_LOG_DIR, exist_ok=True)
    safe_name = _sanitize_prompt_name(prompt_name)
    return os.path.join(_PROMPT_LOG_DIR, f"{safe_name}.json")


def _load_prompt_log(prompt_name: str) -> list[dict[str, Any]]:
    path = _prompt_log_path(prompt_name)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            records = json.load(fp)
            return records if isinstance(records, list) else []
    except FileNotFoundError:
        # 첫 호출 시 정상 — 빈 로그로 시작
        return []
    except json.JSONDecodeError as e:
        # 손상된 JSON — errlog 후 빈 로그로 재시작 (감사 로그 손실은 fatal 아님)
        logger.error(  # noqa: SILENT_FAIL — 감사 로그 손상은 분석 자체를 막지 않도록 빈 로그로 재시작
            "[AUDIT_LOG] 손상된 JSON 발견 — 빈 로그로 재시작 | path=%s | %s: %s",
            path, type(e).__name__, e,
        )
        return []


def _write_prompt_log(prompt_name: str, records: list[dict[str, Any]]) -> None:
    path = _prompt_log_path(prompt_name)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(records, fp, ensure_ascii=False, indent=2)


def _safe_serialize(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {k: _safe_serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_safe_serialize(v) for v in value]
        return str(value)


def _extract_token_usage(callback) -> Optional[dict[str, int]]:
    prompt_tokens = getattr(callback, "prompt_tokens", 0)
    completion_tokens = getattr(callback, "completion_tokens", 0)
    total_tokens = getattr(callback, "total_tokens", 0)
    # OpenAI prompt cache 적중 토큰 — langchain 버전마다 attribute 명 다름.
    # 두 후보 모두 시도, 없으면 0 (gpt-5 계열 prompt cache 자동 측정용).
    cached_tokens = (
        getattr(callback, "prompt_tokens_cached", 0)
        or getattr(callback, "cached_tokens", 0)
    )
    if any(value for value in (prompt_tokens, completion_tokens, total_tokens)):
        return {
            "prompt": int(prompt_tokens or 0),
            "completion": int(completion_tokens or 0),
            "total": int(total_tokens or 0),
            "cached": int(cached_tokens or 0),
        }
    return None


def _entry_sort_key(entry: dict[str, Any]) -> tuple[Any, str]:
    sort_candidate = entry.get("sortKey")
    if sort_candidate is None and isinstance(entry.get("metadata"), dict):
        sort_candidate = entry["metadata"].get("startLine")
    timestamp = entry.get("timestamp", "")
    return (sort_candidate if sort_candidate is not None else float("inf"), timestamp)


def log_llm_interaction(
    prompt_name: str,
    input_payload: Any,
    output_payload: Any,
    token_usage: Optional[dict[str, int]] = None,
    metadata: Optional[dict[str, Any]] = None,
    sort_key: Optional[Any] = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt_name,
        "run_id": _RUN_ID,
        "input": _safe_serialize(input_payload),
        "output": _safe_serialize(output_payload),
    }
    if token_usage:
        entry["tokenUsage"] = token_usage
    if metadata:
        entry["metadata"] = _safe_serialize(metadata)
    if sort_key is not None:
        entry["sortKey"] = sort_key

    with _FILE_LOCK:
        # run_id 기반 누적 (이전 실행 로그 보존 — 캐시 히트 검증용)
        # 같은 run_id 내에서는 첫 호출 시 해당 run_id 엔트리만 초기화
        records = _load_prompt_log(prompt_name)
        if prompt_name not in _CLEARED_PROMPTS:
            # 이번 실행(run_id)의 기존 엔트리만 제거 (이전 실행 보존)
            records = [r for r in records if r.get("run_id") != _RUN_ID]
            _CLEARED_PROMPTS.add(prompt_name)
        records.append(entry)
        records.sort(key=_entry_sort_key)
        _write_prompt_log(prompt_name, records)


def invoke_with_audit(
    chain,
    payload: dict[str, Any],
    prompt_name: str,
    *,
    input_payload: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
    sort_key: Optional[Any] = None,
    config: Optional[Any] = None
):
    with get_openai_callback() as callback:
        result = chain.invoke(payload, config=config)
    token_usage = _extract_token_usage(callback)
    log_llm_interaction(
        prompt_name,
        input_payload if input_payload is not None else payload,
        result,
        token_usage=token_usage,
        metadata=metadata,
        sort_key=sort_key,
    )
    return result


