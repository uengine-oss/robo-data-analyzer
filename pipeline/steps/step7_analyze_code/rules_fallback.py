"""Rules/Examples 이상 복구 폴백 오케스트레이션 (spec 054 US3 / FR-016·019~026).

정상 경로(이상 없음)는 이 모듈을 **타지 않는다** — ``three_split_llm_fanout`` 의 훅이
품질검사 이상일 때만 진입시킨다(성능·캐시 불변, FR-016).

이상 시 흐름(무한 재시도 없음 — 최초→폴백 1회→재검사가 경계, FR-023):

    감지 → 결정론 분할(ast_budget_splitter, LLM 0) → 조각별 rules LLM →
    병합 LLM 1회(code_rules_merge) → 재검사 →
      · 통과: 병합 결과 저장(fallback_ok)
      · 재이상: 결정론 dedupe + 이상 플래그(fallback_flagged) — run 은 죽이지 않음

분할할 제어문 서브트리가 없으면(작은 함수 / parser 가 아직 control AST 미emit)
**추가 LLM 없이** 최초 전체 분석 결과를 그대로 재검사만 한다(FR-020 edge: 전체 재분석
1회로 수렴). 이때도 재이상이면 dedupe + 플래그.

LLM 배관(run_yaml_rule·프롬프트·예산)은 호출부(three_split)가 콜러블로 주입한다 —
이 모듈은 결정론 오케스트레이션 + 스트림 가시성만 담당(테스트 가능, 책임 분리).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from pipeline.steps.step7_analyze_code.ast_budget_splitter import (
    CodeFragment,
    SubtreeNode,
    split,
)
from pipeline.steps.step7_analyze_code.rule_quality import (
    RuleQualityReport,
    check_rule_quality,
    deterministic_dedupe,
)
from pipeline.models.code_analysis_output import RulesExamplesOutput
from llm.usage.cost_budget import BudgetExceededError
from shared.events.runtime_logger import log_error, log_warning
from shared.events.processing_events import emit_current, track_processing

_STEP = "step7.analyze_code.rules_fallback"

# 조각 예산(문자 길이 근사 토큰, research.md §2 시작값 — 실측 후 조정). 함수 전체가 이 예산
# 안이면 분할하지 않고 전체 재분석 1회로 수렴한다.
FRAGMENT_BUDGET_TOKENS = 500

# 최종 상태 라벨 — FallbackRun.final_state(스펙 Key Entities)와 정렬.
STATUS_FALLBACK_OK = "fallback_ok"            # 병합/수렴 후 재검사 통과
STATUS_FALLBACK_FLAGGED = "fallback_flagged"  # 재검사 재이상 → dedupe + 플래그
ANOMALY_FLAG = "rules_quality_anomaly"        # fn.extra 에 남길 이상 플래그 값

# 조각/병합 LLM 콜러블 계약(호출부 주입).
AnalyzeFragment = Callable[[CodeFragment], Awaitable[Optional[RulesExamplesOutput]]]
MergeFragments = Callable[[list[RulesExamplesOutput]], Awaitable[Optional[RulesExamplesOutput]]]


@dataclass
class FallbackOutcome:
    """폴백 1건 산출 — 호출부가 함수 노드에 반영."""
    rules_out: RulesExamplesOutput        # 최종 rules/examples (병합 또는 dedupe 결과)
    recheck: RuleQualityReport            # 재검사 결과
    status: str                           # STATUS_FALLBACK_OK | STATUS_FALLBACK_FLAGGED
    fragment_count: int                   # 분할 조각 수(0 = 수렴 경로)
    anomaly_flag: Optional[str]           # 재이상 시 ANOMALY_FLAG, 통과 시 None


def build_fragments(
    code_body: str,
    control_subtree: Optional[list[SubtreeNode]],
    budget_tokens: int = FRAGMENT_BUDGET_TOKENS,
) -> list[CodeFragment]:
    """함수 본문 + 제어문 서브트리 → 조각 목록(예산 안이거나 서브트리 없으면 ``[]``).

    좌표 계약: ``control_subtree`` 노드의 start/end 는 ``code_body`` 기준 1-based 줄번호.
    (reader/T013 가 owner 함수 컨텍스트에 이 좌표로 실어 보낸다 — 그래프 재조회 없음.)
    """
    if not control_subtree:
        return []
    code_lines = code_body.splitlines()
    return split(
        func_start=1,
        func_end=len(code_lines),
        top_children=list(control_subtree),
        code_lines=code_lines,
        budget_tokens=budget_tokens,
    )


async def run_rules_fallback(
    *,
    code_id: str,
    rules_out: RulesExamplesOutput,
    report: RuleQualityReport,
    code_body: str,
    control_subtree: Optional[list[SubtreeNode]],
    analyze_fragment: AnalyzeFragment,
    merge_fragments: MergeFragments,
    budget_tokens: int = FRAGMENT_BUDGET_TOKENS,
) -> FallbackOutcome:
    """이상 판정된 함수 복구. ``rules_out``/``report`` 는 최초 전체 분석의 결과·이상보고.

    BudgetExceededError(회로 차단기)만 그대로 전파한다. 그 외 조각 실패는 해당 조각만
    건너뛰고(부분 실패 은폐 금지 — log+stream failed), 나머지 조각은 계속한다(FR-026).
    """
    emit_current("Fragment", code_id, sub_action=f"detected:{'|'.join(report.reasons)}", status="done")

    fragments = build_fragments(code_body, control_subtree, budget_tokens)
    merged: Optional[RulesExamplesOutput] = None

    if fragments:
        emit_current("Fragment", code_id, sub_action=f"split:{len(fragments)}", status="done")
        fragment_outputs: list[RulesExamplesOutput] = []
        for frag in fragments:
            out = await _analyze_one_fragment(code_id, frag, analyze_fragment)
            if out is not None:
                fragment_outputs.append(out)
        if fragment_outputs:
            merged = await _merge(code_id, fragment_outputs, merge_fragments)
        else:
            log_warning(_STEP, "all_fragments_failed",
                        "모든 조각 분석 실패 — 최초 전체 분석 결과로 수렴 후 재검사",
                        code_id=code_id, fragments=len(fragments))

    # 병합 성공이면 병합 결과, 아니면(수렴/조각·병합 실패) 최초 전체 분석 결과를 재검사.
    final = merged if merged is not None else rules_out
    fragment_count = len(fragments)

    recheck = check_rule_quality(final, code_body)
    emit_current("Fragment", code_id,
                 sub_action=f"recheck:{'anomaly' if recheck.is_anomaly else 'ok'}", status="done")

    if not recheck.is_anomaly:
        emit_current("Fragment", code_id, sub_action=STATUS_FALLBACK_OK, status="done")
        return FallbackOutcome(final, recheck, STATUS_FALLBACK_OK, fragment_count, None)

    # 재이상 — 무한 재시도 없음. 지울 수 있는 완전중복만 결정론 정리 + 이상 플래그(FR-023).
    deduped = deterministic_dedupe(final)
    post = check_rule_quality(deduped, code_body)
    log_error(_STEP, "fallback_recheck_anomaly",
              RuntimeError("폴백 재검사 이상 — 부분 결과 + 이상 플래그 저장(무한 재시도 금지)"),
              code_id=code_id, reasons=recheck.reasons, post_dedupe_reasons=post.reasons)
    emit_current("Fragment", code_id, sub_action=STATUS_FALLBACK_FLAGGED, status="failed")
    return FallbackOutcome(deduped, post, STATUS_FALLBACK_FLAGGED, fragment_count, ANOMALY_FLAG)


async def _analyze_one_fragment(
    code_id: str, frag: CodeFragment, analyze_fragment: AnalyzeFragment,
) -> Optional[RulesExamplesOutput]:
    """조각 1개 rules LLM — 실패는 해당 조각만 건너뜀(나머지 계속, 은폐 금지)."""
    try:
        async with track_processing("Fragment", frag.fragment_id, sub_action="frag_rules"):
            return await analyze_fragment(frag)
    except BudgetExceededError:
        raise
    except Exception as e:  # noqa: BLE001 — 조각 부분 실패 허용(FR-026: log+stream failed 후 계속)
        log_warning(_STEP, "fragment_failed",
                    "조각 분석 실패 — 이 조각 건너뜀(나머지 조각 계속)",
                    code_id=code_id, fragment_id=frag.fragment_id,
                    error=f"{type(e).__name__}: {e}")
        return None


async def _merge(
    code_id: str, fragment_outputs: list[RulesExamplesOutput], merge_fragments: MergeFragments,
) -> Optional[RulesExamplesOutput]:
    """조각 결과 병합 LLM 1회 — 실패 시 None(수렴 경로로 폴백)."""
    try:
        async with track_processing("Fragment", code_id, sub_action="merge"):
            return await merge_fragments(fragment_outputs)
    except BudgetExceededError:
        raise
    except Exception as e:  # noqa: BLE001 — 병합 실패는 수렴(최초 결과 재검사)로 폴백
        log_warning(_STEP, "merge_failed",
                    "병합 실패 — 최초 전체 분석 결과로 수렴 후 재검사",
                    code_id=code_id, error=f"{type(e).__name__}: {e}")
        return None
