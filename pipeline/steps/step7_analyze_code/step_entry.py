"""Step 7 entry: validate structural inputs and delegate one injected analysis engine.

이 모듈은 table/column·framework 계약을 AnalysisContext에 결합하고 Framework 또는 DBMS
전략 구현을 한 번 호출한다. LLM을 직접 호출하거나 module intent를 별도 생성하지 않으며,
어떤 종료 경로에서도 run-scoped handoff를 닫는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache
from pipeline.languages import ROUTINE_LABELS, lang_of
from pipeline.quality_gates.branch_inventory import validate_static_jump_targets
from pipeline.execution.analysis_handoff import AnalysisHandoff, handoff_of
from pipeline.execution.pause_controller import NULL_PAUSE, PauseController
from pipeline.execution.step_config import step_config

# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from pipeline.steps.step7_analyze_code.column_usage_projection import (
    build_column_usage_projections,
)
from shared.events.runtime_logger import log_info
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.processing_events import step_processing_scope

_STEP = "step7.analyze_code"


def _validate_structural_ast_contract(
    contexts: dict,
    code_by_id: dict,
    source_cache: SourceFileCache,
) -> None:
    """Reject stale routine statement AST before the first semantic model call."""
    for node_id, node in code_by_id.items():
        if node.label not in ROUTINE_LABELS:
            continue
        ctx = contexts.get(node_id)
        if ctx is None:
            raise ValueError(f"routine analysis context missing: {node_id!r}")
        statements = ctx.extra.get("ast_statements")
        if statements is None:
            raise ValueError(
                f"ast_statements missing for {node_id!r} — parser-owned "
                "structural contract cannot be preflighted"
            )
        source = source_cache.slice(ctx.file_path, ctx.start_line, ctx.end_line)
        validate_static_jump_targets(
            source,
            lang_of(Path(node.file_path).suffix),
            tuple(statements),
            line_offset=max(node.start_line - 1, 0),
            target_id=node.id,
            source_ref=ctx.file_path,
        )


def _build_table_contexts(
    tables: list, table_sample_data: dict, columns_by_table: dict | None = None,
) -> dict[str, dict]:
    """정확한 table id를 설명·컬럼·샘플과 분리한 프롬프트 컨텍스트를 만든다.

    step1(discover_datasource)가 채운 ``table_sample_data`` ({fqn:{sample_rows}})와 각
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
            "name": t.name,
            "description": t.description or "",
            "columns": cols,
            "sample_rows": rows,
        }
    return out


