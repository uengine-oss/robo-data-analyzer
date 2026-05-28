"""3 분할 fan-out 헬퍼 — ``analyze_function_step`` 이 함수당 1 회 호출.

함수 1개당 LLM 호출 (병렬, 2~3 번):
- ``function_summary``        → summary + stereotype + logical_name (항상)
- ``function_rules_examples`` → rules + examples + questions (항상)
- ``function_table_insights`` → table_insights (R/W 테이블 있을 때만 — 없으면 skip)

**3분할 입력 구조** = [공통 context] + [#3 전용]:
- [공통] = 본문 + module_intent + codebase_notes + callee + reads/writes + 참조심볼
  + 청크 + 조상 골격. #1/#2/#3 동일 → RadixAttention prefix 캐시 적중.
- [#3 전용] = 자식 range 의 비즈니스 규칙(rules·questions). 규칙 분할에만 append —
  #1/#2 엔 무관(토큰 낭비)이라 안 넣음.

**KV 캐시 흐름** (사용자 원칙):
같은 함수의 3분할은 입력값이 거의 동일. #1 (summary) 가 먼저 끝나면 LLM 서버
prefix cache 에 [system 공통지침 + user 입력값] 올라감. 그 후 #2 (rules) + #3
(insights) 가 동시 발사돼도 둘 다 그 cache 적중 (cache 는 한 번 올리면 동시 다중
호출이 다 적중 가능).

→ ``await run_summary`` → ``asyncio.gather(rules, insights)`` 순서면 충분.
   gather_with_warmup 같은 별도 워밍 헬퍼 불필요 (await 자체가 순서 강제).

**Round 단위 워밍**: ``analyze_function_step`` 가 Round 첫 함수의 ``run_summary``
만 단독 선호출 → 공통지침 cache 만들기. 그 후 모든 함수 풀어놓음 (각자 자기
#1 → #2 #3 흐름). 첫 함수는 ``precomputed_summary`` 로 #1 중복 호출 방지.

DBMS 부모 range 는 본문이 ``compact_body`` (자식 구간 → 자식 summary 치환) — 자식이
먼저 분석되어 그 결과가 부모 입력으로 합류. Framework 는 평면이라 raw 본문.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from analysis_flow.step6_analyze_function.range_body import ancestor_context, body_for
from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import Function
from shared.lang.ontology import coerce_stereotype
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from data_models.function_analysis_output import (
    RulesExamplesOutput,
    SummaryOutput,
    TableInsightsOutput,
)
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_warning
from shared.source_file_cache import SourceFileCache

_STEP = "step6.analyze_function.three_split"


async def run_summary(
    function_id: str,
    contexts: dict[str, AnalysisContext],
    function_by_id: dict[str, Function],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
    module_intents: Optional[dict[str, str]] = None,
    table_descriptions: Optional[dict[str, str]] = None,
    source_cache: Optional[SourceFileCache] = None,
) -> Optional[SummaryOutput]:
    """#1 summary 단독 호출 — Round 첫 함수의 cache 워밍용.

    ``analyze_function_step`` 가 Round 시작 시 첫 함수에 대해 호출 → cache 에
    [공통지침 + 첫 함수 입력값] 올림. 결과를 ``precomputed_summary`` 로 첫 함수의
    ``analyze_function_three_split`` 에 다시 전달해 #1 중복 호출 방지.

    Returns:
        SummaryOutput. 실패 시 None (BudgetExceededError 만 raise).
    """
    ctx = contexts.get(function_id)
    fn = function_by_id.get(function_id)
    if ctx is None or fn is None:
        return None

    common = _build_user_values(
        ctx, fn, contexts, codebase_notes,
        module_intents or {}, table_descriptions or {},
        source_cache or SourceFileCache(),
    )
    try:
        return await run_yaml_rule(
            prompts_dir=Path(strategy.prompts_dir),
            rule_name="function_summary",
            user_values=common,
            response_model=SummaryOutput,
            budget=cost_budget,
        )
    except BudgetExceededError:
        raise
    except Exception as e:  # noqa: BLE001 — silent 차단 목적
        log_error(_STEP, "warmup_summary_failed", e, function_id=function_id)
        return None


async def analyze_function_three_split(
    function_id: str,
    contexts: dict[str, AnalysisContext],
    function_by_id: dict[str, Function],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
    module_intents: Optional[dict[str, str]] = None,
    table_descriptions: Optional[dict[str, str]] = None,
    source_cache: Optional[SourceFileCache] = None,
    precomputed_summary: Optional[SummaryOutput] = None,
) -> Optional[Function]:
    """함수 1개의 3 분할 분석 — 결과를 ``Function`` 에 적용해 반환.

    흐름: #1 summary → (#2 rules + #3 insights 동시). #1 이 cache 에 자기 함수
    입력값을 올린 후 #2 #3 가 그 cache 적중. ``precomputed_summary`` 가 있으면
    #1 재호출 안 함 (Round 첫 함수의 워밍 결과 재사용).

    Args:
        codebase_notes: ``ScanResult.codebase_notes`` — prefix 공유로 캐시 적중.
        module_intents: ``{module_id: intent}`` — module_pre_analysis(M9) 결과.
        table_descriptions: ``{table_fqn: DDL 설명}`` — reads/writes 렌더에 의미 부여.
        precomputed_summary: Round 첫 함수의 워밍 결과. 있으면 #1 호출 skip.

    Returns:
        summary/stereotype/logical_name 채워진 Function. summary 실패 시 None.
    """
    ctx = contexts.get(function_id)
    fn = function_by_id.get(function_id)
    if ctx is None or fn is None:
        return None

    # [공통 context] — 3분할 동일 (캐시되는 prefix).
    common = _build_user_values(
        ctx, fn, contexts, codebase_notes,
        module_intents or {}, table_descriptions or {},
        source_cache or SourceFileCache(),
    )
    # [#3 전용] — 자식 range 의 비즈니스 규칙을 규칙 분할에만 append.
    rules_values = {**common, "child_business_logic": _format_child_business_logic(ctx, contexts)}
    prompts_dir = Path(strategy.prompts_dir)

    # Phase 1: summary — 자기 함수의 cache 만들기 (precomputed 있으면 skip).
    if precomputed_summary is not None:
        summary_out = precomputed_summary
    else:
        try:
            summary_out = await run_yaml_rule(
                prompts_dir=prompts_dir, rule_name="function_summary",
                user_values=common, response_model=SummaryOutput, budget=cost_budget,
            )
        except BudgetExceededError:
            raise
        except Exception as e:  # noqa: BLE001 — silent 차단
            log_error(_STEP, "summary_failed", e, function_id=function_id)
            return None

    # Phase 2: rules + insights 동시 — 자기 함수 cache 적중 (#1 이 방금 올림).
    # 부분 성공 허용 — rules/insights 한 조각 실패가 함수 폐기로 이어지지 않게.
    # BudgetExceededError 만 즉시 전파 (회로 차단기 — 절대원칙 #3).
    tasks = {
        "rules": run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="function_rules_examples",
            user_values=rules_values, response_model=RulesExamplesOutput, budget=cost_budget,
        ),
    }
    if ctx.reads or ctx.writes:
        tasks["insights"] = run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="function_table_insights",
            user_values=common, response_model=TableInsightsOutput, budget=cost_budget,
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    done = dict(zip(tasks, results))

    for result in results:
        if isinstance(result, BudgetExceededError):
            raise result

    rules_out = _take(done, "rules", function_id, required=False)
    insights_out = _take(done, "insights", function_id, required=False)  # 키 없으면 None

    # partial = 호출했는데 실패한 조각 (호출 안 한 #2 skip 은 partial 아님).
    partial_parts = tuple(
        k for k in ("rules", "insights")
        if k in tasks and isinstance(done[k], BaseException)
    )

    # fan-in 머지 → Function 에 적용
    # stereotype 은 ontology_roles.yaml 풀과 검증 (없으면 빈 문자열로 강제)
    validated_stereotype = coerce_stereotype(summary_out.stereotype or "", strategy.name)
    fn.summary = summary_out.summary
    fn.stereotype = validated_stereotype or None
    fn.logical_name = summary_out.logical_name or None
    fn.extra["table_insights"] = (
        [t.model_dump() for t in insights_out.table_insights] if insights_out else []
    )
    fn.extra["rules"] = [r.model_dump() for r in rules_out.rules] if rules_out else []
    fn.extra["examples"] = [e.model_dump() for e in rules_out.examples] if rules_out else []
    fn.extra["questions"] = [q.model_dump() for q in rules_out.questions] if rules_out else []
    if partial_parts:
        fn.extra["partial"] = partial_parts

    # ctx 에 분석 결과 부착 — Bottom-up 으로 상위(caller·부모)가 라이브 조회.
    # summary → callee/compact 본문 / rules·questions → 부모 #3 분할 입력.
    ctx.summary = summary_out.summary
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn


def _take(
    done: dict, key: str, function_id: str, *, required: bool,
):
    """gather(return_exceptions=True) 결과 1조각 → 성공이면 인스턴스, 실패면 None.

    실패 시 ``required=True`` 는 log_error(이 함수 폐기), ``required=False`` 는
    log_warning + None (이 조각만 빈 결과로 진행). 키 자체가 없으면(테이블 없어 #2
    skip 등) silent None — 정상 흐름.
    """
    if key not in done:
        return None
    result = done[key]
    if isinstance(result, BaseException):
        if required:
            log_error(_STEP, f"{key}_failed", result, function_id=function_id)
        else:
            log_warning(
                _STEP, f"{key}_failed",
                f"{key} 조각 실패 — 빈 결과로 진행 (partial 마크)",
                function_id=function_id,
                error=f"{type(result).__name__}: {result}",
            )
        return None
    return result


def _build_user_values(
    ctx: AnalysisContext,
    fn: Function,
    contexts: dict[str, AnalysisContext],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, str],
    cache: SourceFileCache,
) -> dict:
    """3 yaml 공통 ``user_template`` 의 치환 값 ([공통 context]).

    DBMS 부모 range 는 ``function_body`` 가 compact (자식 구간 → 자식 summary 치환).
    ``ancestor_context`` 는 조상 range 골격 (Framework 는 평면이라 "(없음)").
    ``reads`` / ``writes`` 는 테이블 fqn + DDL 설명 — LLM 이 테이블 의미를 알고 분석.
    """
    return {
        "function_id": fn.function_id,
        "signature": fn.signature,
        "function_body": body_for(ctx, contexts, cache),
        "codebase_notes": codebase_notes or "(없음)",
        "module_intent": module_intents.get(fn.module_id) or "(없음)",
        "referenced_symbols": "\n".join(ctx.referenced_symbols) or "(없음)",
        "callee_summaries": _format_callee_summaries(ctx, contexts),
        "ancestor_context": ancestor_context(ctx, contexts, cache),
        "reads": _format_table_refs(ctx.reads, table_descriptions),
        "writes": _format_table_refs(ctx.writes, table_descriptions),
    }


def _format_table_refs(
    fqns: tuple[str, ...],
    table_descriptions: dict[str, str],
) -> str:
    """테이블 fqn 목록 → ``- fqn: DDL설명`` 줄들. 설명 없으면 fqn 만.

    LLM 이 R/W 테이블의 의미(DDL COMMENT / 주석)를 알고 분석하도록. 빈 목록은 "(없음)".
    """
    if not fqns:
        return "(없음)"
    lines = []
    for fqn in fqns:
        desc = table_descriptions.get(fqn, "")
        lines.append(f"    - {fqn}: {desc}" if desc else f"    - {fqn}")
    return "\n" + "\n".join(lines)


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


def _format_child_business_logic(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> str:
    """자식 range 들의 rules + questions → #3 규칙 분할 전용 입력.

    Bottom-up 으로 자식이 먼저 분석돼 ``ctx.extra["rules"]`` 가 채워짐. 부모는 이 규칙을
    자기 분기 조건으로 감싸 강화 (자식 규칙 재추출 X). 자식 없으면 "(없음)".
    """
    child_ids = ctx.extra.get("child_ids", ())
    if not child_ids:
        return "(없음)"
    rules: list = []
    questions: list = []
    for cid in child_ids:
        c = contexts.get(cid)
        if c is None:
            continue
        rules.extend(c.extra.get("rules", ()))
        questions.extend(c.extra.get("questions", ()))
    if not rules and not questions:
        return "(없음)"
    parts: list[str] = []
    if rules:
        parts.append("[자식 규칙]")
        for r in rules:
            stmt = r.get("statement", "") if isinstance(r, dict) else str(r)
            if stmt:
                parts.append(f"- {stmt}")
    if questions:
        parts.append("[자식 질문]")
        for q in questions:
            text = q if isinstance(q, str) else q.get("text", "")
            if text:
                parts.append(f"- {text}")
    return "\n".join(parts)
