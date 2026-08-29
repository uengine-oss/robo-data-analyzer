"""그래프 노드 dataclass — Framework / DBMS 양쪽이 공유.

설계 원칙
─────────
- Framework 와 DBMS 양쪽이 **같은 dataclass** 사용 (분기 0개).
- DBMS 의 nested DML range 도 ``CodeNode`` 으로 흡수 (``parent_id`` 필드로 트리 표현).
- Neo4j 에 박힐 때는 ``graph/nodes/<type>.py`` 의 Cypher 쿼리가 변환.

정체성(식별자) 규칙
────────────────────
- 모든 노드의 자기 식별 키 = ``id`` 하나 (종류 무관 균일). 값은 노드별 고유 문자열
  (코드 노드 = fqn 형태 / DB 노드 = ``db.schema.name`` / EM 노드 = 해시·합성키).
- 다른 노드를 가리키는 필드는 **관계 역할명** (소속 모듈 = ``owner_id`` /
  트리 부모 = ``parent_id``) — 가리키는 값은 그 노드의 ``id``.

정체성 키 = Neo4j MERGE 가 자동 dedup 책임자
────────────────────────────────────────────
같은 ``id`` 가 state list 에 raw 중복 들어와도 **Neo4j MERGE Cypher 가 자동 dedup**
(그래프 진실 보장자). state 자체는 raw 그대로 — 다른 step 이 set 변환으로 자동 dedup.

노드 종류 = Neo4j 라벨 하나 = 그 노드의 antlr ``type`` (``label`` 필드 = 필수). 별도
``node_type`` 속성 없음. Framework/DBMS 공통 — reader 가 ``node["type"]`` 직결 (역할 라벨 폐기).

새 노드 종류 추가 시 손대는 곳
─────────────────────────────
1. 이 파일에 dataclass 추가 (식별 키 = ``id``)
2. ``graph/nodes/<new>.py`` 에 Cypher 쿼리 추가 (MERGE 키 = ``id``)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from graph.schema.node_ids import sql_schema_id, sql_table_id


# ── DB 객체 (DDL 파싱에서만 생성, 이후 단계는 신규 생성 X) ──

@dataclass(frozen=True)
class Schema:
    """DB 스키마 (예: Oracle SCOTT, PostgreSQL public).

    DDL 파싱 step (parse_ddl) 에서만 생성. 이후 단계는 신규 Schema 생성 X.
    식별 키 ``id`` = ``"db.name"``.
    """
    db: str                                  # datasource 이름 (예: "prod_main")
    name: str                                # 스키마 이름 (예: "PUBLIC")
    name_quoted: bool = False
    catalog: str = ""                        # SQL catalog (datasource namespace와 별개)
    catalog_quoted: bool = False

    @property
    def id(self) -> str:
        """``db:<db>.<name>`` — DB 네임스페이스 (코드 id 와 절대 안 겹침, spec 047)."""
        return sql_schema_id(
            self.db, self.name, self.name_quoted,
            catalog=self.catalog, catalog_quoted=self.catalog_quoted,
        )


@dataclass(frozen=True)
class Table:
    """DB 테이블.

    DDL 파싱 또는 Catalog exact resolver에서 생성한다. Catalog 경로는 실제 metadata로
    확정된 객체만 만들며 미해결 사용 발생은 CodeNode의 실행 장부에만 보존한다.

    description       = 정식 ``COMMENT ON`` / 카탈로그 (출처 = description_source).
    comment           = 테이블 위 ``--`` / ``/* */`` 주석 원문 — 항상 보존 (분석 입력 단서).
    analyzed_description = analyze_code 3분할이 합성한 설명 (분석 결과, 항상 덮어씀).
    """
    db: str
    schema: str
    name: str
    logical_name: str = ""                   # 짧은 도메인 표시명(물리명과 별개) — spec 025, 빈값 미저장
    description: str = ""                    # COMMENT ON·-- 주석·카탈로그 1차 설명
    description_source: str = ""             # "ddl" / "code" / "catalog" / "" — 유효 설명이 어느 칸인지
    comment: str = ""                        # -- /* */ 주석 원문 (COMMENT ON 없으면 description 출처)
    analyzed_description: str = ""           # 분석 LLM 합성 설명
    meaning_status: str = ""                 # sufficient / insufficient
    missing_context_count: int = 0
    missing_context_artifact_hash: str = ""
    table_type: str = "TABLE"                # TABLE / VIEW / MATERIALIZED_VIEW
    view_definition: str = ""                # VIEW 의 정의 SELECT 원문 (spec 112 뷰 펼치기 재료)
    # embed_step 이 채우는 의미 벡터. metadata={"vector": True} = 직렬화 소비자용
    # "dim 만 표시" 계약 표식.
    embedding: tuple[float, ...] = field(default=(), metadata={"vector": True})
    schema_quoted: bool = False               # SQL quoted schema면 exact case identity
    name_quoted: bool = False                 # SQL quoted table/view면 exact case identity
    catalog: str = ""                         # SQL catalog (datasource namespace와 별개)
    catalog_quoted: bool = False
    # CREATE VIEW AST scope가 생산한 physical dependency Table ID. view_definition 문자열은
    # 표시/감사용이고 relationship expansion의 권위 입력으로 재파싱하지 않는다.
    view_dependency_ids: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        """``db:<db>.<schema>.<name>`` — DB 네임스페이스 (코드 id 와 절대 안 겹침, spec 047).

        종전엔 db·schema 가 빈값이면 ``"cart"`` 가 되어 **소스 파일 cart.c 의 id 와 충돌**했다.
        """
        return sql_table_id(
            self.db, self.schema, self.name,
            schema_quoted=self.schema_quoted,
            table_quoted=self.name_quoted,
            catalog=self.catalog,
            catalog_quoted=self.catalog_quoted,
        )


@dataclass(frozen=True)
class Column:
    """DB 컬럼.

    DDL 파싱 또는 Catalog exact resolver에서 생성한다.
    ``id`` = ``"<db>.<schema>.<table>.<column>"`` — Neo4j unique key.

    description       = 정식 ``COMMENT ON`` 1차 설명 (출처 = description_source).
    comment           = 컬럼 옆 ``--`` / ``/* */`` 주석 원문 — 항상 보존 (분석 입력 단서).
    analyzed_description = analyze_code 3분할 tableInsights 가 합성한 설명.
    """
    id: str                                  # "prod_main.PUBLIC.USERS.user_id"
    name: str                                # "user_id"
    dtype: str                               # "VARCHAR(32)" 등
    name_quoted: bool = False                # SQL quoted identifier면 exact case identity
    nullable: bool = True
    is_primary_key: bool = False
    is_unique: bool = False
    default_value: Optional[str] = None
    logical_name: str = ""                   # 짧은 도메인 표시명 — spec 028, enrich_table_description 이 채움
                                             # (Table 과 대칭: 없으면 Neo4j 에만 있고 artifact 엔 빈값 = 검증 사각지대)
    description: str = ""                    # COMMENT ON·-- 주석 1차 설명
    description_source: str = ""             # "ddl" / "code" / "catalog" / "" — 유효 설명이 어느 칸인지
    comment: str = ""                        # -- /* */ 주석 원문 (COMMENT ON 없으면 description 출처)
    analyzed_description: str = ""           # 분석 LLM 합성 설명
    meaning_status: str = ""                 # sufficient / insufficient
    missing_context_count: int = 0
    missing_context_artifact_hash: str = ""
    embedding: tuple[float, ...] = field(default=(), metadata={"vector": True})


# ── 코드 객체 (load_ast 가 antlr JSON 에서 생성) ──

@dataclass(frozen=True)
class Package:
    """패키지 / 폴더 — Module 들의 상위 컨테이너.

    파일이 든 폴더로 도출 — 전 언어 공통 (Java ``packageName`` 도 폴더 경로와 일치).
    최상위 파일은 Package 없음.
    """
    id: str                                  # 고유 식별자 = 폴더 경로 (예: "src.order")
    name: str                                # 마지막 세그먼트 (예: "order")


@dataclass(frozen=True)
class Module:
    """모듈 / 클래스 — CodeNode 들의 컨테이너.

    Framework 예: Java class / Python module / C file.
    DBMS 예: PL/SQL package · 스크립트 파일.

    ``start_line`` / ``end_line`` 으로 한 파일 안 여러 클래스(Java inner class 등)의
    라인 범위를 구분한다. 함수 의미 분석이 끝나면 Analyzer는 검증된 함수 요약을
    ``summary_rollup_*`` 두 배열에 같은 순서로 복제한다. Module 자체의 새 요약이나
    역할을 추측하지 않는다.
    """
    id: str                                  # 고유 식별자 = 이 모듈의 fqn (예: "com.example.OrderService")
    name: str
    file_path: str
    label: str                               # Neo4j 라벨 = 자기 antlr type (필수, FILE / CLASS / INTERFACE …)
    start_line: int = 0                      # 0 = 파일 전체 (file-level module)
    end_line: int = 0                        # 0 = 파일 전체

    # antlr JSON 풍부 필드 (load_ast 가 채움) ─────────────────────────────
    comment: str = ""                        # 모듈/파일 헤더 주석 (변경이력·작성자 노이즈 포함 — 원본)
    annotations: str = ""                    # 클래스 어노테이션 (Java @Annotation 등)
    code_text: str = ""                      # 원문 슬라이스 (step2 가 채움 — 라인 범위 기준)
    raw_code_text: str = ""                  # 결정론 분석 전용 정확한 원문 (Neo4j 비저장)
    # Parser listener-owned inheritance syntax.  Keep the exact AST fields instead of
    # reparsing one declaration line in Analyzer (multiline declarations are valid).
    extends_type: str = ""
    implements_types: str = ""

    # 결정론적 함수 요약 묶음. 같은 index의 ID와 summary가 한 쌍이다.
    summary_rollup_function_ids: tuple[str, ...] = ()
    summary_rollup_summaries: tuple[str, ...] = ()


@dataclass
class CodeNode:
    """코드노드 / 메서드 / 프로시저 — 분석의 기본 단위.

    Framework 예: Java method / Python function / C function.
    DBMS 예: PL/SQL procedure / function / nested DML range (``parent_id`` 로 트리).

    Note: ``frozen=False`` — ``analyze_code`` 이 in-place 로 ``summary`` 등 채워야 함.
    """
    id: str                                  # 고유 식별자 = fqn 역할 (Framework: "owner_id.name" / DBMS: "file:proc:line")
    signature: str                           # "save(Order order) -> OrderResult"
    file_path: str                           # 절대 또는 프로젝트 상대 경로
    start_line: int
    end_line: int
    label: str                               # Neo4j 라벨 = 자기 antlr type (필수, METHOD / FUNCTION / PROCEDURE / SELECT / IF …)

    name: str = ""                           # antlr node.name (콜러블=루틴명 / 구문=빈값). 표시명.
    code_text: str = ""                      # 본문 원문 슬라이스 (step2 가 채움 — 라인 범위 기준)
    raw_code_text: str = ""                  # 결정론 분석 전용 정확한 원문 (Neo4j 비저장)

    # 소속·트리 (참조 = 대상 노드의 id) ────
    owner_id: str = ""                       # 소속 Module 의 id (HAS_MEMBER 엣지 빌드 + 모듈 그룹핑용 — 노드 속성엔 안 박음)
    package: str = ""                        # 소속 패키지(폴더) id — external 호출 스키마 귀속용 (노드 속성엔 안 박음)
    parent_id: Optional[str] = None          # 트리 부모 (DBMS nested range 흡수 — Framework 는 거의 None)

    # antlr JSON 풍부 필드 (load_ast 가 채움) ────
    annotations: str = ""                    # 데코레이터 / @Annotation
    comment: str = ""                        # 코드노드 주석 / Javadoc
    return_type: str = ""                    # 반환 타입
    params: str = ""                         # 파라미터 목록 텍스트

    # 구조 statement 표현 (parser spec 016 — listener 가 소유한 원문) ────
    expression: str = ""                     # RETURN/THROW/RAISE 식·제어 조건식 원문
    condition_timing: str = ""               # 제어 조건 평가 시점: 빈값/pre 또는 post(do-while)
    target: str = ""                         # ASSIGNMENT 좌변(lvalue) 원문
    operator: str = ""                       # ASSIGNMENT 연산자 원문 (=, +=, := …)

    # 분석 결과 (analyze_code 이 채움) ────
    summary: Optional[str] = None            # 표시·검색·callee handoff 공용 canonical 의미 계약
    stereotype: Optional[str] = None         # ontology_roles.yaml 풀에서
    meaning_status: str = ""                 # sufficient / partial / insufficient
    missing_context_count: int = 0            # 상세 run artifact 항목 수
    missing_context_artifact_hash: str = ""  # count > 0일 때 canonical artifact SHA-256
    embedding: tuple[float, ...] = field(default=(), metadata={"vector": True})

    # strategy 별 확장 필드 (dict — 새 필드 추가 시 base 무변경) ────
    statement_origin: str = ""
    extra: dict = field(default_factory=dict)

    def own_ast_type(self) -> str:
        """이 노드의 antlr 원시 타입 (DBMS nested: ``CALL``/``MERGE``/``TRY``/``IF`` 등).

        DBMS id 는 ``{parent_id}:{TYPE}:{line}`` 로 조립되므로(`antlr_json_reader`)
        부모 id 를 떼면 첫 세그먼트가 **자기 타입**이다. uniqueness 접미사(``:line#n``)에도
        robust — 자손 타입에 오염되지 않는다 (부분문자열 매칭의 컨테이너 오류 차단).
        루트(parent_id None) · Framework(``.`` id) 는 빈 문자열.
        """
        return self.label


# 컴포넌트(부품)는 합성 노드로 만들지 않는다 — Leiden 군집 멤버십은 FUNCTION.component_id
# 속성이 단일 진실(build_components_step). 이름·그룹간 관계는 거기서 파생(시각화=hull, 그룹호출=온디맨드 GROUP BY).


@dataclass(frozen=True)
class Variable:
    """변수 — 필드 / 멤버 / 전역변수 (선언).

    antlr JSON 의 VARIABLE / FIELD / MEMBER / GLOBAL_VARIABLE / PARAMETER 등.
    """
    id: str                                  # 고유 식별자
    owner_id: str                            # 소속 Module 의 id
    name: str
    file_path: str
    start_line: int
    end_line: int
    label: str                               # Neo4j 라벨 = 자기 antlr type (필수, FIELD / MEMBER / GLOBAL_VARIABLE / VARIABLE / PARAMETER …)
    var_type: str = ""                       # 선언 타입
    comment: str = ""
    annotations: str = ""
    init_value: str = ""                     # 초기화식
    code_text: str = ""                      # 원문 슬라이스 (step2 가 채움 — 라인 범위 기준)
    raw_code_text: str = ""                  # 결정론 분석 전용 정확한 원문 (Neo4j 비저장)


@dataclass(frozen=True)
class Constant:
    """상수 — ``#define`` / ``static final`` / ``CONSTANT_FIELD`` 등."""
    id: str                                  # 고유 식별자
    owner_id: str                            # 소속 Module 의 id
    name: str
    file_path: str
    start_line: int
    end_line: int
    label: str                               # Neo4j 라벨 = 자기 antlr type (필수, DEFINE / CONSTANT_FIELD …)
    value: str = ""                          # 상수 값
    var_type: str = ""
    comment: str = ""
    code_text: str = ""                      # 원문 슬라이스 (step2 가 채움 — 라인 범위 기준)
    raw_code_text: str = ""                  # 결정론 분석 전용 정확한 원문 (Neo4j 비저장)


@dataclass(frozen=True)
class DataType:
    """데이터 타입 — C struct / union / enum 등 (Java enum 포함)."""
    id: str                                  # 고유 식별자
    owner_id: str                            # 소속 Module 의 id
    name: str
    file_path: str
    start_line: int
    end_line: int
    label: str                               # Neo4j 라벨 = 자기 antlr type (필수, STRUCT / UNION / ENUM …)
    comment: str = ""
    code_text: str = ""                      # 원문 슬라이스 (step2 가 채움 — 라인 범위 기준)
    raw_code_text: str = ""                  # 결정론 분석 전용 정확한 원문 (Neo4j 비저장)


# ── Example Mapping 노드 (analyze_code 3분할 결과 → EM 빌더가 생성) ──

@dataclass(frozen=True)
class Rule:
    """결정론 구조 규칙 — JSON 봉투 + 파이썬 표현식 leaf (spec 131).

    정체성 ``id`` = ``<function id>::R<anchor>`` — anchor 가 파일 절대 줄이라 함수 내
    유일하다(폐기된 statement 해시 공유 개념을 사용하지 않는다).
    """
    id: str
    anchor_line: int = 0                     # 절대 파일 줄 — 채점·배칭의 단일 좌표
    structural_condition: str = ""           # 원문에서 결정론 생성한 Python 조건
    structural_effects: tuple[str, ...] = () # 원문에서 결정론 생성한 Python 효과
    business_condition: str = ""             # Strategy-B 업무 의미 Python 조건
    business_effects: tuple[str, ...] = ()   # Strategy-B 업무 의미 Python 효과


@dataclass(frozen=True)
class Example:
    """구조화 Rule 검증 시나리오 — 파이썬 leaf (spec 116).

    정체성 ``id`` = ``<rule id>::E<idx>``. given/then 은 파이썬 leaf 목록.
    """
    id: str
    given_conditions: tuple[str, ...] = ()   # 참이어야 하는 업무 조건 목록
    expected_effects: tuple[str, ...] = ()   # 조건이 참일 때 발생할 업무 효과 목록
