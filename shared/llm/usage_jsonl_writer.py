"""usage.jsonl 적재 헬퍼 — Agent 미들웨어 / 단순 LLM 호출 공유.

Plan §4 M0 명세: "모든 LLM 호출 (Scan / Linking Agent 내부 포함) 토큰/비용 측정,
``usage.jsonl`` 적재".

설계:
- ``CostTrackingMiddleware`` (Agent) 와 ``run_yaml_rule`` (단순 LLM) 양쪽이 같은 형식으로
  적재 → ``_summarize_usage`` 가 동일하게 집계.
- jsonl 형식 = 1 LLM 호출 = 1 줄.
- 적재 실패는 호출 자체 성공으로 승격 X — 그러나 silent skip 금지 (log_warning).
"""
from __future__ import annotations

import json
from pathlib import Path

from shared.llm.token_usage import TokenUsageRecord
from shared.logging_and_stream.logger import log_warning


def append_usage_record(
    usage_log_path: Path,
    agent_name: str,
    record: TokenUsageRecord,
    cost_usd: float,
) -> None:
    """``usage.jsonl`` 에 1 LLM 호출 record append.

    Args:
        usage_log_path: 적재 경로 (예: ``logs/analysis.run/latest/usage.jsonl``).
        agent_name: "scan" / "linking_subagent" / "rule:<rule_name>" 등.
        record: ``track_usage()`` 결과.
        cost_usd: ``compute_cost(record)`` 결과.
    """
    line = json.dumps({
        "agent": agent_name,
        "model": record.model,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cached_tokens": record.cached_tokens,
        "total_tokens": record.total_tokens,
        "latency_ms": record.latency_ms,
        "cost_usd": cost_usd,
    }, ensure_ascii=False)

    try:
        usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        with usage_log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        # 측정 사각지대 만들지 말 것 — 적재 실패 사유 명시.
        log_warning(
            "shared.llm.usage_jsonl_writer",
            "append_usage_record",
            "usage.jsonl 적재 실패 — 비용 집계에 누락됨",
            agent=agent_name,
            path=str(usage_log_path),
            error=f"{type(e).__name__}: {e}",
        )
