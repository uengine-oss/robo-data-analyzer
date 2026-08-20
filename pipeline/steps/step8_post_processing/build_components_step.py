"""build_components_step — 코드노드 호출 군집을 FUNCTION 노드 속성으로 부여.

robo 가 이미 만든 호출그래프(state 의 `code_nodes`/`calls_edges`)만으로:
1. **나누기(결정론, LLM 0):** 내부 호출 그래프에 Leiden 군집화 → 코드노드들을 군집(부품)으로.
2. **표시 상태(결정론, LLM 0):** canonical member slot ID를 exact-once 참조하고
   source-owned 이름이 없음을 explicit partial frame으로 보존.
3. **적재:** 각 멤버 FUNCTION 노드에 `component_id`·`component_name` 속성을 SET
   (health_metrics 와 동일한 배치 UNWIND SET 패턴).

★ 그룹을 합성 노드(`:COMPONENT`)로 materialize 하지 않는다 — 멤버십은 **`FUNCTION.component_id`
  속성이 단일 진실**이고, 이름·그룹간 관계는 전부 거기서 파생한다:
    - 시각화(바운디드 컨텍스트 영역)는 `component_id` 로 뷰에서 hull 오버레이.
    - 그룹↔그룹 호출은 저장하지 않고 `CALLS` 엣지 + `component_id` 로 온디맨드 GROUP BY.
  → 그래프에 합성 노드/엣지 0 → 도달성·중심성·임팩트 분석 왜곡 없음.
  Leiden(leidenalg/igraph)은 군집화 시점에 lazy import (미설치면 명시 에러).
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.steps.step5_linking.link_calls.call_linker import routine_call_pairs
from pipeline.models.aggregate_semantic_frame import (
    build_aggregate_semantic_plan,
    project_aggregate_frame,
    sealed_summary_frame_payload,
)
from pipeline.models.semantic_frame import SemanticFrame
from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.execution.quality_finding_log import record_analysis_findings
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import step_processing_scope
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.execution.analysis_handoff import AnalysisHandoffAborted, handoff_of
from pipeline.execution.step_config import step_config
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip


_STEP = "step8.build_components"

# 군집/이름 파라미터 (튜닝 가능).
MIN_COMPONENT_SIZE = 2     # 멤버 1개(고립 코드노드)는 군집으로 X (노이즈 차단)
LEIDEN_SEED = 42           # 결정론 — 같은 그래프 → 같은 군집

# 멤버 FUNCTION 노드에 군집 멤버십을 속성으로 SET (합성 노드 없음). health_metrics 와 동일 패턴.
_SET_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET node.component_id = item.component_id,
    node.component_name = item.component_name
"""


