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
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline.steps.step7_analyze_code.range_body import ancestor_context, body_for, raw_body_for
from pipeline.steps.step7_analyze_code.rule_quality import check_rule_quality
from pipeline.steps.step7_analyze_code.rw_table_filter import constrain_tables_to_rw
from pipeline.steps.step7_analyze_code.table_prompt_context import dump_table_refs
from pipeline.steps.step7_analyze_code.summary_quality import (
    normalize_searchable_text,
)
from pipeline.models.analysis_context import AnalysisContext, dump_external_evidence, dump_referenced_symbols
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.grounding import check_grounding
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import NoSupportedSourceError, coerce_stereotype, lang_of
from llm.usage.cost_budget import BudgetExceededError, CostBudget
from pipeline.models.code_analysis_output import (
    RulesExamplesOutput,
    SummaryOutput,
    TableInsightsOutput,
)
from llm.runners.yaml_llm_runner import run_yaml_rule
from pipeline.steps.step7_analyze_code.domain_terms import normalize_domain_terms
from shared.events.runtime_logger import log_error, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from pipeline.steps.step7_analyze_code.control_prompt import control_index

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
    on_summary_ready: Optional[Callable[[str], None]] = None,
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
        on_summary_ready: summary 확정(경계 검사 후) 직후 호출되는 스케줄러 신호 —
            CALLS 의존 caller 를 #2/#3 완료를 기다리지 않고 조기 투입하게 한다.

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
            # 같은 의미 필드를 축소 prompt나 교정 LLM으로 다시 만들지 않는다. 최초 생성
            # 실패를 명시적으로 남기고 이 노드는 미분석으로 처리한다.
            log_error(_STEP, "summary_failed", e, code_id=code_id)
            return None

    # "의미 없음"을 LLM이 자의적으로 판정해 검색에서 누락시키지 않는다. 의미를 기계가
    # 복사하거나 추론하지 않고, 두 필드의 비어 있지 않음만 확인한다.
    summary_out.summary, summary_out.search_text = normalize_searchable_text(
        summary_out.summary, summary_out.search_text
    )
    # summary 최종값을 여기서 즉시 공개 — caller 의 callee_summaries 는 이 값 하나만 읽는다.
    # #2/#3(rules·insights) 완료를 기다리게 하면 CALLS 의존 caller 가 그만큼 늦게 풀린다
    # (실측: rules p95 85초). 값은 아래 fan-in 시점과 동일하므로 산출물 불변이다.
    ctx.summary = summary_out.summary
    # ★ #1 에서만 나오는 다섯 필드를 여기서 노드에 확정한다 (spec 096). 종전엔 같은 값을
    #   아래 fan-in 지점에서 넣었다 — #2·#3 은 이 필드들을 건드리지 않으므로 값은 같고
    #   시점만 앞당겨진다. 이 시점이 곧 후처리(모듈요약·컴포넌트명·용어사전)의 의존
    #   해제 시점이다. 노드 완료를 기다리게 하면 그 차이만큼 GPU 슬롯이 빈다.
    _apply_summary_fields(
        fn,
        summary_out,
        common["raw_code_body"],
        common["numbered_raw_code_body"],
        strategy,
    )
    if on_summary_ready is not None:
        on_summary_ready(code_id)

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

    # 품질 검사는 관측만 한다. 결과를 별도 LLM으로 재분석·병합하거나 결정론적으로
    # 고쳐 쓰지 않는다. 최초 rules 생성 결과가 단일 진실이다.
    quality_flag: Optional[str] = None
    quality = (
        check_rule_quality(rules_out, common["raw_code_body"])
        if rules_out is not None else None
    )
    if quality is not None and quality.is_anomaly:
        quality_flag = "rules_quality_anomaly"
        log_warning(
            _STEP,
            "rules_quality_observed",
            "규칙 품질 이상을 관측했지만 산출물은 재작성하지 않음",
            code_id=code_id,
            reasons=quality.reasons,
        )

    # partial = 최초 분할 호출이 결과를 못 낸 경우. 다른 LLM으로 복구하지 않는다.
    partial_parts = tuple(
        k for k in ("rules", "insights")
        if k in tasks and isinstance(done[k], BaseException)
    )

    # spec 027: LLM 귀속 테이블을 결정론 reads/writes 로 제한 (callee 함수명 오귀속 차단).
    constrain_tables_to_rw(
        code_id, ctx.reads, ctx.writes, insights_out, rules_out,
        allowed_columns_by_table=ctx.extra.get("ddl_columns_by_table"),
        table_op=ctx.extra.get("table_op"),
    )

    # spec 101: 근거성 게이트 — 측정 전용. 산출물을 바꾸지 않고 findings 만 남긴다.
    grounding_findings = _measure_grounding(
        fn, ctx, common["raw_code_body"], rules_out
    )

    # fan-in 머지 → CodeNode 에 적용 (#1 유래 다섯 필드는 Phase 1 에서 이미 확정됐다).
    fn.extra["table_insights"] = (
        [t.model_dump() for t in insights_out.table_insights] if insights_out else []
    )
    fn.extra["rules"] = [r.model_dump() for r in rules_out.rules] if rules_out else []   # examples 는 각 rule 하위 중첩
    fn.extra["questions"] = [q.model_dump() for q in rules_out.questions] if rules_out else []
    if partial_parts:
        fn.extra["partial"] = partial_parts
    if quality_flag:
        fn.extra["rules_quality_flag"] = quality_flag
    if grounding_findings:
        fn.extra["grounding_findings"] = [f.as_dict() for f in grounding_findings]
        # 측정값은 **보여야** 측정이다. 계산만 하고 버리면 죽은 데이터다.
        log_warning(
            _STEP, "guardrail_observed",
            "근거성 가드레일이 규칙·사례와 근거 분기의 불일치를 관측 "
            "(측정 전용 — 산출물 변경 없음)",
            code_id=fn.id,
            guardrail="grounding",
            counts=dict(Counter(f.check for f in grounding_findings)),
            findings=[f.as_dict() for f in grounding_findings],
        )

    # ctx 에 나머지 분석 결과 부착 — 부모(PARENT_OF)의 #3 분할 입력.
    # (summary 는 위 Phase 1 종료 시점에 이미 공개됨 — CALLS 의존 조기 해제.)
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn


