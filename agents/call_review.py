"""External 호출 LLM 재검증 (spec 113) — "LLM 후보 판정 → 결정론 게이트" 배치.

문헌 결론(SEA류)대로 LLM 은 **external 잔여에만** 필터로 쓴다 — 정확 일치로 확정된
internal 은 절대 건드리지 않는다. LLM 판정은 결정론 게이트를 통과한 것만 반영:

1. 프로젝트 정의 루틴 이름은 드롭 불가 (무시 + 경고)
2. 목록 밖 이름 무시 (환각 차단)
3. 드롭 비율 상한(설정) 초과 시 **전체 무시** + 경고 — 과도 드롭은 환각 신호

호출부(link_calls_by_name_step)는 fail-open: 에이전트 실패 시 경고 후 전량 유지
(필터는 정화기 — 실패해도 데이터 손실이 없어야 한다. 호출 사실 보존이 기본값).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from agents import AGENTS_PACKAGE_DIR
from agents.contracts import ExternalCallReview
from agents.runtime import run_structured_agent
from llm.usage.cost_budget import CostBudget
from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info, log_warning

_LOG = "step5.call_review"
_SYSTEM_PROMPT_PATH = AGENTS_PACKAGE_DIR / "prompts" / "call_review.md"
_MAX_LISTED = 300          # 프롬프트 폭 통제 — 빈도순 상위만 심사


async def review_external_calls(
    external_counts: dict[str, int],
    defined_names: frozenset[str],
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> set[str]:
    """external 콜리 이름 → 드롭 확정 집합 (결정론 게이트 통과분만).

    빈 목록·비활성 설정이면 빈 집합. 게이트 위반은 조용히 버리지 않고 경고로 표면화.
    """
    if not external_counts:
        return set()

    listed = dict(Counter(external_counts).most_common(_MAX_LISTED))
    lines = [f"- {name} (호출 {count}회)" for name, count in listed.items()]
    review: ExternalCallReview = await run_structured_agent(
        agent_name="call_review",
        invoke_id="run",
        system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
        tools=[],
        response_format=ExternalCallReview,
        user_message=(
            "external 호출 목록 (이름 · 등장 횟수):\n" + "\n".join(lines)
            + "\n\n확신하는 가짜만 drops 로 제출하라."
        ),
        max_calls=SETTINGS.analysis_agents.call_review_max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        stream_emit=stream_emit,
    )

    # ── 결정론 게이트 ────────────────────────────────────────
    drops: set[str] = set()
    for verdict in review.drops:
        name = verdict.callee.strip()
        if name in defined_names:
            log_warning(_LOG, "drop_refused_internal",
                        "프로젝트 정의 루틴 드롭 시도 — 무시", callee=name)
            continue
        if name not in listed:
            log_warning(_LOG, "drop_refused_unlisted",
                        "목록 밖 이름 드롭 시도(환각) — 무시", callee=name)
            continue
        drops.add(name)

    max_ratio = SETTINGS.analysis_agents.call_review_max_drop_ratio
    if listed and len(drops) / len(listed) > max_ratio:
        log_warning(
            _LOG, "drop_cap_exceeded",
            f"드롭 비율 {len(drops)}/{len(listed)} 이 상한({max_ratio:.0%}) 초과 — "
            "환각 신호로 보고 전체 무시",
        )
        return set()

    if drops:
        log_info(_LOG, "drops_accepted", count=len(drops),
                 samples=sorted(drops)[:10])
    return drops
