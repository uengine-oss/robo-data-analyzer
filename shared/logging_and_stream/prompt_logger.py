"""LLM rule 호출별 prompt + 응답을 ``logs/llm_prompts/latest/<rule_name>/`` 에 적재.

각 rule 호출마다 카운터 증가 (``001.json``, ``002.json``, ...).
run 간 누적 X — ``logs/llm_prompts/latest/`` 자체가 run 시작 시 rmtree.

목적:
- 디버깅 (LLM 응답 비결정성 / 환각 추적)
- byte-identical CI 검증의 보조 데이터
- 비용 폭발 구간 사후 분석
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from shared.config.env_catalog import ENV


# rule_name → 카운터 (run 내 누적, run 종료 시 reset)
_counters: dict[str, int] = defaultdict(int)
_lock = threading.Lock()


def _prompts_dir() -> Path:
    return ENV.log.dir / "llm_prompts" / "latest"


def log_rule_call(
    rule_name: str,
    system_prompt: str,
    user_content: str,
    response: Any,
    metadata: dict | None = None,
) -> None:
    """rule 호출 1회 → ``<rule_name>/<NNN>.json`` 적재.

    Args:
        rule_name: ``prompts/<strategy>/<rule>.yaml`` 의 rule 이름.
        system_prompt: 시스템 메시지 내용 (byte-identical).
        user_content: user 메시지 내용 (volatile).
        response: LLM 응답 (Pydantic 또는 dict).
        metadata: model / tokens / latency 등 부가 정보.
    """
    with _lock:
        _counters[rule_name] += 1
        counter = _counters[rule_name]

    out_dir = _prompts_dir() / rule_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # response 직렬화 (Pydantic / dict / str 모두 대응)
    if hasattr(response, "model_dump"):
        response_data = response.model_dump()
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = {"raw": str(response)}

    record = {
        "rule_name": rule_name,
        "counter": counter,
        "system_prompt": system_prompt,
        "user_content": user_content,
        "response": response_data,
        "metadata": metadata or {},
    }
    out_path = out_dir / f"{counter:03d}.json"
    out_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_counters() -> None:
    """run 시작 시 카운터 초기화."""
    with _lock:
        _counters.clear()
