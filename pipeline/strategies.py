"""분석 파이프라인 선언 — Framework / DBMS 두 strategy 한 파일에 모음.

설계 원칙
─────────
- 한 파일에 두 pipeline 다 — 새 strategy 추가도 이 파일 1곳만.
- ``Strategy`` 는 ``steps`` (실행 순서 함수 tuple) + ``context_builder`` +
  ``prompts_dir`` 의 데이터 컨테이너. 로직 X.
- AST 파싱은 antlr JSON reader 공통 — strategy 별 parser 주입 없음
  (``load_ast_step`` 이 ``antlr_json_reader`` 를 직접 호출).
- ``pipeline/execution/langgraph_builder.build_langgraph_workflow(strategy)`` 가
  ``strategy.steps`` 만 보고 그래프 빌드 — if 분기 0개.

**LangGraph 의 책임**:
- 상태 관리: ``StateGraph`` 가 ``WorkflowState`` 자동 전파.
- state 전파: ``Checkpointer`` 가 run 1회 안의 state 보존 (매 ``/analyze`` = 독립 run).
- 스트림: ``astream(stream_mode="updates")`` 단일 채널.

8단계 흐름은 ``pipeline/steps/step1~step8/`` 폴더 구조로 그대로 드러난다.
step3 external loader는 spec 075에서 두 전략의 공통 step으로 삽입된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# 공통 step (양쪽 다 씀)
from pipeline.steps.step1_parse_ddl.parse_ddl_step import parse_ddl_step
from pipeline.steps.step1_parse_ddl.discover_datasource_step import discover_datasource_step
from pipeline.steps.step1_parse_ddl.resolve_data_objects_step import resolve_data_objects_step
from pipeline.steps.step2_load_ast.load_ast_step import load_ast_step
from pipeline.steps.step3_load_external.load_external_step import load_external_step
from pipeline.steps.step5_linking.link_tables.link_tables_by_regex_step import (
    link_deterministic_step,
)
from pipeline.steps.step5_linking.link_external.link_external_step import link_external_step
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step7_analyze_code.step_entry import analyze_code_step
from pipeline.steps.step7_analyze_code.framework_per_node import (
    framework_analysis_targets,
    run_framework_per_node_analysis,
)
from pipeline.steps.step7_analyze_code.batch_analyze import (
    dbms_analysis_targets,
    run_dbms_batch_analysis,
)
from pipeline.steps.step8_post_processing.embed_step import embed_step
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    enrich_table_description_step,
)
from pipeline.steps.step8_post_processing.summarize_module_step import summarize_module_step
from pipeline.steps.step8_post_processing.build_components_step import build_components_step
from pipeline.steps.step8_post_processing.build_glossary_step import build_glossary_step
from pipeline.steps.step8_post_processing.health_metrics_step import health_metrics_step

# Framework 전용 step
from pipeline.steps.step4_scan_project.scan_codebase_step import scan_codebase_step
from pipeline.steps.step4_scan_project.scan_external_step import scan_external_step
from pipeline.steps.step5_linking.link_calls.link_calls_by_name_step import (
    link_calls_by_name_step,
)
from pipeline.steps.step5_linking.link_tables.link_residual_patterns_step import (
    link_residual_patterns_step,
)

# DBMS 전용 step
from pipeline.steps.step5_linking.link_tables.link_dbms_tables_step import link_dbms_tables_step
from pipeline.steps.step5_linking.link_calls.link_calls_by_ast_step import (
    link_calls_by_ast_step,
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
        analysis_targets: ``code_nodes`` → 그 엔진이 실제로 분석할 노드 id 집합.
            엔진과 ``build_context_step`` 이 같은 답을 봐야 하는 단일 진실 —
            대상 밖 노드는 어느 렌더에도 등장하지 않으므로 사전 압축 대상도 아니다.
        prompts_dir: yaml 프롬프트 위치 (``"llm/prompts/framework"`` / ``"llm/prompts/dbms"``).
        steps: 활성 step 함수 tuple (이 순서대로 LangGraph StateGraph 노드).

    Note: AST 파싱은 ``antlr_json_reader`` 공통 — parser 주입 없음.
    """
    name: str
    context_builder: Any
    code_analyzer: Callable
    analysis_targets: Callable
    prompts_dir: str
    steps: tuple[Callable, ...]


# ═══════════════════════════════════════════════════════════════
# Framework Pipeline
# ═══════════════════════════════════════════════════════════════

