"""산출물 및 LLM 호출 감사 로깅

LLM 호출 기록, 디버깅 로그, 산출물 저장.
기존 llm_audit.py를 리네이밍 + save_artifact 추가.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from langchain_community.callbacks import get_openai_callback
except ImportError:  # pragma: no cover
    from langchain.callbacks import get_openai_callback

from config.settings import settings

__all__ = [
    "log_llm_interaction",
    "invoke_with_audit",
    "save_artifact",
    "save_artifacts_batch",
]

_AUDIT_DIR = settings.path.audit_dir
_PROMPT_LOG_DIR = settings.path.prompt_log_dir
_FILE_LOCK = threading.Lock()
_CLEARED_PROMPTS: set[str] = set()

# 실행 ID — 각 프로세스 시작 시 고유값. 캐시 검증 시 1차/2차 실행 구분용.
import datetime as _dt
_RUN_ID = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _sanitize_prompt_name(prompt_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt_name)
    safe = safe.strip("_")
    return safe or "prompt"


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
        return []
    except json.JSONDecodeError:
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
    if any(value for value in (prompt_tokens, completion_tokens, total_tokens)):
        return {
            "prompt": int(prompt_tokens or 0),
            "completion": int(completion_tokens or 0),
            "total": int(total_tokens or 0),
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


async def ainvoke_with_audit(
    chain,
    payload: dict[str, Any],
    prompt_name: str,
    *,
    input_payload: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
    sort_key: Optional[Any] = None,
    config: Optional[Any] = None
):
    """비동기 버전 invoke_with_audit. chain.ainvoke() 사용."""
    from langchain_community.callbacks import get_openai_callback
    with get_openai_callback() as callback:
        result = await chain.ainvoke(payload, config=config)
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


# ==================== 산출물 저장 (신규) ====================

def save_artifact(
    name: str,
    data: Any,
    *,
    run_dir: Path,
    subfolder: str = "artifacts",
) -> Path:
    """단일 산출물을 JSON으로 저장합니다.

    Args:
        name: 파일명 (확장자 포함, 예: "step1_catalog.json")
        data: 저장할 데이터 (JSON 직렬화 가능)
        run_dir: 실행 디렉토리
        subfolder: 하위 폴더 (기본: "artifacts")

    Returns:
        저장된 파일 경로
    """
    run_dir = Path(run_dir)
    out_dir = run_dir / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(
        json.dumps(_safe_serialize(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def save_artifacts_batch(
    items: dict[str, Any],
    *,
    run_dir: Path,
    subfolder: str = "artifacts",
) -> list[Path]:
    """여러 산출물을 한번에 저장합니다.

    Args:
        items: {파일명: 데이터} 딕셔너리
        run_dir: 실행 디렉토리
        subfolder: 하위 폴더

    Returns:
        저장된 파일 경로 리스트
    """
    paths = []
    for name, data in items.items():
        if data is not None:
            paths.append(save_artifact(name, data, run_dir=run_dir, subfolder=subfolder))
    return paths
