"""3 분할 fan-out 헬퍼 — ``analyze_code_step`` 이 코드노드당 1 회 호출.

코드노드 1개당 LLM 호출 (병렬, 2~3 번):
- ``code_summary``        → summary + stereotype + logical_name (항상)
- ``code_rules_examples`` → rules + examples + questions (항상 — framework 는 함수 단위)
- ``code_table_insights`` → table_insights (R/W 테이블 있을 때만 — 없으면 skip)

**3분할 입력 구조** = [공통 context] + [#3 전용]:
- [공통] = 본문 + module_intent + codebase_notes + callee + reads/writes + 참조심볼
  + 청크 + 조상 골격. #1/#2/#3 동일 → RadixAttention prefix 캐시 적중.
- [#3 전용] = 자식 range 의 비즈니스 규칙(rules·questions). 규칙 분할에만 append —
  #1/#2 엔 무관(토큰 낭비)이라 안 넣음.

**KV 캐시 흐름** (사용자 원칙):
같은 코드노드의 3분할은 입력값이 거의 동일. #1 (summary) 가 먼저 끝나면 LLM 서버
prefix cache 에 [system 공통지침 + user 입력값] 올라감. 그 후 #2 (rules) + #3
(insights) 가 동시 발사돼도 둘 다 그 cache 적중 (cache 는 한 번 올리면 동시 다중
호출이 다 적중 가능).

→ ``await run_summary`` → ``asyncio.gather(rules, insights)`` 순서면 충분.
   gather_with_warmup 같은 별도 워밍 헬퍼 불필요 (await 자체가 순서 강제).

**Round 단위 워밍**: ``analyze_code_step`` 가 Round 첫 코드노드의 ``run_summary``
만 단독 선호출 → 공통지침 cache 만들기. 그 후 모든 코드노드 풀어놓음 (각자 자기
#1 → #2 #3 흐름). 첫 코드노드는 ``precomputed_summary`` 로 #1 중복 호출 방지.

DBMS 부모 range 는 본문이 ``compact_body`` (자식 구간 → 자식 summary 치환) — 자식이
먼저 분석되어 그 결과가 부모 입력으로 합류. Framework 는 평면이라 raw 본문.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from pipeline.steps.step7_analyze_code.range_body import ancestor_context, body_for
from pipeline.steps.step7_analyze_code.rule_quality import check_rule_quality
from pipeline.steps.step7_analyze_code.rules_fallback import run_rules_fallback
from pipeline.steps.step7_analyze_code.rw_table_filter import constrain_tables_to_rw
from pipeline.steps.step7_analyze_code.table_prompt_context import dump_table_refs
from pipeline.steps.step7_analyze_code.summary_quality import enforce_summary_boundary
from pipeline.models.analysis_context import AnalysisContext, dump_external_evidence, dump_referenced_symbols
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import coerce_stereotype
from llm.usage.cost_budget import BudgetExceededError, CostBudget
from pipeline.models.code_analysis_output import (
    RulesExamplesOutput,
    SummaryCoreOutput,
    SummaryOutput,
    TableInsightsOutput,
)
from llm.runners.yaml_llm_runner import run_yaml_rule
from pipeline.steps.step7_analyze_code.domain_terms import normalize_domain_terms
from shared.events.runtime_logger import log_error, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache

_STEP = "step7.analyze_code.three_split"


async def run_summary(
    code_id: str,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
    module_intents: Optional[dict[str, str]] = None,
    table_descriptions: Optional[dict[str, dict]] = None,
    source_cache: Optional[SourceFileCache] = None,
) -> Optional[SummaryOutput]:
    """#1 summary 단독 호출 — Round 첫 코드노드의 cache 워밍용.

    ``analyze_code_step`` 가 Round 시작 시 첫 코드노드에 대해 호출 → cache 에
    [공통지침 + 첫 코드노드 입력값] 올림. 결과를 ``precomputed_summary`` 로 첫 코드노드의
    ``analyze_code_three_split`` 에 다시 전달해 #1 중복 호출 방지.

    track_processing 이 start/done/failed 자동 emit (stream 인자 chain X).

    Returns:
        SummaryOutput. 실패 시 None (BudgetExceededError 만 raise).
    """
    ctx = contexts.get(code_id)
    fn = code_by_id.get(code_id)
    if ctx is None or fn is None:
        log_warning(
            _STEP, "warmup_input_missing",
            "워밍 요약 입력 부재 — 이 Round 는 캐시 프리픽스 워밍 없이 진행한다.",
            code_id=code_id, ctx_missing=ctx is None, node_missing=fn is None,
        )
        return None

    common = _build_user_values(
        ctx, fn, contexts, codebase_notes,
        module_intents or {}, table_descriptions or {},
        source_cache or SourceFileCache(),
    )
    try:
        async with track_processing("CodeNode", code_id, sub_action="warmup_summary"):
            return await run_yaml_rule(
                prompts_dir=Path(strategy.prompts_dir),
                rule_name="code_summary",
                user_values=common,
                response_model=SummaryOutput,
                budget=cost_budget,
            )
    except BudgetExceededError:
        raise        # 회로 차단기 — 그대로 전파
    except Exception as e:  # noqa: BLE001 — silent 차단 목적
        log_error(_STEP, "warmup_summary_failed", e, code_id=code_id)
        return None


async def analyze_code_three_split(
    code_id: str,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
    module_intents: Optional[dict[str, str]] = None,
    table_descriptions: Optional[dict[str, dict]] = None,
    source_cache: Optional[SourceFileCache] = None,
    precomputed_summary: Optional[SummaryOutput] = None,
) -> Optional[CodeNode]:
    """코드노드 1개의 3 분할 분석 — 결과를 ``CodeNode`` 에 적용해 반환.

    흐름: #1 summary → (#2 rules + #3 insights 동시). #1 이 cache 에 자기 코드노드
    입력값을 올린 후 #2 #3 가 그 cache 적중. ``precomputed_summary`` 가 있으면
    #1 재호출 안 함 (Round 첫 코드노드의 워밍 결과 재사용).

    Args:
        codebase_notes: ``ScanResult.codebase_notes`` — prefix 공유로 캐시 적중.
        module_intents: ``{module_id: intent}`` — module_pre_analysis(M9) 결과.
        table_descriptions: ``{table_fqn: DDL 설명}`` — reads/writes 렌더에 의미 부여.
        precomputed_summary: Round 첫 코드노드의 워밍 결과. 있으면 #1 호출 skip.

    Returns:
        summary/stereotype/logical_name 채워진 CodeNode. summary 실패 시 None.
    """
    ctx = contexts.get(code_id)
    fn = code_by_id.get(code_id)
    if ctx is None or fn is None:
        # 조용히 None 을 내면 호출부가 analyzed/failed 어디에도 안 세어 노드가 실종된다
        # (헌법 IV / spec 045 FR-008) → 원인(둘 중 뭐가 없는지)을 남긴다.
        log_warning(
            _STEP, "analysis_input_missing",
            "코드노드 분석 입력 부재 — 이 노드는 summary 없이 남는다(검색 코퍼스 제외).",
            code_id=code_id, ctx_missing=ctx is None, node_missing=fn is None,
        )
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

    # split 단위 wrapper — code_id 캡처 closure (응집 ★).
    async def _track_split(sub_action: str, coro):
        async with track_processing("CodeNode", code_id, sub_action=sub_action):
            return await coro

    # Phase 1: summary — 자기 코드노드의 cache 만들기 (precomputed 있으면 skip).
    if precomputed_summary is not None:
        summary_out = precomputed_summary
    else:
        try:
            summary_out = await _track_split(
                "split_summary",
                run_yaml_rule(
                    prompts_dir=prompts_dir, rule_name="code_summary",
                    user_values=common, response_model=SummaryOutput, budget=cost_budget,
                ),
            )
        except BudgetExceededError:
            raise
        except Exception as e:  # noqa: BLE001 — silent 차단
            # 검색 필드 생성 중 출력 폭주(LengthFinishReasonError 등)로 실패 시, 핵심 3필드
            # 축소 스키마로 1회 폴백 — 노드 상실(요약 없는 노드 = 검색 코퍼스 탈락) 방지.
            # 실측: b720(간편결제사 확인) 나열형 필드 반복 폭주. 재발 감시용 명시 로그(greppable
            # 태그 "search_field_overflow"). domain_terms는 fixed-item 배열이며
            # search_text 또는 provider 이상 응답에도 대비해 핵심 요약 폴백은 유지한다.
            log_warning(_STEP, "search_field_overflow",
                        f"summary+검색필드 실패({type(e).__name__}) — 핵심 3필드 축소 폴백 재호출 "
                        f"(나열형 필드 출력 폭주 의심)",
                        code_id=code_id)
            try:
                core = await _track_split(
                    "split_summary_core",
                    run_yaml_rule(
                        prompts_dir=prompts_dir, rule_name="code_summary_core",
                        user_values=common, response_model=SummaryCoreOutput, budget=cost_budget,
                    ),
                )
                summary_out = SummaryOutput(
                    summary=core.summary, stereotype=core.stereotype,
                    logical_name=core.logical_name,
                )
            except BudgetExceededError:
                raise
            except Exception as e2:  # noqa: BLE001 — silent 차단
                log_error(_STEP, "summary_failed", e2, code_id=code_id)
                return None

    summary_out.summary = await enforce_summary_boundary(
        summary=summary_out.summary,
        node_name=fn.name,
        logical_name=summary_out.logical_name,
        search_text=summary_out.search_text,
        budget=cost_budget,
        audit_id=code_id,
    )

    # Phase 2: rules + insights 동시 — 자기 코드노드 cache 적중 (#1 이 방금 올림).
    # 부분 성공 허용 — rules/insights 한 조각 실패가 코드노드 폐기로 이어지지 않게.
    # BudgetExceededError 만 즉시 전파 (회로 차단기 — 절대원칙 #3).
    # 두 split 은 코드노드 결과 합성 단위라 gather 가 자연 (라운드 단위 X — 그건 round 루프 영역).
    tasks = {
        "rules": _track_split(
            "split_rules",
            run_yaml_rule(
                prompts_dir=prompts_dir, rule_name="code_rules_examples",
                user_values=rules_values, response_model=RulesExamplesOutput, budget=cost_budget,
            ),
        ),
    }
    if ctx.reads or ctx.writes:
        tasks["insights"] = _track_split(
            "split_insights",
            run_yaml_rule(
                prompts_dir=prompts_dir, rule_name="code_table_insights",
                user_values=common, response_model=TableInsightsOutput, budget=cost_budget,
            ),
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    done = dict(zip(tasks, results))

    for result in results:
        if isinstance(result, BudgetExceededError):
            raise result

    rules_out = _take(done, "rules", code_id)
    insights_out = _take(done, "insights", code_id)  # 키 없으면 None

    # partial = 호출했는데 실패한 조각 (호출 안 한 #2 skip 은 partial 아님).
    partial_parts = tuple(
        k for k in ("rules", "insights")
        if k in tasks and isinstance(done[k], BaseException)
    )

    # spec 054 US3: rules 이상 시에만 폴백(정상 경로는 통째 스킵 — 성능·캐시 불변, FR-016).
    # 최초 전체 분석의 rules_out 을 결정론 품질검사 → 이상이면 분할→조각분석→병합→재검사.
    fallback_flag: Optional[str] = None
    if rules_out is not None:
        quality = check_rule_quality(rules_out, common["code_body"])
        if quality.is_anomaly:
            outcome = await run_rules_fallback(
                code_id=code_id,
                rules_out=rules_out,
                report=quality,
                code_body=common["code_body"],
                control_subtree=ctx.extra.get("control_subtree"),
                analyze_fragment=_make_fragment_analyzer(prompts_dir, common, cost_budget),
                merge_fragments=_make_fragment_merger(prompts_dir, common, fn.signature, cost_budget),
            )
            rules_out = outcome.rules_out
            fallback_flag = outcome.anomaly_flag

    # spec 027: LLM 귀속 테이블을 결정론 reads/writes 로 제한 (callee 함수명 오귀속 차단).
    constrain_tables_to_rw(
        code_id, ctx.reads, ctx.writes, insights_out, rules_out,
        allowed_columns_by_table=ctx.extra.get("ddl_columns_by_table"),
    )

    # fan-in 머지 → CodeNode 에 적용
    # stereotype 은 ontology_roles.yaml 풀과 검증 (없으면 빈 문자열로 강제)
    validated_stereotype = coerce_stereotype(summary_out.stereotype or "", strategy.name)
    fn.summary = summary_out.summary
    fn.stereotype = validated_stereotype or None
    fn.logical_name = summary_out.logical_name or None
    fn.search_text = summary_out.search_text or None
    fn.domain_terms = normalize_domain_terms(
        summary_out.domain_terms,
        source_text=common["code_body"],
        code_id=fn.id,
        component=_STEP,
    )
    fn.extra["table_insights"] = (
        [t.model_dump() for t in insights_out.table_insights] if insights_out else []
    )
    fn.extra["rules"] = [r.model_dump() for r in rules_out.rules] if rules_out else []   # examples 는 각 rule 하위 중첩
    fn.extra["questions"] = [q.model_dump() for q in rules_out.questions] if rules_out else []
    if partial_parts:
        fn.extra["partial"] = partial_parts
    if fallback_flag:
        # 폴백 재검사도 이상 → 결정론 dedupe 한 부분 결과 + 이상 플래그(silent success 금지, FR-023).
        fn.extra["rules_quality_flag"] = fallback_flag

    # ctx 에 분석 결과 부착 — Bottom-up 으로 상위(caller·부모)가 라이브 조회.
    # summary → callee/compact 본문 / rules·questions → 부모 #3 분할 입력.
    ctx.summary = summary_out.summary
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn


def _take(done: dict, key: str, code_id: str):
    """gather(return_exceptions=True) 결과 1조각 → 성공이면 인스턴스, 실패면 None.

    실패 시 log_warning + None (이 조각만 빈 결과로 진행, partial 마크). 키 자체가
    없으면(테이블 없어 #2 skip 등) silent None — 정상 흐름.
    """
    if key not in done:
        return None
    result = done[key]
    if isinstance(result, BaseException):
        log_warning(
            _STEP, f"{key}_failed",
            f"{key} 조각 실패 — 빈 결과로 진행 (partial 마크)",
            code_id=code_id,
            error=f"{type(result).__name__}: {result}",
        )
        return None
    return result


def _build_user_values(
    ctx: AnalysisContext,
    fn: CodeNode,
    contexts: dict[str, AnalysisContext],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
) -> dict:
    """3 yaml 공통 ``user_template`` 의 치환 값 ([공통 context]).

    DBMS 부모 range 는 ``code_body`` 가 compact (자식 구간 → 자식 summary 치환).
    ``ancestor_context`` 는 조상 range 골격 (Framework 는 평면이라 "(없음)").
    ``reads`` / ``writes`` 는 테이블 fqn + DDL 설명 — LLM 이 테이블 의미를 알고 분석.
    """
    return {
        "id": fn.id,
        "signature": fn.signature,
        "code_body": body_for(ctx, contexts, cache),
        "codebase_notes": codebase_notes or "(없음)",
        "module_intent": module_intents.get(fn.owner_id) or "(없음)",
        "referenced_symbols": dump_referenced_symbols(ctx.referenced_symbols),
        "external_evidence": dump_external_evidence(ctx.external_evidence),
        "callee_summaries": _format_callee_summaries(ctx, contexts),
        "ancestor_context": ancestor_context(ctx, contexts, cache),
        "reads": dump_table_refs(ctx.reads, table_descriptions),
        "writes": dump_table_refs(ctx.writes, table_descriptions),
    }


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


# ── spec 054 폴백: 조각/병합 LLM 콜러블 팩토리 (배관은 여기, 오케스트레이션은 rules_fallback) ──


def _make_fragment_analyzer(prompts_dir, common: dict, cost_budget):
    """조각 rules LLM 콜러블 — 조각 본문 + 조상 조건 경로로 code_rules_examples 호출.

    조각은 함수 시그니처·공통 문맥(common)을 그대로 물려받고, code_body 만 조각 원문으로,
    ancestor_context 는 이 조각을 감싸는 제어문 조건 경로로 바꾼다(FR-021).
    """
    async def _analyze(frag):
        frag_values = {
            **common,
            "code_body": frag.text,
            "ancestor_context": "\n".join(frag.ancestor_path) if frag.ancestor_path else "(없음)",
            "child_business_logic": "(없음)",
        }
        return await run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="code_rules_examples",
            user_values=frag_values, response_model=RulesExamplesOutput, budget=cost_budget,
        )

    return _analyze


def _make_fragment_merger(prompts_dir, common: dict, signature: str, cost_budget):
    """병합 LLM 콜러블 — 조각별 결과 → 함수 하나의 규칙/예시(code_rules_merge, LLM 1회)."""
    async def _merge(fragment_outputs):
        merge_values = {
            **common,
            "signature": signature,
            "fragment_results": _format_fragment_results(fragment_outputs),
        }
        return await run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="code_rules_merge",
            user_values=merge_values, response_model=RulesExamplesOutput, budget=cost_budget,
        )

    return _merge


def _format_fragment_results(fragment_outputs) -> str:
    """조각별 RulesExamplesOutput → 병합 프롬프트 입력 텍스트(조각 경계 표시)."""
    parts: list[str] = []
    for idx, out in enumerate(fragment_outputs, start=1):
        parts.append(f"[조각 {idx}]")
        parts.append(out.model_dump_json())
    return "\n".join(parts) if parts else "(조각 결과 없음)"
