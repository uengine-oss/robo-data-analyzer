"""Framework/DBMS가 공유하는 summary↔RULE 책임 경계 품질 게이트."""
from __future__ import annotations

import re
from pathlib import Path

from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.usage.cost_budget import BudgetExceededError
from pipeline.models.code_analysis_output import SummaryRewriteOutput
from llm.usage.cost_budget import CostBudget
from runtime_events.runtime_logger import log_info, log_warning


_PROMPTS = Path(__file__).resolve().parents[3] / "llm" / "prompts" / "quality"
_RULE_MARKER = re.compile(
    r"경우|이면|하면|실패|거부|초과|이하|이상|오류(?:가| 상황| 발생)|"
    r"(?<![가-힣])(?:시|때)(?![가-힣])"
)
_POLICY_NUMBER = re.compile(r"(?<![A-Za-z가-힣])(?:-?\d[\d,.]*|[A-Z]+\d+)(?![A-Za-z가-힣])")


def summary_boundary_reasons(summary: str) -> list[str]:
    """목적 요약에 정책 상세가 섞였다는 결정론 신호를 반환한다."""
    text = summary or ""
    markers = _RULE_MARKER.findall(text)
    reasons = []
    if markers:
        reasons.append("rule_marker")
    if _POLICY_NUMBER.search(text):
        reasons.append("policy_number_or_code")
    if len([line for line in text.splitlines() if line.strip()]) > 1:
        reasons.append("multiple_lines")
    return reasons


async def enforce_summary_boundary(
    *, summary: str, node_name: str, logical_name: str, search_text: str,
    budget: CostBudget | None, audit_id: str,
) -> str:
    """위반 draft만 의미 재작성하고 재검증한다. 실패 시 원문을 보존하고 경고한다."""
    reasons = summary_boundary_reasons(summary)
    if not reasons:
        return summary
    draft = summary
    for attempt in (1, 2):
        try:
            result = await run_yaml_rule(
                prompts_dir=_PROMPTS,
                rule_name="summary_rewrite",
                user_values={
                    "node_name": node_name,
                    "logical_name": logical_name,
                    "search_text": search_text,
                    "draft_summary": draft,
                },
                response_model=SummaryRewriteOutput,
                budget=budget,
            )
        except BudgetExceededError:
            raise
        except Exception as exc:  # 품질 보정 실패가 원래 분석 결과를 소실시키면 안 된다.
            log_warning(
                __name__, "summary_boundary_rewrite_failed",
                f"summary 책임 경계 재작성 실패 — 원래 요약 보존: {type(exc).__name__}: {exc}",
                code_id=audit_id, reasons=reasons, attempt=attempt,
            )
            return summary
        draft = result.summary.strip()
        remaining = summary_boundary_reasons(draft)
        if not remaining:
            log_info(
                __name__, "summary_boundary_rewritten",
                "규칙형 요약을 목적·효과 한 문장으로 재작성",
                code_id=audit_id, reasons=reasons, attempt=attempt,
            )
            return draft
        reasons = remaining
    if reasons == ["rule_marker"]:
        log_info(
            __name__, "summary_boundary_reviewed",
            "두 차례 의미 재작성 후 구체 정책 없이 조건 표지만 남은 목적 요약을 보존",
            code_id=audit_id, reasons=reasons,
        )
        return draft
    log_warning(
        __name__, "summary_boundary_unresolved",
        "두 차례 재작성 후에도 summary 책임 경계 위반 — 마지막 draft 보존",
        code_id=audit_id, reasons=reasons,
    )
    return draft
