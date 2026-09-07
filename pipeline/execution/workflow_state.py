"""WorkflowState — LangGraph StateGraph 의 state.

설계 원칙
─────────
- **strategy 무관** (Framework / DBMS 같은 state). 분기 0개.
- 각 state 키 = **1 step 만 반환** — LangGraph default replace 로 충분.
  새 step 추가도 키 책임 단일하면 reducer 불필요.
- 모든 step 의 입출력은 이 state 의 키.

중복 / 단일 진실
────────────────
발생 장부는 source occurrence를 손실 없이 보존한다. 제품 공개 전에는 각 builder가
안정된 제품 identity로 투영하고, 완전한 ``ProductWritePlan``이 operation·property owner·
endpoint·record hash의 누락과 중복을 검증한다. Neo4j MERGE에 정확성 책임을 넘기지 않는다.

영역 규칙 — 새 step 추가 시
─────────────────────────────
각 step 은 자기가 만든 항목만 ``return {"<key>": [items]}`` 으로 돌려준다.
LangGraph 가 default replace 로 state 에 박는다. 같은 키를 두 step 가 동시 책임?
→ 영역 설계 잘못. 한 키 = 한 step 책임 단일화.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.analysis_meta import AnalysisRun
from graph.schema.graph_edges import (
    Calls,
    Fk,
    HasColumn,
    HasRule,
    InitBy,
    ParentOf,
    ReadsWrites,
    References,
    StatementNext,
)
from graph.schema.graph_nodes import (
    Column,
    Constant,
    DataType,
    CodeNode,
    Module,
    Package,
    Rule,
    Schema,
    Table,
    Variable,
)
from pipeline.models.external_reference_resolution import ResolvedExternalReferenceLink
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord


class WorkflowState(TypedDict, total=False):
    """파이프라인 1회 실행의 누적 state.

    각 키 = 1 step 만 반환 — LangGraph default replace 로 충분.
    occurrence list는 raw 발생을 보존하고, 제품 identity 중복 제거와 exact-once 검증은
    최종 ProductWritePlan builder가 소유한다.

    ``operator.add`` reducer 키 (여러 step 이 나눠 누적 반환):
    - ``call_sites`` / ``import_refs`` — step2 가 파일별 partial 로 여러 dict 반환.
    - ``reads_writes_edges`` — 링킹 여러 step(결정론 + 잔여 루프 / DML + catalog)이 누적.
    그 밖 키는 1 step 이 누적 list 를 한 번에 반환 (default replace 로 충분).
    """
    # ── 실행 메타 ─────────────────────────────────
    run: AnalysisRun

    # ── step1_parse_ddl 결과 ──────────────────────
    schemas: list[Schema]
    tables: list[Table]
    columns: list[Column]
    has_column_edges: list[HasColumn]
    fk_edges: list[Fk]

    # ── step2_load_ast 결과 ───────────────────────
    code_nodes: list[CodeNode]
    modules: list[Module]
    packages: list[Package]
    variables: list[Variable]
    constants: list[Constant]
    data_types: list[DataType]
    parent_of_edges: list[ParentOf]
    # 구문 → 다음 구문 (DBMS 실행 흐름).
    statement_next_edges: list[StatementNext]
    references_edges: list[References]
    init_by_edges: list[InitBy]
    # step2 가 파일별 partial 로 여러 dict 반환 → 누적 필요 (다른 키는 한 번에 반환).
    call_sites: Annotated[list[dict], operator.add]
    callable_facts: list[dict]
    parameter_facts: list[dict]
    binding_target_facts: list[dict]
    scope_directive_facts: list[dict]
    structural_expression_facts: list[dict]
    semantic_expression_facts: list[dict]
    resolved_expression_facts: list[dict]
    macro_facts: list[dict]
    import_refs: Annotated[list[dict], operator.add]
    import_resolution_ledger: list[dict]
    # Parser common evidence: exact decoded source once/file + kind completeness ledger.
    evidence_sources: list[dict]
    evidence_completeness: list[dict]
    preprocessing_contexts: list[dict]

    # ── step3_load_external + step5_linking 결과 ──
    external_source_files: list[ExternalSourceFile]
    external_source_records: list[ExternalSourceRecord]
    resolved_external_reference_links: list[ResolvedExternalReferenceLink]
    external_reference_ledger: dict

    # ── step4_scan_project 결과 (Framework 만) ─
    scan_result: dict                     # ScanResult Pydantic → dict

    # ── step5_linking 결과 ────────────────────────
    # 여러 step 이 누적 반환 — framework: 결정론(stage1) + 잔여 패턴 루프(stage3) /
    # dbms: DML 매칭(stage1) + catalog lineage(step8). reducer가 없으면 뒤 step이
    # 앞 엣지를 통째 덮어써 step6(build_context)가 불완전한 R/W를 LLM에 전달한다.
    # → ``operator.add``로 누적한다. step6 소비와 최종 제품 builder가 각 계약 key를 검증한다.
    reads_writes_edges: Annotated[list[ReadsWrites], operator.add]
    calls_edges: list[Calls]
    call_resolution_ledger: list[dict]
    # 잔여 루프가 결정론 단계의 raw 를 '최종 잔여'로 덮는 게 의도 — default replace 유지.
    unresolved_cases: list[dict]

    # ── step6_build_context 결과 ──────────────────
    analysis_contexts: dict[str, AnalysisContext]   # code_id → ctx

    # ── 데이터소스 발견 산출(step1 discover_datasource — 옵션·전략 독립) ───
    # catalog sample-context로 받은 테이블별 실제 샘플행 — step7 분석 입력값.
    # {table_fqn: {columns, sample_rows}}. 토글 off / datasource 미지정 / 미매칭 → 키 없음.
    table_sample_data: dict[str, dict]
    data_object_resolution_metrics: dict[str, int | float]

    # ── step7 Rule 결과 (analyze_code 함수 단위 누적) ──
    # 구조화된 Rule과 관련 엣지.
    # Rule 노드 id = "<function id>::R<anchor>"이며 anchor 좌표가 정체성이다.
    # Rule builder가 owner·anchor·ordinal identity의 누락·중복을 DB 쓰기 전에 검증한다.
    rules: list[Rule]
    has_rule_edges: list[HasRule]
    rule_table_edges: list[ReadsWrites]

    # ── step8 결정론 후처리 결과 ────────────────────
    # routine node에 직접 저장할 ID/vector 쌍.
    routine_embeddings: list[dict]
