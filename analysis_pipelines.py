"""분석 파이프라인 선언 — Framework / DBMS 두 strategy 한 파일에 모음.

설계 원칙
─────────
- 한 파일에 두 pipeline 다 — 새 strategy 추가도 이 파일 1곳만.
- ``Strategy`` 는 ``steps`` (실행 순서 함수 tuple) + ``context_builder`` +
  ``prompts_dir`` 의 데이터 컨테이너. 로직 X.
- AST 파싱은 antlr JSON reader 공통 — strategy 별 parser 주입 없음
  (``load_ast_step`` 이 ``antlr_json_reader`` 를 직접 호출).
- ``shared/workflow/graph_builder.build_langgraph_workflow(strategy)`` 가
  ``strategy.steps`` 만 보고 그래프 빌드 — if 분기 0개.

**LangGraph 의 책임**:
- 상태 관리: ``StateGraph`` 가 ``WorkflowState`` 자동 전파.
- state 전파: ``Checkpointer`` 가 run 1회 안의 state 보존 (매 ``/analyze`` = 독립 run).
- 스트림: ``astream(stream_mode="updates")`` 단일 채널.

7 단계 흐름은 ``analysis_flow/step1~step7/`` 폴더 구조로 그대로 드러남.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# 공통 step (양쪽 다 씀)
from analysis_flow.step1_parse_ddl.parse_ddl_step import parse_ddl_step
from analysis_flow.step2_load_ast.load_ast_step import load_ast_step
from analysis_flow.step4_linking.stage1_regex_grep.link_deterministic_step import (
    link_deterministic_step,
)
from analysis_flow.step5_build_context.build_context_step import build_context_step
from analysis_flow.step5_build_context.dbms_context_builder import DbmsContextBuilder
from analysis_flow.step5_build_context.framework_context_builder import FrameworkContextBuilder
from analysis_flow.step5_build_context.fetch_table_samples_step import fetch_table_samples_step
from analysis_flow.step6_analyze_code.analyze_code_step import (
    analyze_code_step,
    run_framework_per_node_analysis,
)
from analysis_flow.step6_analyze_code.batch_analyze import run_dbms_batch_analysis
from analysis_flow.step7_post_processing.embed_step import embed_step
from analysis_flow.step7_post_processing.enrich_table_description_step import (
    enrich_table_description_step,
)
from analysis_flow.step7_post_processing.summarize_module_step import summarize_module_step
from analysis_flow.step7_post_processing.build_components_step import build_components_step
from analysis_flow.step7_post_processing.build_glossary_step import build_glossary_step
from analysis_flow.step7_post_processing.health_metrics_step import health_metrics_step

# Framework 전용 step
from analysis_flow.step3_scan_project.scan_codebase_step import scan_codebase_step
from analysis_flow.step4_linking.stage2_extract_calls.link_calls_by_name_step import (
    link_calls_by_name_step,
)
from analysis_flow.step4_linking.stage3_residual_patterns.link_residual_patterns_step import (
    link_residual_patterns_step,
)

# DBMS 전용 step
from analysis_flow.step4_linking.link_dbms_tables_step import link_dbms_tables_step
from analysis_flow.step4_linking.stage2_extract_calls.link_calls_by_ast_step import (
    link_calls_by_ast_step,
)
from analysis_flow.step7_post_processing.enrich_lineage_from_catalog_step import (
    enrich_lineage_from_catalog_step,
)


@dataclass(frozen=True)
class Strategy:
    """파이프라인 전략 선언 — 한 파일 안에 framework / dbms 둘 다 둠.

    Fields:
        name: 식별자 (``"framework"`` / ``"dbms"``).
        context_builder: ``build_context_step`` 이 주입받는 컨텍스트 빌더 인스턴스.
        code_analyzer: ``analyze_code_step`` 이 주입받는 **분석 엔진** (per-node 3분할 /
            형제 배치). 두 구현체는 **동일 시그니처** — step 은 어느 쪽인지 모른다.
            (헌법 II "분기 0" — 종전엔 step 안에 ``strategy.name == "dbms"`` 하드분기가
            있었다. spec 046 FR-001.)
        prompts_dir: yaml 프롬프트 위치 (``"prompts/framework"`` / ``"prompts/dbms"``).
        steps: 활성 step 함수 tuple (이 순서대로 LangGraph StateGraph 노드).

    Note: AST 파싱은 ``antlr_json_reader`` 공통 — parser 주입 없음.
    """
    name: str
    context_builder: Any
    code_analyzer: Callable
    prompts_dir: str
    steps: tuple[Callable, ...]


# ═══════════════════════════════════════════════════════════════
# Framework Pipeline
# ═══════════════════════════════════════════════════════════════

FRAMEWORK_PIPELINE = Strategy(
    name="framework",
    context_builder=FrameworkContextBuilder(),    # build_context_step 이 주입받음
    code_analyzer=run_framework_per_node_analysis,  # analyze_code_step 이 주입받음 (per-node 3분할)
    prompts_dir="prompts/framework",
    steps=(
        parse_ddl_step,
        load_ast_step,
        scan_codebase_step,                # ★ Framework 만 — Scan Agent 1회
        link_deterministic_step,
        link_calls_by_name_step,              # ★ Framework 만 — 결정론 Call 분류 + wrapper 통합 (LLM 0)
        link_residual_patterns_step,          # ★ Framework 만 — 잔여 패턴 루프 (테이블 미해결)
        build_context_step,
        fetch_table_samples_step,             # ★ 옵션·전략독립 — catalog 샘플 → table_sample_data (analyze 전)
        analyze_code_step,
        # ★ 후처리 병렬 그룹 — 4 step 서로 독립(입력 read-only·return state 키 비충돌:
        #   modules / tables+columns / {} / {}). 동시 실행 후 합류.
        #   (build_components·health_metrics 는 state 를 안 늘리고 노드 속성만 SET → {} 반환.)
        #   공유 llm_semaphore(executor 주입)로 서버 총 동시수 제어(과부하 방지).
        (
            summarize_module_step,
            enrich_table_description_step,
            build_components_step,            # ① 컴포넌트 군집화+이름 (Leiden + 병렬 naming)
            health_metrics_step,              # ② 건강지표 (결정론·LLM 0 — 그래프 위 계산)
        ),
        build_glossary_step,                  # 용어사전 종합 (spec 043 — 모듈 요약을 문맥으로 쓰므로 그룹 뒤)
        embed_step,
    ),
)


# ═══════════════════════════════════════════════════════════════
# DBMS Pipeline
# ═══════════════════════════════════════════════════════════════

DBMS_PIPELINE = Strategy(
    name="dbms",
    context_builder=DbmsContextBuilder(),     # parent + sibling 컨텍스트
    code_analyzer=run_dbms_batch_analysis,    # analyze_code_step 이 주입받음 (형제 배치 1콜)
    prompts_dir="prompts/dbms",
    steps=(
        parse_ddl_step,
        load_ast_step,
        # scan_codebase_step 없음 — DBMS 는 Scan Agent 안 씀 (Plan §6)
        link_dbms_tables_step,                # ★ DBMS 만 — DML range → DDL 테이블 결정론 매칭 (LLM 0)
        # link_deterministic_step 없음 — DBMS 는 Scan 미사용이라 R/W 0 을 반환해
        #   link_dbms_tables_step 의 R/W 를 덮어쓸 뿐 (artifact 오기록). stage3 도 없어
        #   unresolved 소비처도 없음 → 데드 step (Framework 만 사용).
        link_calls_by_ast_step,               # ★ DBMS 만 — AST CALL 노드 분류 (빌트인·자기루프 드롭)
        # 잔여 패턴 루프 없음 — DBMS 는 에이전트 링킹을 안 씀
        build_context_step,
        fetch_table_samples_step,             # ★ 옵션·전략독립 — catalog 샘플 → table_sample_data (analyze 전)
        analyze_code_step,
        summarize_module_step,
        enrich_table_description_step,
        # enrich_metadata_from_catalog_step 제거 — 미존재 엔드포인트(GET /tables/<fqn>/metadata)
        #   기반이라 동작 X. 테이블 메타(샘플)는 fetch_table_samples_step(sample-context)로 대체.
        enrich_lineage_from_catalog_step,     # ★ DBMS 만 — catalog lineage
        build_components_step,                # ① 컴포넌트 군집화+이름 (Leiden + 병렬 naming)
        health_metrics_step,                  # ② 건강지표 (결정론·LLM 0 — 그래프 위 계산)
        build_glossary_step,                  # 용어사전 종합 (spec 043 — 모듈 요약 뒤)
        embed_step,
    ),
)


# ═══════════════════════════════════════════════════════════════
# 이름 → Pipeline lookup
# ═══════════════════════════════════════════════════════════════

PIPELINES: dict[str, Strategy] = {
    "framework": FRAMEWORK_PIPELINE,
    "dbms": DBMS_PIPELINE,
}


def get_pipeline(name: str) -> Strategy:
    """strategy 이름 → Strategy 인스턴스. 모르는 이름은 ``ValueError``."""
    pipeline = PIPELINES.get(name)
    if pipeline is None:
        raise ValueError(f"Unknown pipeline: {name!r} (expected one of {sorted(PIPELINES)})")
    return pipeline
