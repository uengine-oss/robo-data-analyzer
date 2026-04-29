"""Phase 2: 분석 전 Linking (결정론)

AST 기반 CALLS + REFERENCES + INIT_BY 관계를 Neo4j 에 사전 구축.
LLM 호출 전에 수행되므로, node_analysis 단계에서 이 정보를 활용 가능.

- call_linker (DBMS 전용 정규식): CALLS 엣지
- common.variable_linker: REFERENCES 엣지 (StatementNode → canonical dict 변환)
- common.init_by_linker: INIT_BY 엣지 (DECLARE := 만, 단순 식별자 source)
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.common.pipeline.init_by_linker import find_init_by_sources
from analyzer.common.pipeline.variable_linker import WILDCARD_SCOPE, find_variable_references
from analyzer.strategy.dbms.common.cypher.graph_queries import (
    build_init_by_query,
    build_references_query,
)
from analyzer.strategy.dbms.common.domain.analysis_constants import PROCEDURE_TYPES
from analyzer.strategy.dbms.common.models.node_models import FileAnalysisContext, FileStatus, StatementNode
from analyzer.strategy.dbms.linking.call_linker import build_calls_queries
from util.logger import log_process
from util.stream_emitter import emit_message

logger = logging.getLogger(__name__)

# 파일 전체 scope (wildcard) 로 매칭되는 변수성 노드
_PACKAGE_VAR_TYPES = {"PACKAGE_VARIABLE", "CONSTANT_FIELD"}
# REFERENCES / INIT_BY name_index 등록 대상 — 모든 변수성 식별자.
# PARAMETER 도 자식 식별자라 `tagNo := tagsn` 같은 변수↔파라미터 INIT_BY 매칭에 필요.
_VAR_LIKE_TYPES = {"VARIABLE", "PACKAGE_VARIABLE", "CONSTANT_FIELD", "PARAMETER"}


# ═══════════════════════════════════════════════════════════════════════════
# StatementNode → canonical dict 변환
# ═══════════════════════════════════════════════════════════════════════════


def _enclosing_proc(node: StatementNode) -> Optional[StatementNode]:
    """가장 가까운 PROCEDURE/FUNCTION/TRIGGER 조상 반환."""
    current = node.parent
    while current is not None:
        if current.node_type in PROCEDURE_TYPES:
            return current
        current = current.parent
    return None


def _scope_id(node: StatementNode) -> Any:
    """node 의 enclosing 프로시저 식별자. 없으면 WILDCARD."""
    proc = _enclosing_proc(node)
    return id(proc) if proc is not None else WILDCARD_SCOPE


def _build_reference_targets_sources(nodes: List[StatementNode]):
    """REFERENCES 매칭의 target/source 후보 분류.

    target 분류:
      - VARIABLE / PARAMETER → enclosing PROCEDURE/FUNCTION 의 scope (지역)
      - PACKAGE_VARIABLE / CONSTANT_FIELD → 파일 전체 (wildcard scope)
    source: 분석 가능한 모든 노드 (코드 본문에서 변수명 토큰 등장 여부 매칭).
    """
    targets: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    for n in nodes:
        if not n.node_type:
            continue
        name = (n.name or "").strip()
        if not name:
            pass  # 식별자 없는 노드는 target 대상 아님
        elif n.node_type in ("VARIABLE", "PARAMETER"):
            # 지역 scope — 자기 함수 본문 안에서만 매칭
            targets.append({
                "id": id(n), "name": name,
                "scope_id": _scope_id(n),
                "node": n,
            })
        elif n.node_type in _PACKAGE_VAR_TYPES:
            # 파일 전체 scope — 어느 함수에서 참조하든 매칭
            targets.append({
                "id": id(n), "name": name,
                "scope_id": WILDCARD_SCOPE,
                "node": n,
            })
        if n.analyzable and n.code:
            sources.append({
                "id": id(n),
                "scope_id": _scope_id(n),
                "code": n.code,
                "node": n,
            })
    return targets, sources


def _build_init_by_inputs(nodes: List[StatementNode]):
    """VARIABLE 중 init_value 가진 노드 + name_index 구성."""
    variables: List[Dict[str, Any]] = []
    name_index: Dict[str, List[Dict[str, Any]]] = {}
    for n in nodes:
        if n.node_type not in _VAR_LIKE_TYPES:
            continue
        name = (n.name or "").strip()
        if not name:
            continue
        entry = {"id": id(n), "name": name, "node": n}
        name_index.setdefault(name.lower(), []).append(entry)
        if n.node_type == "VARIABLE" and n.init_value:
            variables.append({
                "id": id(n), "name": name,
                "init_value": n.init_value,
                "node": n,
            })
    return variables, name_index


# ═══════════════════════════════════════════════════════════════════════════
# 파일별 링킹 쿼리 빌드
# ═══════════════════════════════════════════════════════════════════════════


def _build_reference_queries(nodes, full_directory, file_name) -> List[Dict[str, Any]]:
    targets, sources = _build_reference_targets_sources(nodes)
    pairs = find_variable_references(targets=targets, sources=sources)
    queries: List[Dict[str, Any]] = []
    for t, s in pairs:
        sn: StatementNode = s["node"]
        queries.append(build_references_query(
            src_node_type=sn.node_type, src_line=sn.start_line,
            var_name=t["name"],
            full_directory=full_directory, file_name=file_name,
        ))
    return queries


def _build_init_by_queries(nodes, full_directory, file_name) -> List[Dict[str, Any]]:
    variables, name_index = _build_init_by_inputs(nodes)
    pairs = find_init_by_sources(variables=variables, name_index=name_index)
    queries: List[Dict[str, Any]] = []
    for v, src in pairs:
        sn: StatementNode = v["node"]
        queries.append(build_init_by_query(
            src_name=v["name"], src_line=sn.start_line,
            target_name=src["name"],
            full_directory=full_directory, file_name=file_name,
        ))
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 메인 진입점
# ═══════════════════════════════════════════════════════════════════════════


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
    total_init_by_edges = 0

    for file_ctx in targets:
        proc = file_ctx.processor
        nodes = proc._nodes or []
        ref_queries = _build_reference_queries(nodes, proc.full_directory, proc.file_name)
        init_by_queries = _build_init_by_queries(nodes, proc.full_directory, proc.file_name)
        linking_queries = (
            build_calls_queries(nodes, proc.full_directory, proc.file_name)
            + ref_queries
            + init_by_queries
        )
        if not linking_queries:
            continue

        try:
            async for chunk in write_to_graph(client, linking_queries, f"DBMS_PHASE2:LINKING:{file_ctx.file_name}"):
                yield chunk
        except Exception as e:  # noqa: SILENT_FAIL — 한 파일 링킹 실패가 다른 파일 진행을 막지 않도록 errlog 후 계속
            log_process("PHASE:LINK", "ERROR", f"링킹 반영 실패 ({file_ctx.file_name}): {e}", logging.ERROR, e)
            continue

        # 통계 (대략)
        calls_cnt = sum(1 for q in linking_queries if "CALLS" in q.get("query", ""))
        total_call_edges += calls_cnt
        total_ref_edges += len(ref_queries)
        total_init_by_edges += len(init_by_queries)

    yield emit_message(
        f"   ✓ 결정론 링킹 완료: CALLS {total_call_edges}건, REFERENCES {total_ref_edges}건, INIT_BY {total_init_by_edges}건"
    )
    log_process(
        "PHASE:LINK", "DONE",
        f"CALLS={total_call_edges} REFERENCES={total_ref_edges} INIT_BY={total_init_by_edges}",
    )