FRAMEWORK_PIPELINE = Strategy(
    name="framework",
    context_builder=FrameworkContextBuilder(),    # build_context_step 이 주입받음
    code_analyzer=run_framework_per_node_analysis,  # analyze_code_step 이 주입받음 (per-node 3분할)
    analysis_targets=framework_analysis_targets,    # routine 만 — 위 엔진의 필터와 같은 함수
    prompts_dir="llm/prompts/framework",
    steps=(
        parse_ddl_step,
        # discover: 단어장 이원화(DDL ∪ 실DB) + 샘플 수집 통합 — 데이터소스 HTTP 접점은
        # 이 step 한 곳 (spec 107, 구 step6 fetch_table_samples 흡수). tables 키는 이
        # step 만 쓰므로 load_ast 와 병렬 안전.
        (load_ast_step, load_external_step, discover_datasource_step),
        scan_codebase_step,                # ★ Framework 만 — Scan Agent 1회
        scan_external_step,                # unknown text만 — external skill reload/agent
        link_deterministic_step,
        link_calls_by_name_step,              # ★ Framework 만 — 결정론 Call 분류 + wrapper 통합 (LLM 0)
        link_residual_patterns_step,          # ★ Framework 만 — 잔여 패턴 루프 (테이블 미해결)
        link_external_step,                   # 외부 파일 key/FQN/SQL→DDL 결정론 연결
        build_context_step,

        # ★ 후처리는 step7 과 **같은 superstep**에서 돈다 (spec 096). 노드 간 배리어를 두면
        #   후처리가 step7 의 마지막 콜까지 기다리는데, 실제로 후처리가 읽는 값은 step7 의
        #   일부 분할에서만 나온다. 각 step 이 ``AnalysisHandoff`` 로 자기 의존이 풀리는
        #   시점을 스스로 기다리고, 남는 LLM 슬롯을 낮은 우선순위로 채운다.
        #   (총 동시 요청은 안 늘어난다 — 세마포어가 run 에 하나뿐이다.)
        (
            analyze_code_step,
            summarize_module_step,            # 게이트: 전 노드 #1 summary
            build_components_step,            # 게이트: 전 노드 #1 summary (Leiden + 병렬 naming)
            build_glossary_step,              # 사람 제공 glossary 독립 적재
            enrich_table_description_step,    # 게이트: 전 노드 완료 (insights 가 rules 와 함께 확정)
            health_metrics_step,              # 게이트 없음 — 결정론 그래프(step2·5)만 읽는다
        ),
        embed_step,
    ),
)


# ═══════════════════════════════════════════════════════════════
# DBMS Pipeline
# ═══════════════════════════════════════════════════════════════

DBMS_PIPELINE = Strategy(
    name="dbms",
    context_builder=FrameworkContextBuilder(),     # parent + sibling 컨텍스트
    code_analyzer=run_dbms_batch_analysis,    # analyze_code_step 이 주입받음 (형제 배치 1콜)
    analysis_targets=dbms_analysis_targets,   # 유효 트리 잔존 노드 — 위 엔진과 같은 집합
    prompts_dir="llm/prompts/dbms",
    steps=(
        parse_ddl_step,
        (load_ast_step, load_external_step),
        # parser object evidence dedup → Catalog/Fabric exact batch → confirmed sample.
        # 전체 datasource eager discovery는 DBMS production 경로에서 제거한다(spec 126).
        resolve_data_objects_step,
        scan_external_step,                # unknown text만 — external skill reload/agent
        # scan_codebase_step 없음 — DBMS 는 Scan Agent 안 씀 (Plan §6)
        link_dbms_tables_step,                # ★ DBMS 만 — DML range → DDL 테이블 결정론 매칭 (LLM 0)
        # link_deterministic_step 없음 — DBMS R/W의 단일 생산자는 위
        # link_dbms_tables_step이다. 이 부재는 test_dbms_pipeline_contract가 고정한다.
        link_calls_by_ast_step,               # ★ DBMS 만 — AST CALL 노드 분류 (빌트인·자기루프 드롭)
        # 잔여 패턴 루프 없음 — DBMS 는 에이전트 링킹을 안 씀
        link_external_step,                   # 외부 파일 key/FQN/SQL→DDL 결정론 연결
        build_context_step,

        # ★ Framework 와 같은 겹치기 (spec 096) — 각 step 이 ``AnalysisHandoff`` 로 자기
        #   의존 해제를 기다린다. DBMS 배치 엔진은 summary 와 rules 가 한 콜에서 같이
        #   확정되므로 #1 게이트와 완료 게이트가 사실상 같은 시점이다.
        (
            analyze_code_step,
            summarize_module_step,            # 게이트: 전 노드 요약
            build_components_step,            # 게이트: 전 노드 요약
            build_glossary_step,              # 사람 제공 glossary 독립 적재
            enrich_table_description_step,    # 게이트: 전 노드 완료
            health_metrics_step,              # 게이트 없음 — 결정론 그래프(step2·5)만 읽는다
        ),
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