async def build_components_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """호출그래프 → Leiden 군집 → degraded display → FUNCTION membership SET."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    # 군집은 결정론 호출그래프(step5)로 만들고 canonical summary handoff는
    # exact member slot identity를 component frame에 연결하는 데만 사용한다.
    handoff = handoff_of(cfg)
    try:
        await handoff.summaries()
    except AnalysisHandoffAborted:
        log_handoff_skip(_STEP, "컴포넌트 군집", pause)
        return {}

    code_nodes: list[CodeNode] = handoff.merge(state.get("code_nodes", []))
    calls: list[Calls] = state.get("calls_edges", [])
    if not code_nodes:
        log_info(_STEP, "skip", reason="code_nodes 0")
        return {}

    started = time.monotonic()
    # 1. 나누기 (결정론)
    clusters = _cluster(code_nodes, calls)         # [(component_id, [member_code_id...])]
    if not clusters:
        log_info(_STEP, "skip", reason="군집 없음 (내부 호출 부족)")
        return {}
    log_info(_STEP, "start", code_nodes=len(code_nodes), components=len(clusters))

    # 2. source-grounded 이름 producer가 없으므로 membership과 explicit partial만 보존한다.
    fn_by_id = {f.id: f for f in code_nodes}

    def _project_one(component_id: str, members: list[str]) -> SemanticFrame | None:
        member_fns = [fn_by_id[m] for m in members if m in fn_by_id]
        # This step is released by the summary handoff.  Always consume that sealed
        # snapshot; ``semantic_frame`` may already contain Rule/table slots for faster
        # siblings and would make input depend on completion timing.
        member_frames = [
            SemanticFrame.model_validate(
                sealed_summary_frame_payload(fn)
            )
            for fn in member_fns
            if sealed_summary_frame_payload(fn)
        ]
        if len(member_frames) != len(member_fns):
            missing = [
                fn.id for fn in member_fns
                if not sealed_summary_frame_payload(fn)
            ]
            record_analysis_findings([AnalysisFinding(
                concern="component_name",
                status="unavailable",
                target_id=component_id,
                reasons=(f"canonical member semantic frame unavailable: {missing}",),
                evidence_rule="deterministic_component_projection",
            )])
            log_warning(
                _STEP, "quality_finding",
                "component semantic frame omitted because a canonical member frame is unavailable",
                component_id=component_id, missing_count=len(missing),
            )
            return None
        profile, specs = build_aggregate_semantic_plan(
            target_id=component_id,
            target_type="COMPONENT",
            owner_id=component_id,
            file="(derived-call-graph)",
            start_line=0,
            end_line=0,
            family="COMPONENT",
            member_frames=member_frames,
            include_responsibility=False,
            member_roles=("RESPONSIBILITY",),
        )
        frame = project_aggregate_frame(
            profile,
            specs,
            member_frames,
            member_roles=("RESPONSIBILITY",),
            physical_name=component_id,
        )
        record_analysis_findings([AnalysisFinding(
            concern="component_name",
            status="unverified",
            target_id=component_id,
            reasons=(
                "source-owned component display meaning unavailable; "
                "deterministic membership ID preserved only",
            ),
            evidence_rule="deterministic_component_projection",
        )])
        return frame

    async def _run_projection() -> list[SemanticFrame | None]:
        projected: list[SemanticFrame | None] = []
        for component_id, members in clusters:
            await pause.gate()
            projected.append(_project_one(component_id, members))
        return projected

    with step_processing_scope(_STEP, stream):
        results = await pause.run_restartable_during_pause(_run_projection)
    names = {
        cid: None
        for cid, _members in clusters
    }
    component_frames = {
        cid: result.model_dump(mode="json", by_alias=True)
        for (cid, _members), result in zip(clusters, results)
        if result is not None
    }

    # 3. 적재 — 각 멤버 FUNCTION 노드에 component_id/name 속성 SET (합성 노드/엣지 없음).
    items = [
        {"id": m, "component_id": cid, "component_name": names[cid]}
        for cid, members in clusters
        for m in members
    ]
    if neo is not None and items:
        await neo.run_batch_unwind(_SET_QUERY, items)
    elif neo is None:
        log_warning(_STEP, "config_missing", "neo_client 누락 — component 속성 SET skip (계산만)")

    log_info(
        _STEP, "done",
        components=len(clusters), members_tagged=len(items),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    # 합성 노드/엣지를 state 에 남기지 않는다 — 멤버십은 FUNCTION.component_id 가 단일 진실.
    return {"component_semantic_frames": component_frames}


def _cluster(code_nodes: list[CodeNode], calls: list[Calls]) -> list[tuple[str, list[str]]]:
    """내부 호출 그래프에 Leiden → [(component_id, [member_id...])]. 결정론(seed 고정).

    Leiden(leidenalg/igraph)은 여기서 lazy import — 미설치면 명시 에러(설치 안내).
    component_id = 멤버 정렬 첫 code_id 기반 → 같은 그래프 재분석 시 안정 키.
    """
    # Reusable primitive/system utilities are cross-cutting graph bridges, not
    # business component members.  Letting them participate in an undirected
    # Leiden graph merges otherwise unrelated domains through string/time
    # helpers.  Keep their CALLS edges in the graph, but exclude them only from
    # the derived component partition.
    ids = [
        f.id for f in code_nodes
        if str(getattr(f, "stereotype", "") or "") not in {"Util", "Utility"}
    ]
    eligible = set(ids)
    # CALL→대상 저장을 루틴쌍으로 해소(단일 해소기). 군집에 자기루프는 무의미 → 제외.
    edges = [
        (a, b)
        for a, b in routine_call_pairs(code_nodes, calls)
        if a != b and a in eligible and b in eligible
    ]
    if not edges:
        return []

    try:
        import igraph as ig
        import leidenalg
    except ImportError as e:  # noqa: BLE001 — 명시 안내 후 전파
        raise RuntimeError(
            "leidenalg/igraph 미설치 — `pip install leidenalg igraph` 필요 "
            "(requirements.txt 반영됨)"
        ) from e

    g = ig.Graph(directed=False)
    g.add_vertices(ids)                 # 정점 이름 = code_id (index i ↔ ids[i])
    g.add_edges(edges)                  # 이름 기반 엣지 (중복은 무방 — 가중 동등)

    partition = leidenalg.find_partition(
        g, leidenalg.ModularityVertexPartition, seed=LEIDEN_SEED,
    )

    out: list[tuple[str, list[str]]] = []
    for community in partition:
        members = sorted(ids[i] for i in community)
        if len(members) < MIN_COMPONENT_SIZE:
            continue                    # 고립/초소 군집 = 노이즈 → 군집 X
        out.append((f"COMPONENT:{members[0]}", members))
    return out