def _measure_grounding(
    fn: CodeNode, ctx: AnalysisContext, code_body: str, rules_out: Optional[RulesExamplesOutput]
) -> list:
    """근거성 게이트를 **측정 전용**으로 돌린다 (spec 101).

    게이트 실패가 분석을 죽이면 안 된다 — 측정은 부가 관측이고 산출물은 이미 확정됐다.
    다만 조용히 삼키지도 않는다(절대원칙 #3): 실패는 경고로 표면화하고 findings 만 비운다.
    """
    if rules_out is None or not rules_out.rules:
        return []
    try:
        inventory = build_inventory(
            code_body, ctx.extra.get("control_subtree"), lang_of(Path(fn.file_path).suffix),
            declared_values=[s.declared_value for s in ctx.referenced_symbols],
            line_offset=max(fn.start_line - 1, 0),
        )
        return check_grounding(rules_out, inventory)
    except NoSupportedSourceError:
        return []          # 게이트 밖 언어 — 관측 대상이 아니다
    except Exception as error:  # noqa: BLE001 — 관측 실패가 분석을 죽이지 않게
        log_warning(
            _STEP, "grounding_gate_failed",
            "근거성 게이트 측정 실패 — 분석 산출물은 영향 없음",
            code_id=fn.id, error=repr(error),
        )
        return []


def _apply_summary_fields(
    fn: CodeNode,
    summary_out: SummaryOutput,
    code_body: str,
    numbered_code_body: str,
    strategy: Any,
) -> None:
    """#1 summary 에서만 나오는 코드노드 필드를 확정한다 (spec 096).

    #2(insights)·#3(rules)는 이 다섯 필드를 읽지도 쓰지도 않는다. 그래서 노드 완료가
    아니라 #1 확정 시점에 넣어도 값이 같고, 후처리는 그만큼 일찍 시작할 수 있다.
    stereotype 은 ontology_roles.yaml 풀과 검증 (없으면 빈 문자열로 강제).
    """
    fn.summary = summary_out.summary
    fn.stereotype = coerce_stereotype(summary_out.stereotype or "", strategy.name) or None
    fn.logical_name = summary_out.logical_name or None
    fn.search_text = summary_out.search_text or None
    fn.domain_terms = normalize_domain_terms(
        summary_out.domain_terms,
        source_text=code_body,
        numbered_source_text=numbered_code_body,
        code_id=fn.id,
        component=_STEP,
    )


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
    excerpt = cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line)
    raw_body = raw_body_for(ctx, contexts, cache)
    prompt_body = body_for(ctx, contexts, cache)
    lang = lang_of(Path(fn.file_path).suffix)
    return {
        "id": fn.id,
        "signature": fn.signature,
        "code_body": prompt_body,
        "raw_code_body": raw_body,
        "numbered_raw_code_body": excerpt.numbered_text,
        "source_file": fn.file_path,
        "source_range": f"{ctx.start_line}-{ctx.end_line}",
        "source_status": excerpt.status,
        "coordinate_system": excerpt.coordinate_system,
        "codebase_notes": codebase_notes or "(없음)",
        "module_intent": module_intents.get(fn.owner_id) or "(없음)",
        "referenced_symbols": dump_referenced_symbols(ctx.referenced_symbols),
        "external_evidence": dump_external_evidence(ctx.external_evidence),
        "callee_summaries": _format_callee_summaries(ctx, contexts),
        "ancestor_context": ancestor_context(ctx, contexts, cache),
        "reads": dump_table_refs(
            ctx.reads, table_descriptions, direct_source=raw_body, lang=lang
        ),
        "writes": dump_table_refs(
            ctx.writes, table_descriptions, direct_source=raw_body, lang=lang
        ),
        "table_ops": json.dumps(
            ctx.extra.get("table_op") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        # spec 102: 규칙이 고를 수 있는 근거 분기 후보. 분할 3 템플릿만 참조한다.
        "branch_index": _branch_index(fn, ctx, cache),
    }


def _branch_index(fn: CodeNode, ctx: AnalysisContext, cache: SourceFileCache) -> str:
    """``줄번호: 조건식`` 목록. 게이트가 쓰는 분기 대장과 **같은 정의처**에서 만든다."""
    return control_index(fn, ctx, cache)


def _format_callee_summaries(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> str:
    """``callee_id`` → callee.summary 텍스트 포맷.

    Bottom-up DAG 진행 중이면 callee summary 가 ``contexts`` 에 있음.
    아직 없으면(cycle 내부·분석 실패) 다른 의미 필드로 대신하지 않고 목록에서 제외한다.
    """
    if not ctx.callees:
        return "(없음)"
    items = []
    for callee_id in ctx.callees:
        c = contexts.get(callee_id)
        if c is None:
            continue
        text = (c.summary or "").strip()
        if text:
            items.append({
                "callee_id": callee_id,
                "scope": "callee-interface-only",
                "summary": text,
            })
    return (
        json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if items else "(없음)"
    )


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
