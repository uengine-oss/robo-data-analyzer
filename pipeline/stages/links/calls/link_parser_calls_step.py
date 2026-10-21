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

from graph.schema.graph_nodes import Variable
from pipeline.parsing.resolution.call_classifier import classify_call_sites
from shared.events.runtime_logger import log_info, log_warning
from graph.edges.call_edges import emit_calls
from pipeline.execution.graph_delta_stream import emit_message

_STEP = "step5.link_parser_calls"


def _receiver_declarations(variables: list[Any], constants: list[Any]) -> list[Any]:
    """필드 선언을 **빠짐없이** 수신자 바인딩에 넘긴다.

    ⚠ 2026-10-21 실측(ISS-59). ``private final EmployeeDao employeeDao = …`` 는
    ``final`` 때문에 ``Constant``(라벨 ``CONSTANT_FIELD``)로 모델링된다. 그런데
    수신자 타입을 푸는 ``_receiver_owner`` 는 ``variables`` 만 받고 라벨이
    ``FIELD``/``MEMBER`` 인 것만 본다 — **상수 필드는 쳐다보지도 않는다.**

    그래서 ``employeeDao.selectByEmpNo(conn, empNo)`` 가 전부
    ``java_receiver_type_binding_environment_required`` 로 떨어졌다:
    호출 539건 중 **387건 미해결, 그 중 266건이 이 사유** 였다. 결과로
    서비스→DAO CALLS 가 **한 건도 없고**(남은 85건 중 69건이 `HrException`
    생성자다), 규칙을 가진 메서드 49개 중 테이블에 닿는 것이 **6개** 뿐이었다.
    ES 의 Aggregate 출처에 테이블이 안 나오는 **진짜 뿌리**가 여기다.

    **추측이 아니다** — 선언 타입이 원문에 적혀 있고 그래프에도 들어 있다
    (CONSTANT_FIELD 43개 중 **42개**가 선언 타입을 갖고 있다). 그 선언을
    필드 선언으로 되돌려 넘길 뿐, 바인딩 규칙 자체는 건드리지 않는다.

    변환은 **이 단계 안에서만** 산다 — 만든 레코드는 분류기로만 가고
    제품 graph 에는 어떤 노드도 만들지 않는다.
    """
    promoted: list[Any] = []
    for constant in constants or []:
        if getattr(constant, "label", "") != "CONSTANT_FIELD":
            continue
        declared_type = (getattr(constant, "var_type", "") or "").strip()
        if not declared_type:
            continue
        promoted.append(Variable(
            id=constant.id,
            owner_id=constant.owner_id,
            name=constant.name,
            file_path=constant.file_path,
            start_line=constant.start_line,
            end_line=constant.end_line,
            label="FIELD",
            var_type=declared_type,
        ))
    return [*(variables or []), *promoted]


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
        variables=_receiver_declarations(
            state.get("variables", []), state.get("constants", []),
        ),
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
