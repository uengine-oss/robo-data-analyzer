"""3 분할 fan-out 헬퍼 — ``analyze_function_step`` 이 함수당 1 회 호출.

함수 1개당 LLM 호출 3 번 병렬:
1. ``function_summary``         → summary + stereotype + actor + logical_name
2. ``function_table_insights``  → tableInsights + questions
3. ``function_rules_examples``  → rules + examples (nested)

**같은 입력 prefix** (시스템 프롬프트 / 함수 본문 / callee 카드 / reads/writes) →
RadixAttention 95%+ 적중. 마지막 instruction 만 다름.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import Function
from data_models.stereotype_validator import coerce_stereotype
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.llm_output_models import (
    RulesExamplesOutput,
    SummaryOutput,
    TableInsightsOutput,
)
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.logger import log_error


async def analyze_function_three_split(
    function_id: str,
    contexts: dict[str, AnalysisContext],
    function_by_id: dict[str, Function],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
) -> Optional[Function]:
    """함수 1개의 3 분할 분석 — 결과를 ``Function`` 에 적용해 반환.

    Args:
        codebase_notes: ``ProfileResult.codebase_notes`` — Plan §3.3 prefix 공유로
            RadixAttention 캐시 적중. 모든 함수 분석이 같은 prefix 사용.

    Returns:
        summary/stereotype/actor/logical_name 채워진 Function. 실패 시 None.
    """
    ctx = contexts.get(function_id)
    fn = function_by_id.get(function_id)
    if ctx is None or fn is None:
        return None

    user_values = _build_user_values(ctx, fn, contexts, strategy, codebase_notes)
    prompts_dir = Path(strategy.prompts_dir)

    # 3 호출 병렬 — 같은 입력 prefix
    tasks = [
        run_yaml_rule(
            prompts_dir=prompts_dir,
            rule_name="function_summary",
            user_values=user_values,
            response_model=SummaryOutput,
            budget=cost_budget,
        ),
        run_yaml_rule(
            prompts_dir=prompts_dir,
            rule_name="function_table_insights",
            user_values=user_values,
            response_model=TableInsightsOutput,
            budget=cost_budget,
        ),
        run_yaml_rule(
            prompts_dir=prompts_dir,
            rule_name="function_rules_examples",
            user_values=user_values,
            response_model=RulesExamplesOutput,
            budget=cost_budget,
        ),
    ]
    try:
        summary_out, insights_out, rules_out = await asyncio.gather(*tasks)
    except (BudgetExceededError, ModelCallLimitExceeded):
        # 회로 차단기 — 상위로 전파해서 파이프라인 중단
        raise
    except Exception as e:
        # 개별 함수 분석 실패 → 명시 로깅 후 이 함수만 건너뜀 (다른 함수 계속).
        # 절대원칙 #3 — silent return None 금지. 사유 기록.
        log_error(
            "analysis_flow.step6_analyze_function.three_split_llm_fanout",
            "analyze_function_three_split",
            e,
            function_id=function_id,
        )
        return None

    # fan-in 머지 → Function 에 적용
    # stereotype 은 ontology_roles.yaml 풀과 검증 (없으면 빈 문자열로 강제)
    validated_stereotype = coerce_stereotype(
        summary_out.stereotype or "",
        strategy.name,
    )
    fn.summary = summary_out.summary
    fn.stereotype = validated_stereotype or None
    fn.actor = summary_out.actor or None
    fn.logical_name = summary_out.logical_name or None
    fn.extra["table_insights"] = [t.model_dump() for t in insights_out.table_insights]
    fn.extra["questions"] = list(insights_out.questions)
    fn.extra["rules"] = [r.model_dump() for r in rules_out.rules]

    # ctx 에도 summary 부착 (Bottom-up 다음 Round 의 caller 가 사용)
    ctx.summary = summary_out.summary
    return fn


def _build_user_values(
    ctx: AnalysisContext,
    fn: Function,
    contexts: dict[str, AnalysisContext],
    strategy: Any,
    codebase_notes: str,
) -> dict:
    """3 yaml 공통 ``user_template`` 의 치환 값.

    Args:
        codebase_notes: ``ProfileResult`` 의 자연어 단서 (모든 함수 분석 공통 prefix).
    """
    body_text = _read_body(fn)
    callee_summaries_text = _format_callee_summaries(ctx, contexts)

    # DBMS 의 parent / sibling — extra 에서 추출
    parent_summary = ctx.extra.get("parent_summary") or "(없음)"
    sibling_ids = ctx.extra.get("sibling_ids", ())
    sibling_summaries_text = _format_sibling_summaries(sibling_ids, contexts)

    return {
        "function_id": fn.function_id,
        "signature": fn.signature,
        "function_body": body_text,
        "codebase_notes": codebase_notes or "(없음)",
        "callee_summaries": callee_summaries_text,
        "reads": ", ".join(ctx.reads) or "(없음)",
        "writes": ", ".join(ctx.writes) or "(없음)",
        # DBMS 만 사용
        "parent_summary": parent_summary,
        "sibling_summaries": sibling_summaries_text,
    }


def _read_body(fn: Function) -> str:
    """함수 본문 라인 범위 추출."""
    try:
        raw = Path(fn.file_path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = raw.splitlines()
    return "\n".join(lines[max(fn.start_line - 1, 0) : fn.end_line])


def _format_callee_summaries(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> str:
    """``callee_id`` → callee.summary 또는 one_liner 텍스트 포맷.

    Bottom-up DAG 진행 중이면 callee summary 가 ``contexts`` 에 있음.
    아직 없으면 (cycle 내부 등) one_liner 또는 signature 만.
    """
    if not ctx.callees:
        return "(없음)"
    lines = []
    for callee_id in ctx.callees:
        c = contexts.get(callee_id)
        if c is None:
            lines.append(f"- {callee_id}: (외부 또는 미해결)")
            continue
        text = c.summary or c.one_liner or c.signature
        lines.append(f"- {callee_id}: {text}")
    return "\n".join(lines)


def _format_sibling_summaries(
    sibling_ids: tuple[str, ...],
    contexts: dict[str, AnalysisContext],
) -> str:
    """DBMS 의 sibling summary 포맷."""
    if not sibling_ids:
        return "(없음)"
    lines = []
    for sid in sibling_ids:
        c = contexts.get(sid)
        if c is None:
            continue
        text = c.summary or c.one_liner or "(분석 전)"
        lines.append(f"- {sid}: {text}")
    return "\n".join(lines) if lines else "(없음)"
