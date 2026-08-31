"""Parser call facts → one Analyzer resolution core → product ``Calls`` pairs.

호출은 Parser 구조 사실과 검증된 프로젝트 계약으로만 분류된다 (LLM·에이전트 0):
프로젝트 루틴 → internal / 검증된 프레임워크 제공 함수 → 제외 / 그 외 → external.
이름 전용 표준 함수 목록은 사용하지 않으며 external도 제품 CALLS 끝점을 만들지 않는다.
inactive는 occurrence ledger에 보존하고 조건부·expression·owner·wrapper target 증거 부족은
``call_resolution_ledger``에 명시한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from pipeline.analysis_ir.resolution.call_classifier import classify_call_sites
from shared.events.runtime_logger import log_info, log_warning
from graph.edges.calls import emit_calls
from pipeline.execution.graph_delta_stream import emit_message

_STEP = "step5.link_parser_calls"


@dataclass(frozen=True)
class CallClassificationContext:
    """Strategy-owned verified exclusions supplied to the shared call resolver."""

    framework_function_patterns: frozenset[str] = frozenset()
    wrappers: tuple[Any, ...] = ()


async def link_parser_calls_step(
    state: dict,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Resolve every Parser call occurrence exactly once. LLM calls: zero."""
    cfg = step_config(config)
    # call site 는 step2(load_ast) 가 antlr JSON 에서 수집한 것을 state 로 받는다.
    call_sites = state.get("call_sites", [])
    if not call_sites:
        log_info(_STEP, "done", call_sites=0, calls_edges=0, note="no call sites")
        return {"calls_edges": [], "call_resolution_ledger": []}

    started = time.monotonic()
    strategy = cfg.get("strategy")
    context_provider = getattr(strategy, "call_classification_context", None)
    if context_provider is None or not callable(context_provider):
        raise ValueError(
            "Parser call classification requires a strategy-owned context provider"
        )
    classification_context = context_provider(state)
    if not isinstance(classification_context, CallClassificationContext):
        raise TypeError(
            "strategy call classification context must be CallClassificationContext"
        )
    result = classify_call_sites(
        call_sites, state.get("code_nodes", []),
        framework_names=classification_context.framework_function_patterns,
        wrappers=classification_context.wrappers,
        macro_facts=state.get("macro_facts", []),
        preprocessing_contexts=state.get("preprocessing_contexts", []),
        callable_facts=state.get("callable_facts", []),
        parameter_facts=state.get("parameter_facts", []),
        binding_target_facts=state.get("binding_target_facts", []),
        scope_directive_facts=state.get("scope_directive_facts", []),
        import_refs=state.get("import_refs", []),
        evidence_sources=state.get("evidence_sources", []),
        evidence_completeness=state.get("evidence_completeness", []),
    )

    # 라이브 스트림은 임시 진행 표시일 뿐 제품 graph를 쓰지 않는다.
    # external/제외/미해결 occurrence는 장부에만 남고 제품 끝점을 만들지 않는다.
    stream = cfg.get("stream")
    await emit_calls(stream, result.edges)
    emit_message(stream, f"✅ 호출 이름 분류 완료 — {len(result.edges)}건")

    if result.wrapper_unresolved:
        log_warning(
            _STEP, "wrapper_unresolved",
            "wrapper target literal/symbol evidence 미지원 — source를 재파싱하지 않고 unresolved 보존",
            count=result.wrapper_unresolved,
        )
    if result.unresolved:
        log_warning(
            _STEP, "call_occurrences_unresolved",
            "canonical call occurrence 중 target/owner/presence가 확정되지 않은 항목",
            count=len(result.unresolved),
        )

    internal = result.resolution_counts.get("internal", 0)
    log_info(
        _STEP, "done",
        call_sites=len(call_sites),
        internal=internal,
        external=result.resolution_counts.get("external", 0),
        dynamic=result.resolution_counts.get("dynamic", 0),
        configuration_dependent=result.resolution_counts.get(
            "configuration_dependent", 0,
        ),
        ambiguous=result.resolution_counts.get("ambiguous", 0),
        unsupported=result.resolution_counts.get("unsupported", 0),
        inactive=result.resolution_counts.get("inactive", 0),
        dropped_stdlib=result.dropped_stdlib,
        dropped_framework=result.dropped_framework,
        dropped_macro=result.dropped_macro,
        preserved_inactive=result.preserved_inactive,
        unresolved_presence=result.unresolved_presence,
        unresolved_expression=result.unresolved_expression,
        missing_routine_owner_count=result.missing_routine_owner_count,
        unresolved_macro=result.unresolved_macro,
        wrapper_unresolved=result.wrapper_unresolved or None,
        accounted=result.accounted,
        calls_edges=len(result.edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "calls_edges": result.edges,
        "call_resolution_ledger": result.ledger,
    }