async def analyze_code_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Bottom-up DAG + 관심사 fan-out.

    코드노드 분석 결과는 **코드노드 단위 즉시** state에 합치고 NDJSON 진행 이벤트를
    보낸다. 제품 그래프 저장은 전체 계획 검증 뒤 최종 공개 경로만 수행한다.
    """
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    stream: Optional[NDJSONStream] = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or NULL_PAUSE
    # 후처리와 슬롯·분할 확정 신호를 나누는 유일한 run 스코프 채널.
    handoff: AnalysisHandoff = handoff_of(cfg)

    contexts = state.get("analysis_contexts", {})
    code_node_list = state.get("code_nodes", [])
    code_by_id = {f.id: f for f in code_node_list}

    # 테이블 fqn → DDL 설명 — 3분할 reads/writes 렌더에 의미 부여 (LLM 이 테이블 뜻을 앎).
    # 테이블 "컨텍스트" = DDL 설명 + step1(discover_datasource)가 채운 실제 샘플행 합본.
    # 1채널(table_descriptions)로 흘려 공통 JSON 직렬화기가 코드노드별 reads/writes만 렌더한다.
    # 샘플은 토글 off / datasource 미지정 / 미매칭 시 없음 → 설명만(없으면 fqn 만).
    # spec 027: DDL 파싱 컬럼(결정론)을 테이블 fqn 별로 모아 컨텍스트에 주입.
    # ★ 컬럼의 소속 테이블은 **관계(HAS_COLUMN)** 로 찾는다 — id 를 뜯지 않는다 (spec 047 FR-008).
    #   종전엔 id 문자열을 잘라 테이블 id 를 얻었다. 그러면 소비자가
    #   id 의 **모양**에 묶여, id 규칙을 바꿀 수 없고 겹침도 못 고친다(그게 이 스펙 이전의 상태).
    #   id 는 불투명한 열쇠일 뿐이다 — 소속은 관계가 말해준다.
    column_by_id = {c.id: c for c in state.get("columns", [])}
    columns_by_table: dict[str, list[str]] = {}
    column_identities_by_table: dict[str, list[object]] = {}
    for e in state.get("has_column_edges", []):
        column = column_by_id.get(e.child_id)
        if column is not None and column.name:
            columns_by_table.setdefault(e.parent_id, []).append(column.name)
            column_identities_by_table.setdefault(e.parent_id, []).append(column)
    table_descriptions = _build_table_contexts(
        state.get("tables", []), state.get("table_sample_data", {}), columns_by_table,
    )
    # LLM에 보여준 DDL 컬럼 allowlist를 결과 검증에도 같은 단일 진실로 사용한다.
    # 각 코드노드는 자기 R/W 테이블만 보유하므로 다른 테이블 컬럼이 섞일 수 없다.
    for ctx in contexts.values():
        ctx.extra["ddl_columns_by_table"] = {
            table_id: tuple(column_identities_by_table.get(table_id, ()))
            for table_id in dict.fromkeys((*ctx.reads, *ctx.writes))
        }

    # Analyzer가 검증한 구조화 계약만 downstream에 전달한다.
    scan_result = state.get("scan_result") or {}

    # SKILL 계약의 핸들러 마커(PFM_CATCH 류) — 분기표 [handler] 행의 원천 (spec 116).
    # 언어 표가 아니라 프레임워크 계약이 소유하므로 ctx 를 통해 주입한다.
    handler_markers = tuple(scan_result.get("handler_markers") or ())
    if handler_markers:
        for ctx in contexts.values():
            ctx.extra["handler_markers"] = handler_markers

    # SKILL 계약의 API 값 계약(mpfmdbio success_value 류) — rules 프롬프트의
    # "소스만 봐서는 모르는 값 사실" 절 + 채점기 창작 검사 풀의 원천 (spec 116 §19).
    api_contracts = tuple(scan_result.get("api_contracts") or ())
    if api_contracts:
        for ctx in contexts.values():
            ctx.extra["api_contracts"] = api_contracts

    if not contexts:
        log_info(_STEP, "skip", reason="no analysis_contexts")
        handoff.close()   # 후처리가 영원히 기다리지 않게 — 조용한 교착 금지
        return {}

    # The parser owns named GOTO and LABEL facts.  Validate the complete routine AST before
    # module pre-analysis so a stale AST cannot spend GPU time and fail much later in Rule
    # materialization.
    source_cache = cfg.get("source_cache") or SourceFileCache()
    _validate_structural_ast_contract(contexts, code_by_id, source_cache)

    # ★ 분석 엔진은 **전략이 주입**한다 (헌법 II "분기 0" / spec 046 FR-001).
    #   framework = per-node 3분할 / dbms = 형제 배치 1콜.
    #   종전엔 여기에 `strategy.name == "dbms"` 하드분기가 있었다 — graph_builder 규약이
    #   *"같은 step 인데 처리 방식 다름? → cfg["strategy"].<implementer> 호출(DI)"* 라고
    #   못박아 놓은 것을 위반한 상태였다.
    #   두 엔진은 **유지**한다 (FR-002): 노드 우주 정의가 실제로 다르다 — framework 는
    #   contexts 전체, dbms 는 skip-투명 유효트리.
    # run 스코프 공유 소스 캐시 (executor 주입) — 없으면 단독 생성(CLI/테스트 경로). FR-008.
    column_usage_projections = build_column_usage_projections(
        contexts,
        code_by_id,
        strategy_name=strategy.name,
        source_cache=source_cache,
    )
    for node_id, ctx in contexts.items():
        ctx.extra["column_usage_candidates"] = column_usage_projections.get(node_id, {})

    # 인계 채널은 **어떤 경로로 빠져나가든** 반드시 닫는다. 안 닫으면 같은 superstep 의
    # 후처리가 게이트에서 영원히 대기해 run 이 끝나지 않는다 (조용한 교착 금지).
    try:
        with step_processing_scope(_STEP, stream):
            result = await strategy.code_analyzer(
                state, contexts, code_by_id, strategy, cost_budget, stream,
                table_descriptions, pause, source_cache,
                handoff,
            )
    except BaseException as error:
        handoff.abort(error)
        raise
    finally:
        handoff.close()
    return result
