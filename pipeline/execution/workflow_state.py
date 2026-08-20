"""WorkflowState — LangGraph StateGraph 의 state.

설계 원칙
─────────
- **strategy 무관** (Framework / DBMS 같은 state). 분기 0개.
- 각 state 키 = **1 step 만 반환** — LangGraph default replace 로 충분.
  새 step 추가도 키 책임 단일하면 reducer 불필요.
- 모든 step 의 입출력은 이 state 의 키.

dedup / 단일 진실
────────────────
list 안 raw 중복은 **Neo4j MERGE Cypher 가 자동 dedup** (그래프 진실 보장자).
state / artifact 의 list 가 raw 매치 그대로여도 분석 흐름엔 영향 0 — 다른 step 이
``set`` 으로 변환해 사용 (예: ``build_context_step`` 의 reads/writes set).

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
    AffectsTable,
    BelongsTo,
    Calls,
    Fk,
    HasColumn,
    HasExample,
    HasMember,
    HasQuestion,
    HasRule,
    InitBy,
    ParentOf,
    ReadsWrites,
    References,
    StatementNext,
    ExternalEdge,
)
from graph.schema.graph_nodes import (
    Column,
    Constant,
    DataType,
    Example,
    CodeNode,
    Module,
    Package,
    Question,
    Rule,
    Schema,
    Table,
    ObjectReference,
    Variable,
    ExternalChunk,
    ProjectFile,
)


class WorkflowState(TypedDict, total=False):
    """파이프라인 1회 실행의 누적 state.

    각 키 = 1 step 만 반환 — LangGraph default replace 로 충분.
    list 중복은 Neo4j MERGE 가 dedup (그래프 진실 보장자), 분석 흐름은 set 변환으로
    자동 dedup — state 자체는 raw list 그대로 OK.

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
    object_references: list[ObjectReference]
    columns: list[Column]
    has_column_edges: list[HasColumn]
    # BELONGS_TO 는 두 step 이 누적: step1(Table→Schema) + step2(그릇 FILE/CLASS→Package).
    # 끝점 종류만 다른 같은 "소속" 관계 — default replace 면 step2 가 step1 을 덮어쓰므로
    # ``operator.add`` 로 누적 (reads_writes_edges 와 동일 패턴). 중복은 Neo4j MERGE 가 dedup.
    belongs_to_edges: Annotated[list[BelongsTo], operator.add]
    fk_edges: list[Fk]

    # ── step2_load_ast 결과 ───────────────────────
    code_nodes: list[CodeNode]
    modules: list[Module]
    packages: list[Package]
    variables: list[Variable]
    constants: list[Constant]
    data_types: list[DataType]
    parent_of_edges: list[ParentOf]
    # 그릇 → 멤버 (루틴/변수/상수/struct 멤버). 멤버 종류는 노드 라벨로 구분.
    has_member_edges: list[HasMember]
    # 구문 → 다음 구문 (DBMS 실행 흐름).
    statement_next_edges: list[StatementNext]
    references_edges: list[References]
    init_by_edges: list[InitBy]
    # step2 가 파일별 partial 로 여러 dict 반환 → 누적 필요 (다른 키는 한 번에 반환).
    call_sites: Annotated[list[dict], operator.add]
    macro_facts: list[dict]
    import_refs: Annotated[list[dict], operator.add]
    # Parser common evidence: exact decoded source once/file + kind completeness ledger.
    evidence_sources: list[dict]
    evidence_completeness: list[dict]
    preprocessing_contexts: list[dict]

    # ── step3_load_external + step5_linking 결과 (spec 078) ──
    project_files: list[ProjectFile]
    external_chunks: list[ExternalChunk]
    external_edges: Annotated[list[ExternalEdge], operator.add]

    # ── step4_scan_project 결과 (Framework 만) ─
    scan_result: dict                     # ScanResult Pydantic → dict

    # ── step5_linking 결과 ────────────────────────
    # 여러 step 이 누적 반환 — framework: 결정론(stage1) + 잔여 패턴 루프(stage3) /
    # dbms: DML 매칭(stage1) + catalog lineage(step8). reducer가 없으면 뒤 step이
    # 앞 엣지를 통째 덮어써 step6(build_context)가 불완전한 R/W를 LLM에 전달한다.
    # → ``operator.add``로 누적 (중복은 Neo4j MERGE·step6 set 변환이 자동 dedup).
    reads_writes_edges: Annotated[list[ReadsWrites], operator.add]
    calls_edges: list[Calls]
    call_resolution_ledger: list[dict]
    # 잔여 루프가 결정론 단계의 raw 를 '최종 잔여'로 덮는 게 의도 — default replace 유지.
    unresolved_cases: list[dict]

    # ── step6_build_context 결과 ──────────────────
    analysis_contexts: dict[str, AnalysisContext]   # code_id → ctx

    # ── 데이터소스 발견 산출 (step1 discover_datasource — 옵션·전략독립, spec 107) ───
    # catalog sample-context로 받은 테이블별 실제 샘플행 — step7 분석 입력값.
    # {table_fqn: {columns, sample_rows}}. 토글 off / datasource 미지정 / 미매칭 → 키 없음.
    table_sample_data: dict[str, dict]
    data_object_resolution_metrics: dict[str, int | float]

    # ── step7 Example Mapping 결과 (analyze_code 함수 단위 누적) ──
    # 비즈니스 로직 3가지 (rules/examples/questions) + 관련 엣지 6종.
    # Rule 노드 id = "<function id>::R<anchor>" (anchor 좌표 정체성, spec 116).
    # 중복은 Neo4j MERGE Cypher 가 id 키로 dedup (rule / example / question 공통).
    rules: list[Rule]
    examples: list[Example]
    questions: list[Question]
    has_rule_edges: list[HasRule]
    has_example_edges: list[HasExample]
    has_question_edges: list[HasQuestion]
    affects_table_edges: list[AffectsTable]

    # step8 build_components는 합성 노드를 만들지 않고 FUNCTION.component_id 속성만 SET — state 누적 없음.
