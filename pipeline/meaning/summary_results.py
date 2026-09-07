"""Summary 응답의 근거 검증과 공개 필드 반영."""
from __future__ import annotations

from typing import Any

from graph.schema.graph_nodes import CodeNode
from llm.runners.model_output_error import ModelOutputError
from pipeline.meaning.summary.summary_quality import (
    copied_callee_summary_id,
    missing_structural_summary_identities,
    normalize_canonical_summary,
    unsupported_code_comparison_claims,
)
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.models.semantic_frame import mark_semantic_frame_insufficient
from pipeline.source_languages import (
    exclude_contradicted_stereotype,
    require_stereotype,
)
from shared.events.runtime_logger import log_warning

_STEP = "step7.analyze_code.meanings"


def apply_summary_result(
    *,
    fn: CodeNode,
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
    analysis_inputs: dict,
    strategy: Any,
    result: SummaryOutput | ModelOutputError,
) -> list[AnalysisFinding]:
    """Validate one Summary result and expose only supported meaning."""
    if isinstance(result, ModelOutputError):
        return [AnalysisFinding(
            concern="summary",
            status="unavailable",
            target_id=fn.id,
            reasons=(str(result),),
            evidence_rule=result.rule_name,
        )]

    _publish_semantic_frame(fn, ctx, result)
    if result.evidence_status == "insufficient":
        _reject_summary(fn, ctx, "needs_context")
        return [AnalysisFinding(
            concern="summary",
            status="unverified",
            target_id=fn.id,
            reasons=tuple(result.missing_context),
            candidate=result.model_dump(),
            evidence_rule="code_summary",
        )]

    result.summary = normalize_canonical_summary(result.summary)
    copied_from = copied_callee_summary_id(
        result.summary,
        (
            (callee_id, contexts[callee_id].summary)
            for callee_id in ctx.callees
            if callee_id in contexts
        ),
    )
    if copied_from is not None:
        _reject_summary(fn, ctx, "rejected")
        _degrade_summary_semantic_frame(
            fn, ctx, "validated summary unavailable: callee summary copy rejected",
        )
        return [AnalysisFinding(
            concern="summary",
            status="rejected",
            target_id=fn.id,
            reasons=(
                "summary가 직접 callee canonical summary와 동일하여 "
                "현재 대상의 책임을 소유하지 않음",
            ),
            candidate={"summary": result.summary, "copied_callee_id": copied_from},
            evidence_rule="code_summary",
        )]

    missing_identities = missing_structural_summary_identities(result.summary, fn)
    if missing_identities:
        _reject_summary(fn, ctx, "rejected")
        _degrade_summary_semantic_frame(
            fn, ctx, "validated summary unavailable: direct AST identities omitted",
        )
        return [AnalysisFinding(
            concern="summary",
            status="rejected",
            target_id=fn.id,
            reasons=(
                "summary omitted direct AST physical identities: "
                + ", ".join(missing_identities),
            ),
            candidate={
                "summary": result.summary,
                "missing_structural_identities": list(missing_identities),
            },
            evidence_rule="code_summary",
        )]

    unsupported_comparisons = unsupported_code_comparison_claims(
        result.summary,
        "\n".join((
            analysis_inputs["raw_source_code"],
            analysis_inputs["control_branch_index"],
            fn.expression or "",
        )),
        ctx.referenced_symbols,
    )
    if unsupported_comparisons:
        _reject_summary(fn, ctx, "rejected")
        _degrade_summary_semantic_frame(
            fn, ctx, "validated summary unavailable: comparison operands changed",
        )
        return [AnalysisFinding(
            concern="summary",
            status="rejected",
            target_id=fn.id,
            reasons=(
                "summary changed explicit comparison operands: "
                + "; ".join(unsupported_comparisons),
            ),
            candidate={
                "summary": result.summary,
                "unsupported_code_comparisons": list(unsupported_comparisons),
            },
            evidence_rule="code_summary",
        )]

    findings: list[AnalysisFinding] = []
    contradiction = _apply_summary_fields(fn, ctx, result, strategy)
    if contradiction is not None:
        findings.append(contradiction)
    ctx.summary = result.summary
    ctx.extra["summary_status"] = (
        "partial" if result.evidence_status == "partial" else "accepted"
    )
    if result.evidence_status == "partial":
        findings.append(AnalysisFinding(
            concern="summary",
            status="unverified",
            target_id=fn.id,
            reasons=tuple(result.missing_context),
            candidate=result.model_dump(),
            evidence_rule="code_summary",
        ))
    return findings


def _reject_summary(fn: CodeNode, ctx: AnalysisContext, status: str) -> None:
    fn.summary = None
    fn.stereotype = None
    ctx.summary = ""
    ctx.extra["summary_status"] = status


def _apply_summary_fields(
    fn: CodeNode,
    ctx: AnalysisContext,
    result: SummaryOutput,
    strategy: Any,
) -> AnalysisFinding | None:
    stereotype, violation = exclude_contradicted_stereotype(
        require_stereotype(result.stereotype, strategy.name),
        has_writes=bool(ctx.writes),
    )
    if violation:
        log_warning(
            _STEP,
            "stereotype_contradiction",
            "stereotype 이 결정론 쓰기 근거와 모순 — 빈 값으로 강제(사람 검토)",
            code_id=fn.id,
            reason=violation,
        )
    fn.summary = result.summary
    fn.stereotype = stereotype or None
    if not violation:
        return None
    return AnalysisFinding(
        concern="stereotype",
        status="rejected",
        target_id=fn.id,
        reasons=(violation,),
        candidate={"stereotype": result.stereotype},
        evidence_rule="code_summary",
    )


def _publish_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
    result: SummaryOutput,
) -> None:
    frame = result.semantic_frame
    if frame is None:
        raise ValueError(f"frame-first summary is missing semantic frame: {fn.id}")
    fn.extra["_summary_semantic_frame"] = frame
    ctx.extra["_summary_semantic_frame"] = frame


def _degrade_summary_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
    reason: str,
) -> None:
    frame = fn.extra.get("_summary_semantic_frame")
    if frame is None:
        raise ValueError(f"summary semantic frame missing while rejecting output: {fn.id}")
    degraded = mark_semantic_frame_insufficient(frame, (reason,))
    fn.extra["_summary_semantic_frame"] = degraded
    ctx.extra["_summary_semantic_frame"] = degraded
