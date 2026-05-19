"""분석 파이프라인 선언 — Framework / DBMS 두 strategy 한 파일에 모음.

설계 원칙
─────────
- 한 파일에 두 pipeline 다 — 새 strategy 추가도 이 파일 1곳만.
- ``Strategy`` 는 ``steps`` (실행 순서 함수 tuple) + ``parser`` + ``context_builder`` +
  ``prompts_dir`` 의 데이터 컨테이너. 로직 X.
- ``shared/workflow_runner/langgraph_workflow.build_langgraph_workflow(strategy)`` 가
  ``strategy.steps`` 만 보고 그래프 빌드 — if 분기 0개.

**LangGraph 의 책임**:
- 상태 관리: ``StateGraph`` 가 ``WorkflowState`` 자동 전파.
- 일시정지 / 재개: ``Checkpointer`` 가 ``thread_id`` 별 state 보존.
- 스트림: ``astream(stream_mode="updates")`` 단일 채널.

7 단계 흐름은 ``analysis_flow/step1~step7/`` 폴더 구조로 그대로 드러남.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# 공통 step (양쪽 다 씀)
from analysis_flow.step1_parse_ddl.parse_ddl_step import parse_ddl_step
from analysis_flow.step2_parse_source.parse_source_step import parse_source_step
from analysis_flow.step2_parse_source.plsql_parser import PLSQLParser
from analysis_flow.step2_parse_source.tree_sitter_parser import TreeSitterParser
from analysis_flow.step4_linking.stage1_regex_grep.link_deterministic_step import (
    link_deterministic_step,
)
from analysis_flow.step5_build_context.build_context_step import build_context_step
from analysis_flow.step5_build_context.dbms_context_builder import DbmsContextBuilder
from analysis_flow.step5_build_context.framework_context_builder import FrameworkContextBuilder
from analysis_flow.step6_analyze_function.analyze_function_step import analyze_function_step
from analysis_flow.step7_post_processing.embed_step import embed_step
from analysis_flow.step7_post_processing.enrich_table_description_step import (
    enrich_table_description_step,
)
from analysis_flow.step7_post_processing.summarize_module_step import summarize_module_step

# Framework 전용 step
from analysis_flow.step3_profile_project.profile_codebase_step import profile_codebase_step
from analysis_flow.step4_linking.stage2_extract_calls.link_calls_by_llm_step import (
    link_calls_by_llm_step,
)
from analysis_flow.step4_linking.stage3_resolve_unresolved.link_unresolved_step import (
    link_unresolved_step,
)

# DBMS 전용 step
from analysis_flow.step4_linking.stage2_extract_calls.link_calls_by_regex_step import (
    link_calls_by_regex_step,
)
from analysis_flow.step7_post_processing.enrich_lineage_from_catalog_step import (
    enrich_lineage_from_catalog_step,
)
from analysis_flow.step7_post_processing.enrich_metadata_from_catalog_step import (
    enrich_metadata_from_catalog_step,
)
from analysis_flow.step7_post_processing.extract_dml_metadata_step import (
    extract_dml_metadata_step,
)


@dataclass(frozen=True)
class Strategy:
    """파이프라인 전략 선언 — 한 파일 안에 framework / dbms 둘 다 둠.

    Fields:
        name: 식별자 (``"framework"`` / ``"dbms"``).
        parser: ``parse_source_step`` 이 주입받는 파서 인스턴스.
        context_builder: ``build_context_step`` 이 주입받는 컨텍스트 빌더 인스턴스.
        prompts_dir: yaml 프롬프트 위치 (``"prompts/framework"`` / ``"prompts/dbms"``).
        steps: 활성 step 함수 tuple (이 순서대로 LangGraph StateGraph 노드).
    """
    name: str
    parser: Any
    context_builder: Any
    prompts_dir: str
    steps: tuple[Callable, ...]


# ═══════════════════════════════════════════════════════════════
# Framework Pipeline
# ═══════════════════════════════════════════════════════════════

FRAMEWORK_PIPELINE = Strategy(
    name="framework",
    parser=TreeSitterParser(),                # parse_source_step 이 주입받음
    context_builder=FrameworkContextBuilder(),    # build_context_step 이 주입받음
    prompts_dir="prompts/framework",
    steps=(
        parse_ddl_step,
        parse_source_step,
        profile_codebase_step,                # ★ Framework 만 — Profile Agent 1회
        link_deterministic_step,
        link_calls_by_llm_step,               # ★ Framework 만 — LLM 배치 Call 분류
        link_unresolved_step,                 # ★ Framework 만 — Linking Agent invoke
        build_context_step,
        analyze_function_step,
        summarize_module_step,
        enrich_table_description_step,
        embed_step,
    ),
)


# ═══════════════════════════════════════════════════════════════
# DBMS Pipeline
# ═══════════════════════════════════════════════════════════════

DBMS_PIPELINE = Strategy(
    name="dbms",
    parser=PLSQLParser(),                     # procedure + nested range → Function + parent_id
    context_builder=DbmsContextBuilder(),     # parent + sibling 컨텍스트
    prompts_dir="prompts/dbms",
    steps=(
        parse_ddl_step,
        parse_source_step,
        # profile_codebase_step 없음 — DBMS 는 Profile Agent 안 씀 (Plan §6)
        extract_dml_metadata_step,            # ★ DBMS 만 — DML range 메타 LLM 추출
        link_deterministic_step,
        link_calls_by_regex_step,             # ★ DBMS 만 — PL/SQL PKG.FUNC regex
        # link_unresolved_step 없음 — DBMS 는 DeepAgent 안 씀
        build_context_step,
        analyze_function_step,
        summarize_module_step,
        enrich_table_description_step,
        enrich_metadata_from_catalog_step,    # ★ DBMS 만 — catalog metadata
        enrich_lineage_from_catalog_step,     # ★ DBMS 만 — catalog lineage
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
