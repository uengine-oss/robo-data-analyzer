"""step7_analyze_code — 모듈 사전분석 + Bottom-up DAG + 3 분할 fan-out.

순서:
0. 모듈 사전분석 (Framework 전용) — 모듈마다 LLM 1회로 헤더 주석 → 모듈 의도.
   결과 ``module_intent`` 는 아래 코드노드 분석의 3분할 공통 context 에 주입.
1. 호출 그래프 (CALLS + PARENT_OF) → ``find_call_cycles`` → cycle 식별 (Tarjan SCC).
2. ``bottom_up_rounds`` → leaf → root Round 별 정렬.
3. Round 별 병렬 분석:
   - 같은 Round 코드노드들 ``asyncio.as_completed`` + ``Semaphore`` 병렬 — 끝나는 순서대로
     즉시 Neo4j 영속화 + NDJSON emit (★ 라운드 단위 묶음 X — 라운드는 내부 concurrency
     제어 + 메타 정보일 뿐).
   - Round 간 순차 — 다음 Round 의 caller 가 이전 Round 의 callee summary 사용.
4. 각 코드노드 = ``analyze_code_three_split`` (3 분할 fan-out, RadixAttention 95%+).

cycle 내부 코드노드 → callee summary 없이 분석 (``is_in_cycle=True`` 메타).
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from shared.files.cache import SourceFileCache
from pipeline.execution.graph_delta_stream import emit_edges, emit_nodes
from pipeline.execution.pause_controller import NULL_PAUSE, PauseController
from pipeline.execution.step_config import step_config

from pipeline.steps.step7_analyze_code.analysis_order import bottom_up_rounds
# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from pipeline.steps.step7_analyze_code.call_cycles import find_call_cycles
from pipeline.steps.step7_analyze_code.module_pre_analysis import run_module_pre_analysis
from pipeline.steps.step7_analyze_code.rw_table_filter import flush_drop_summary, reset_drop_stats
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
    run_summary,
)
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from pipeline.steps.step7_analyze_code.example_mapping_builder import (
    ExampleMappingResult,
    build_example_mapping,
)
from graph.writers.example_writer import write_example_mapping
from llm.usage.cost_budget import BudgetExceededError
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.languages import ROUTINE_LABELS
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.code_node import update_summaries

_STEP = "step7.analyze_code"


async def _persist_and_emit_code(
    fn: CodeNode,
    neo: Optional[Neo4jClient],
    stream: Optional[NDJSONStream],
) -> ExampleMappingResult:
    """코드노드 분석 결과 1건 → Neo4j upsert + NDJSON 이벤트 + EM 결과 반환.

    ``code_text``(원문 슬라이스)는 step2 가 이미 채웠다 — 여기선 관여하지 않는다.
    EM 결과는 caller 가 round 단위 누적 후 step 종료 시 state 반영 (artifact 검증 정합).
    동시 호출 X (caller 의 라운드 루프가 순차 처리) — Neo4j 락 경합 없음.
    """
    em = build_example_mapping([fn])
    if neo is not None:
        await update_summaries(neo, [fn])
        await write_example_mapping(neo, em)
    if stream is not None:
        await _emit_code_graph(stream, fn, em)
    return em


async def _emit_code_graph(stream: NDJSONStream, fn: CodeNode, em: ExampleMappingResult) -> None:
    """CodeNode 노드 + EM 노드/엣지 → NDJSON node/relationship 이벤트.

    프론트가 그래프를 코드노드 단위로 누적 렌더하도록 — 한 코드노드의 모든 변경을 묶어 emit.
    """
    # 저장 writer와 같은 dataclass 목록을 공통 직렬화기에 전달해 wire 드리프트를 막는다.
    await emit_nodes(stream, [fn, *em.rules, *em.examples, *em.questions])
    await emit_edges(stream, [
        *em.has_rule,
        *em.has_example,
        *em.has_question,
        *em.affects_table,
    ])
