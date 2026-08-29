"""Finalize one verified CodeNode and emit its provisional progress event.

검증된 Rule에서 Example을 결정론적으로 투영하고 상태·진행 이벤트를 합친다. 이 경계는
LLM을 호출하거나 의미를 다시 생성하지 않는다.
"""
from __future__ import annotations

from typing import Optional

from pipeline.execution.graph_delta_stream import emit_edges, emit_nodes

# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step7_analyze_code.example_mapping_builder import (
    ExampleMappingResult,
    build_example_mapping,
)
from shared.events.ndjson_event_stream import NDJSONStream

_STEP = "step7.analyze_code"


async def finalize_and_emit_code_result(
    fn: CodeNode,
    stream: Optional[NDJSONStream],
) -> ExampleMappingResult:
    """코드노드 분석 결과 1건 → state용 결과와 NDJSON 진행 이벤트 반환.

    ``code_text``(원문 슬라이스)는 step2 가 이미 채웠다 — 여기선 관여하지 않는다.
    EM 결과는 caller 가 round 단위 누적 후 step 종료 시 state 반영 (artifact 검증 정합).
    caller가 완료 결과를 하나씩 수거하므로 Rule/Example 누적 순서가 명시적이다.
    """
    em = build_example_mapping([fn])
    if stream is not None:
        await _emit_code_graph(stream, fn, em)
    return em


async def _emit_code_graph(stream: NDJSONStream, fn: CodeNode, em: ExampleMappingResult) -> None:
    """CodeNode 노드 + EM 노드/엣지 → NDJSON node/relationship 이벤트.

    프론트가 그래프를 코드노드 단위로 누적 렌더하도록 — 한 코드노드의 모든 변경을 묶어 emit.
    """
    # 저장 writer와 같은 dataclass 목록을 공통 직렬화기에 전달해 wire 드리프트를 막는다.
    await emit_nodes(stream, [fn, *em.rules, *em.examples])
    await emit_edges(stream, [
        *em.has_rule,
        *em.has_example,
        *em.affects_table,
    ])
