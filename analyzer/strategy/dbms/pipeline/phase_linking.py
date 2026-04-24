"""Phase 2: 분석 전 Linking (결정론)

AST 기반 CALLS + REFERENCES 관계를 Neo4j 에 사전 구축.
LLM 호출 전에 수행되므로, node_analysis 단계에서 이 정보를 활용 가능.

- call_linker: CALL 노드 + initializer_contains_method_call 플래그 → CALLS
- variable_matcher: VARIABLE (DECLARE 안) + PACKAGE_VARIABLE → REFERENCES
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, List

from analyzer.strategy.dbms.common.node_models import FileAnalysisContext, FileStatus
from analyzer.strategy.dbms.linking.call_linker import build_calls_queries
from analyzer.strategy.dbms.linking.variable_matcher import build_references_queries
from analyzer.common.graph_writer import run_graph_queries
from util.stream_emitter import emit_data, emit_message
from util.logger import log_process

logger = logging.getLogger(__name__)


async def run_phase_linking(
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """Phase 1 직후, Phase 2(LLM) 이전에 결정론 링킹 수행."""
    targets = [c for c in contexts if c.status == FileStatus.PH1_OK and c.processor and c.processor._nodes]
    if not targets:
        return

    client = ctx.client
    total_call_edges = 0
    total_ref_edges = 0

    for file_ctx in targets:
        proc = file_ctx.processor
        nodes = proc._nodes or []
        linking_queries = (
            build_calls_queries(nodes, proc.full_directory, proc.file_name)
            + build_references_queries(nodes, proc.full_directory, proc.file_name)
        )
        if not linking_queries:
            continue

        try:
            async for batch in run_graph_queries(client, linking_queries):
                yield emit_data(graph=batch)
        except Exception as e:
            log_process("PHASE:LINK", "ERROR", f"링킹 반영 실패 ({file_ctx.file_name}): {e}", logging.ERROR, e)
            continue

        # 통계 (대략)
        calls_cnt = sum(1 for q in linking_queries if "CALLS" in q.get("query", ""))
        refs_cnt = sum(1 for q in linking_queries if "REFERENCES" in q.get("query", ""))
        total_call_edges += calls_cnt
        total_ref_edges += refs_cnt

    yield emit_message(f"   ✓ 결정론 링킹 완료: CALLS {total_call_edges}건, REFERENCES {total_ref_edges}건")
    log_process("PHASE:LINK", "DONE", f"CALLS={total_call_edges} REFERENCES={total_ref_edges}")
