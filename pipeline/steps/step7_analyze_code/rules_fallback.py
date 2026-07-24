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

import asyncio
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
    rules_out: Optional[RulesExamplesOutput],
    report: Optional[RuleQualityReport],
    code_body: str,
    control_subtree: Optional[list[SubtreeNode]],
    analyze_fragment: AnalyzeFragment,
    merge_fragments: MergeFragments,
    budget_tokens: int = FRAGMENT_BUDGET_TOKENS,
) -> FallbackOutcome:
    """함수 rules 복구. **두 진입을 하나로 통합**한다 (spec 099):

    - **품질 이상**: ``rules_out`` = 최초 전체 분석 결과, ``report`` = 이상보고.
    - **콜 실패**(길이 초과 폭주 등): ``rules_out=None``, ``report=None``. 최초 결과가 아예
      없으니 조각 분석·병합만으로 rules 를 **새로 복구**한다. 둘 다 "전체 분석이 신뢰할
      rules 를 못 냈다"는 같은 사건이라 같은 조각 메커니즘으로 처리한다.

    조각을 만들 수 없거나(작은/평면 함수) 전부 실패하면 — 실패 진입은 복구할 최초 결과도
    없으므로 **빈 rules + 미복구 플래그**로 정직하게 표면화한다(조용한 손실 금지). 이상 진입은
    종전대로 최초 결과를 재검사한다.

    BudgetExceededError(회로 차단기)만 그대로 전파한다. 그 외 조각 실패는 해당 조각만
    건너뛰고(부분 실패 은폐 금지 — log+stream failed), 나머지 조각은 계속한다(FR-026).
    """
    detected = "|".join(report.reasons) if report is not None else "call_failed"
    emit_current("Fragment", code_id, sub_action=f"detected:{detected}", status="done")

    fragments = build_fragments(code_body, control_subtree, budget_tokens)
    merged: Optional[RulesExamplesOutput] = None

    if fragments:
        emit_current("Fragment", code_id, sub_action=f"split:{len(fragments)}", status="done")
        # 조각은 서로 독립이므로 동시에 분석한다. ``gather`` 는 입력 순서를 보존하므로
        # 병합 입력은 순차 실행과 동일하다(산출물 불변). 종전 순차 실행은 폭주 폴백이
        # step7 꼬리에 걸릴 때 조각 수만큼 콜을 직렬로 쌓아 run 전체를 늘렸다
        # (run20 실측: 감지기 발동 16:59:43 → 폴백 병합 종료 17:02:28 = step7 종료 시각).
        results = await asyncio.gather(
            *(_analyze_one_fragment(code_id, frag, analyze_fragment) for frag in fragments),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BudgetExceededError):
                raise result        # 회로 차단기 — 그대로 전파
        fragment_outputs: list[RulesExamplesOutput] = [
            r for r in results if isinstance(r, RulesExamplesOutput)
        ]
        if fragment_outputs:
            merged = await _merge(code_id, fragment_outputs, merge_fragments)
        else:
            log_warning(_STEP, "all_fragments_failed",
                        "모든 조각 분석 실패 — 최초 전체 분석 결과로 수렴 후 재검사",
                        code_id=code_id, fragments=len(fragments))

    # 병합 성공이면 병합 결과, 아니면(수렴/조각·병합 실패) 최초 전체 분석 결과를 재검사.
    final = merged if merged is not None else rules_out
    fragment_count = len(fragments)

    if final is None:
        # 콜 실패 진입인데 조각을 못 만들거나(control_subtree 없음) 전부 실패 — 복구할
        # 최초 결과도 없다. 빈 rules 를 미복구로 표면화한다(조용한 손실 금지 — 헌법 IV).
        empty = RulesExamplesOutput()
        log_warning(_STEP, "rules_unrecovered",
                    "rules 콜 실패 후 조각 복구 불가(조각 없음/전부 실패) — 빈 rules + 미복구 플래그",
                    code_id=code_id, fragments=fragment_count)
        emit_current("Fragment", code_id, sub_action="unrecovered", status="failed")
        return FallbackOutcome(
            empty, check_rule_quality(empty, code_body),
            STATUS_FALLBACK_FLAGGED, fragment_count, ANOMALY_FLAG,
        )

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
