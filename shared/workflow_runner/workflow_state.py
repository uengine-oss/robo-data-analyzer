"""WorkflowState — LangGraph StateGraph 의 state.

설계 원칙:
- **strategy 무관** (Framework / DBMS 같은 state). 분기 0개.
- 누적 데이터는 list (Annotated + ``operator.add`` reducer 로 LangGraph 가 자동 누적).
- 모든 step 의 입출력은 이 state 의 키.

새 step / 새 데이터 종류 추가 시:
1. ``data_models/`` 에 dataclass 추가 (해당 시)
2. 이 state 에 키 추가 (Annotated + reducer)
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from data_models.analysis_context import AnalysisContext
from data_models.graph_edges import (
    BelongsTo,
    Calls,
    Fk,
    HasColumn,
    ParentOf,
    ReadsWrites,
    ReferTo,
    Uses,
)
from data_models.graph_nodes import Actor, Column, Function, Module, Schema, Table
from data_models.project_and_run import Run


class WorkflowState(TypedDict, total=False):
    """파이프라인 1회 실행의 누적 state.

    각 step 이 자기 결과를 list 에 append → ``operator.add`` reducer 가
    LangGraph 가 자동 누적. ``analysis_contexts`` 같은 dict 는 별도 reducer 없이 그냥 dict.
    """
    # ── 실행 메타 ─────────────────────────────────
    run: Run

    # ── step1_parse_ddl 결과 ──────────────────────
    schemas: Annotated[list[Schema], operator.add]
    tables: Annotated[list[Table], operator.add]
    columns: Annotated[list[Column], operator.add]
    has_column_edges: Annotated[list[HasColumn], operator.add]
    belongs_to_edges: Annotated[list[BelongsTo], operator.add]
    fk_edges: Annotated[list[Fk], operator.add]

    # ── step2_parse_source 결과 ───────────────────
    functions: Annotated[list[Function], operator.add]
    modules: Annotated[list[Module], operator.add]
    uses_edges: Annotated[list[Uses], operator.add]
    parent_of_edges: Annotated[list[ParentOf], operator.add]

    # ── step3_profile_project 결과 (Framework 만) ─
    profile_result: dict                     # ProfileResult Pydantic → dict

    # ── step4_linking 결과 ────────────────────────
    reads_writes_edges: Annotated[list[ReadsWrites], operator.add]
    calls_edges: Annotated[list[Calls], operator.add]
    refer_to_edges: Annotated[list[ReferTo], operator.add]
    unresolved_cases: Annotated[list[dict], operator.add]

    # ── step5_build_context 결과 ──────────────────
    analysis_contexts: dict[str, AnalysisContext]   # function_id → ctx

    # ── step7_post_processing (Actor 통합) 결과 ──
    actors: Annotated[list[Actor], operator.add]
