"""step6_analyze_code — 모듈 사전분석 + Bottom-up DAG + 3 분할 fan-out.

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

from source.file_cache import SourceFileCache
from pipeline.execution.graph_delta_stream import emit_edges, emit_nodes
from pipeline.execution.pause_controller import NULL_PAUSE, PauseController
from pipeline.execution.step_config import step_config

from pipeline.steps.step6_analyze_code.analysis_order import bottom_up_rounds
# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from pipeline.steps.step6_analyze_code.call_cycles import find_call_cycles
from pipeline.steps.step6_analyze_code.module_pre_analysis import run_module_pre_analysis
from pipeline.steps.step6_analyze_code.rw_table_filter import flush_drop_summary, reset_drop_stats
from pipeline.steps.step6_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
    run_summary,
)
from graph.schema.graph_nodes import CodeNode
from analyzer_settings import SETTINGS
from pipeline.steps.step6_analyze_code.example_mapping_builder import (
    ExampleMappingResult,
    build_example_mapping,
)
from graph.writers.example_writer import write_example_mapping
from llm.usage.cost_budget import BudgetExceededError
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.languages import ROUTINE_LABELS
from runtime_events.runtime_logger import log_error, log_info, log_warning
from runtime_events.ndjson_event_stream import NDJSONStream
from runtime_events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.code_node import update_summaries

_STEP = "step6.analyze_code"


def _build_table_contexts(
    tables: list, table_sample_data: dict, columns_by_table: dict | None = None,
) -> dict[str, dict]:
    """정확한 table id를 설명·컬럼·샘플과 분리한 프롬프트 컨텍스트를 만든다.

    step5(fetch_table_samples)가 채운 ``table_sample_data`` ({fqn:{sample_rows}}) 와 각
    렌더러는 코드노드별 reads/writes만 골라 JSON으로 직렬화한다. table id와 설명을
    한 문자열로 합치지 않으므로 LLM이 긴 설명을 id 일부로 오인하지 않는다.

    spec 027: ``columns_by_table`` (DDL 파싱 결정론 컬럼명)을 주입 — 라이브 datasource 가
    없어 샘플행이 비어도 LLM 이 알려진 컬럼 목록 기준으로 빠짐없이 서술하게 한다(컬럼 설명
    누락 방지). 컬럼은 R/W 로 그라운드된 테이블에만 렌더되므로 노이즈 없음.
    """
    columns_by_table = columns_by_table or {}
    out: dict[str, dict] = {}
    for t in tables:
        tid = getattr(t, "id", None)
        if not tid:
            continue
        cols = list(columns_by_table.get(tid) or [])
        rows = (table_sample_data.get(tid) or {}).get("sample_rows") or []
        out[tid] = {
            "description": t.description or "",
            "columns": cols,
            "sample_rows": rows,
        }
    return out


async def analyze_code_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Bottom-up DAG + 3 분할 fan-out.

    코드노드 분석 결과는 **코드노드 단위 즉시** Neo4j 박힘 + NDJSON stream emit — 라운드 또는
    step 끝까지 batch 누적 안 함. 메모리 누적 0, 그래프 실시간 반영.
    """
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream: Optional[NDJSONStream] = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or NULL_PAUSE

    contexts = state.get("analysis_contexts", {})
    code_node_list = state.get("code_nodes", [])
    code_by_id = {f.id: f for f in code_node_list}

    # 테이블 fqn → DDL 설명 — 3분할 reads/writes 렌더에 의미 부여 (LLM 이 테이블 뜻을 앎).
    # 테이블 "컨텍스트" = DDL 설명 + step5(fetch_table_samples)가 채운 실제 샘플행 합본.
    # 1채널(table_descriptions)로 흘려 공통 JSON 직렬화기가 코드노드별 reads/writes만 렌더한다.
    # 샘플은 토글 off / datasource 미지정 / 미매칭 시 없음 → 설명만(없으면 fqn 만).
    # spec 027: DDL 파싱 컬럼(결정론)을 테이블 fqn 별로 모아 컨텍스트에 주입.
    # ★ 컬럼의 소속 테이블은 **관계(HAS_COLUMN)** 로 찾는다 — id 를 뜯지 않는다 (spec 047 FR-008).
    #   종전엔 id 문자열을 잘라 테이블 id 를 얻었다. 그러면 소비자가
    #   id 의 **모양**에 묶여, id 규칙을 바꿀 수 없고 겹침도 못 고친다(그게 이 스펙 이전의 상태).
    #   id 는 불투명한 열쇠일 뿐이다 — 소속은 관계가 말해준다.
    name_by_col: dict[str, str] = {c.id: c.name for c in state.get("columns", [])}
    columns_by_table: dict[str, list[str]] = {}
    for e in state.get("has_column_edges", []):
        cname = name_by_col.get(e.child_id)
        if cname:
            columns_by_table.setdefault(e.parent_id, []).append(cname)
    table_descriptions = _build_table_contexts(
        state.get("tables", []), state.get("table_sample_data", {}), columns_by_table,
    )
    # LLM에 보여준 DDL 컬럼 allowlist를 결과 검증에도 같은 단일 진실로 사용한다.
    # 각 코드노드는 자기 R/W 테이블만 보유하므로 다른 테이블 컬럼이 섞일 수 없다.
    for ctx in contexts.values():
        ctx.extra["ddl_columns_by_table"] = {
            table_id: tuple(columns_by_table.get(table_id, ()))
            for table_id in dict.fromkeys((*ctx.reads, *ctx.writes))
        }

    # ScanResult.codebase_notes — 3분할 모든 호출이 공통 prefix 로 사용 (캐시 친화).
    # Framework 만 채움 — DBMS 는 빈 문자열 (Scan Agent 안 씀).
    scan_result = state.get("scan_result") or {}
    codebase_notes = scan_result.get("codebase_notes", "")

    if not contexts:
        log_info(_STEP, "skip", reason="no analysis_contexts")
        return {}

    started = time.monotonic()
    reset_drop_stats()  # 디스패처 테이블-드롭 run 집계 시작 (종료 시 flush 가 1줄 표면화)

    # 0. 모듈 사전분석 — 모듈당 LLM 1회로 헤더 주석 → 모듈 의도 (최대 2줄).
    # intent 는 코드노드 3분할 공통 context prefix 로만 사용 — 영구 그래프 속성 X.
    # module_pre_analysis 가 자체 step_processing_scope 진입 → 가시성 자동.
    module_intents = await run_module_pre_analysis(
        state.get("modules", []), code_node_list, strategy, cost_budget,
        stream=stream,
    )

    # ★ 분석 엔진은 **전략이 주입**한다 (헌법 II "분기 0" / spec 046 FR-001).
    #   framework = per-node 3분할 / dbms = 형제 배치 1콜.
    #   종전엔 여기에 `strategy.name == "dbms"` 하드분기가 있었다 — graph_builder 규약이
    #   *"같은 step 인데 처리 방식 다름? → cfg["strategy"].<implementer> 호출(DI)"* 라고
    #   못박아 놓은 것을 위반한 상태였다.
    #   두 엔진은 **유지**한다 (FR-002): 노드 우주 정의가 실제로 다르다 — framework 는
    #   contexts 전체, dbms 는 skip-투명 유효트리.
    # run 스코프 공유 소스 캐시 (executor 주입) — 없으면 단독 생성(CLI/테스트 경로). FR-008.
    source_cache = cfg.get("source_cache") or SourceFileCache()

    with step_processing_scope(_STEP, stream):
        result = await strategy.code_analyzer(
            state, contexts, code_by_id, strategy, cost_budget, neo, stream,
            codebase_notes, module_intents, table_descriptions, pause, source_cache,
        )
    flush_drop_summary()  # 디스패처 드롭 집계 1줄 표면화 (FR-005a)
    return result
