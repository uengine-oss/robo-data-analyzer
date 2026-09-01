"""Finalize one verified CodeNode and emit its provisional progress event."""
from __future__ import annotations

from typing import Optional

from pipeline.execution.graph_delta_stream import emit_edges, emit_nodes

# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from graph.schema.graph_nodes import CodeNode
from pipeline.meaning.rule.rule_graph import (
    RuleGraphResult,
    build_rule_graph,
)
from shared.events.ndjson_event_stream import NDJSONStream

_STEP = "step7.analyze_code"


async def finalize_and_emit_code_result(
    fn: CodeNode,
    stream: Optional[NDJSONStream],
) -> RuleGraphResult:
    """코드노드 분석 결과 1건 → state용 결과와 NDJSON 진행 이벤트 반환.

    ``code_text``(원문 슬라이스)는 step2 가 이미 채웠다 — 여기선 관여하지 않는다.
    Rule 결과는 caller가 round 단위 누적 후 step 종료 시 state에 반영한다.
    """
    rule_graph = build_rule_graph([fn])
    if stream is not None:
        await _emit_code_graph(stream, fn, rule_graph)
    return rule_graph


async def _emit_code_graph(
    stream: NDJSONStream,
    fn: CodeNode,
    rule_graph: RuleGraphResult,
) -> None:
    """CodeNode와 Rule graph를 한 묶음의 NDJSON 이벤트로 보낸다.

    프론트가 그래프를 코드노드 단위로 누적 렌더하도록 — 한 코드노드의 모든 변경을 묶어 emit.
    """
    # 저장 writer와 같은 dataclass 목록을 공통 직렬화기에 전달해 wire 드리프트를 막는다.
    await emit_nodes(stream, [fn, *rule_graph.rules])
    await emit_edges(stream, [
        *rule_graph.has_rule,
        *rule_graph.table_edges,
    ])
