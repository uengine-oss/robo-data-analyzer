"""그래프 노드 dataclass — Framework / DBMS 양쪽이 공유.

설계 원칙
─────────
- Framework 와 DBMS 양쪽이 **같은 dataclass** 사용 (분기 0개).
- DBMS 의 nested DML range 도 ``Function`` 으로 흡수 (``parent_id`` 필드로 트리 표현).
- Neo4j 에 박힐 때는 ``shared/neo4j/nodes/<type>.py`` 의 Cypher 쿼리가 변환.

정체성(식별자) 규칙 — plan §4
─────────────────────────────
- 코드 노드(Module/Package/Function/Variable/Constant/DataType): 키 = ``<타입>_id``.
  다른 노드를 가리키는 필드는 **참조 대상 노드의 키 이름 그대로** (소속 모듈 = ``module_id``).
- DB 노드(Schema/Table/Column): 키 = ``fqn`` (``"db.schema.table.column"``).
- ``function_id`` / ``module_id`` 가 곧 그 노드의 fqn 역할 — 별도 ``fqn`` 필드 없음.

노드 종류(antlr 원시 타입)는 Neo4j 라벨로만 표현 — 별도 ``node_type`` 속성 없음.
라벨은 antlr ``type`` → ontology 가 도출 (Framework: ``:FUNCTION:METHOD`` 멀티라벨 /
DBMS: ``:FUNCTION`` 단일 라벨).

새 노드 종류 추가 시 손대는 곳
─────────────────────────────
1. 이 파일에 dataclass 추가
2. ``shared/neo4j/nodes/<new>.py`` 에 Cypher 쿼리 추가
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── DB 객체 (DDL 파싱에서만 생성, 이후 단계는 신규 생성 X) ──

@dataclass(frozen=True)
class Schema:
    """DB 스키마 (예: Oracle SCOTT, PostgreSQL public).

    DDL 파싱 step (parse_ddl) 에서만 생성. 이후 단계는 신규 Schema 생성 X.
    """
    db: str                                  # datasource 이름 (예: "prod_main")
    name: str                                # 스키마 이름 (예: "PUBLIC")


@dataclass(frozen=True)
class Table:
    """DB 테이블.

    DDL 파싱에서만 생성 (Plan §3.1: DDL = 진실 기준). 이후 단계는 R/W 엣지만 추가.

    description       = ``COMMENT ON`` 우선·없으면 ``--`` 주석 / 카탈로그 (출처 = description_source).
    comment           = 테이블 위 ``--`` / ``/* */`` 주석 원문 — 항상 보존 (분석 입력 단서).
    analyzed_description = analyze_function 3분할이 합성한 설명 (분석 결과, 항상 덮어씀).
    """
    db: str
    schema: str
    name: str
    description: str = ""                    # COMMENT ON·-- 주석·카탈로그 1차 설명
    description_source: str = ""             # "ddl" / "source" / "" — 유효 설명이 어느 칸인지
    comment: str = ""                        # -- /* */ 주석 원문 (COMMENT ON 없으면 description 출처)
    analyzed_description: str = ""           # 분석 LLM 합성 설명
    table_type: str = "TABLE"                # TABLE / VIEW / MATERIALIZED_VIEW

    @property
    def fqn(self) -> str:
        """``db.schema.name`` — Neo4j 단일 식별 키."""
        return f"{self.db}.{self.schema}.{self.name}"


@dataclass(frozen=True)
class Column:
    """DB 컬럼.

    DDL 파싱에서만 생성. fqn = ``"<db>.<schema>.<table>.<column>"`` — Neo4j unique key.

    description       = ``COMMENT ON`` 우선·없으면 ``--`` 주석 1차 설명 (출처 = description_source).
    comment           = 컬럼 옆 ``--`` / ``/* */`` 주석 원문 — 항상 보존 (분석 입력 단서).
    analyzed_description = analyze_function 3분할 tableInsights 가 합성한 설명.
    """
    fqn: str                                 # "prod_main.PUBLIC.USERS.user_id"
    name: str                                # "user_id"
    dtype: str                               # "VARCHAR(32)" 등
    nullable: bool = True
    is_primary_key: bool = False
    is_unique: bool = False
    default_value: Optional[str] = None
    description: str = ""                    # COMMENT ON·-- 주석 1차 설명
    description_source: str = ""             # "ddl" / "source" / "" — 유효 설명이 어느 칸인지
    comment: str = ""                        # -- /* */ 주석 원문 (COMMENT ON 없으면 description 출처)
    analyzed_description: str = ""           # 분석 LLM 합성 설명


# ── 코드 객체 (load_ast 가 antlr JSON 에서 생성) ──

@dataclass(frozen=True)
class Package:
    """패키지 / 폴더 — Module 들의 상위 컨테이너.

    파일이 든 폴더로 도출 — 전 언어 공통 (Java ``packageName`` 도 폴더 경로와 일치).
    최상위 파일은 Package 없음.
    """
    package_id: str                          # 고유 식별자 = 폴더 경로 (예: "src.order")
    name: str                                # 마지막 세그먼트 (예: "order")


@dataclass(frozen=True)
class Module:
    """모듈 / 클래스 — Function 들의 컨테이너.

    Framework 예: Java class / Python module / C file.
    DBMS 예: PL/SQL package · 스크립트 파일.

    ``start_line`` / ``end_line`` 으로 한 파일 안 여러 클래스(Java inner class 등)의
    라인 범위 구분. ``intent`` 는 module_pre_analysis(M9 — 헤더 주석 정제),
    ``summary`` / ``stereotype`` / ``logical_name`` 은 summarize_module 이 채운다
    (표시명은 헤더가 아니라 분석 결과에서 도출).
    """
    module_id: str                           # 고유 식별자 = 이 모듈의 fqn (예: "com.example.OrderService")
    name: str
    file_path: str
    sub_label: str = ""                      # Neo4j sub-label — reader 가 ontology 로 도출 (Framework CLASS 등 / 없으면 빈값)
    start_line: int = 0                      # 0 = 파일 전체 (file-level module)
    end_line: int = 0                        # 0 = 파일 전체

    # antlr JSON 풍부 필드 (load_ast 가 채움) — module_pre_analysis(M9) 의 LLM 입력 ────
    comment: str = ""                        # 모듈/파일 헤더 주석 (변경이력·작성자 노이즈 포함 — 원본)
    annotations: str = ""                    # 클래스 어노테이션 (Java @Annotation 등)

    # 분석 결과 ────
    summary: str = ""                        # summarize_module 가 채움
    stereotype: str = ""                     # summarize_module 가 채움 (Function.stereotype 과 명칭 통일)
    logical_name: Optional[str] = None       # 짧은 도메인 표시명 — summarize_module 가 채움
    intent: Optional[str] = None             # 모듈 의도 1~3문장 — module_pre_analysis(M9)


@dataclass
class Function:
    """함수 / 메서드 / 프로시저 — 분석의 기본 단위.

    Framework 예: Java method / Python function / C function.
    DBMS 예: PL/SQL procedure / function / nested DML range (``parent_id`` 로 트리).

    Note: ``frozen=False`` — ``analyze_function`` 이 in-place 로 ``summary`` 등 채워야 함.
    """
    function_id: str                         # 고유 식별자 = fqn 역할 (Framework: "module_id.name" / DBMS: "file:proc:line")
    signature: str                           # "save(Order order) -> OrderResult"
    file_path: str                           # 절대 또는 프로젝트 상대 경로
    start_line: int
    end_line: int

    sub_label: str = ""                      # Neo4j sub-label — reader 가 ontology 로 도출 (Framework METHOD 등 / DBMS·C 빈값)
    # 소속 (정체성 규칙 — 참조 필드 = 대상 노드 키 이름) ────
    module_id: str = ""                      # 소속 Module 의 module_id
    package_id: str = ""                     # 소속 Package 의 package_id (파일이 든 폴더; 최상위면 빈 값)
    parent_id: Optional[str] = None          # 트리 부모 (DBMS nested range 흡수 — Framework 는 거의 None)

    # antlr JSON 풍부 필드 (load_ast 가 채움) ────
    annotations: str = ""                    # 데코레이터 / @Annotation
    comment: str = ""                        # 함수 주석 / Javadoc
    return_type: str = ""                    # 반환 타입
    params: str = ""                         # 파라미터 목록 텍스트

    # 분석 결과 (analyze_function 이 채움) ────
    summary: Optional[str] = None            # 4 섹션 마크다운
    stereotype: Optional[str] = None         # ontology_roles.yaml 풀에서
    logical_name: Optional[str] = None       # 짧은 도메인 토큰

    # strategy 별 확장 필드 (dict — 새 필드 추가 시 base 무변경) ────
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Variable:
    """변수 — 필드 / 멤버 / 전역변수 (선언).

    antlr JSON 의 VARIABLE / FIELD / MEMBER / GLOBAL_VARIABLE / PARAMETER 등.
    """
    variable_id: str                         # 고유 식별자
    module_id: str                           # 소속 Module
    name: str
    file_path: str
    start_line: int
    end_line: int
    var_type: str = ""                       # 선언 타입
    comment: str = ""
    annotations: str = ""
    init_value: str = ""                     # 초기화식
    sub_label: str = ""                      # Neo4j sub-label — reader 가 ontology 로 도출


@dataclass(frozen=True)
class Constant:
    """상수 — ``#define`` / ``static final`` / ``CONSTANT_FIELD`` 등."""
    constant_id: str                         # 고유 식별자
    module_id: str                           # 소속 Module
    name: str
    file_path: str
    start_line: int
    end_line: int
    value: str = ""                          # 상수 값
    var_type: str = ""
    comment: str = ""
    sub_label: str = ""                      # Neo4j sub-label — reader 가 ontology 로 도출


@dataclass(frozen=True)
class DataType:
    """데이터 타입 — C struct / union / enum 등 (Java enum 포함)."""
    datatype_id: str                         # 고유 식별자
    module_id: str                           # 소속 Module
    name: str
    file_path: str
    start_line: int
    end_line: int
    sub_label: str = ""                      # Neo4j sub-label — reader 가 ontology 로 도출
    comment: str = ""


# ── 아키텍트 청크 노드 (linking 이 생성) ──

@dataclass(frozen=True)
class ArchitectChunk:
    """구조화 파일(XML mapper / YAML / properties 등)의 키-값 청크.

    키 하나 + 그 값 블록. 종류(sql / config / 설정)를 따지지 않는 generic 노드.
    linking(step4)이 — **코드가 그 키를 실제로 참조하는 청크만** — 노드화한다
    (안 쓰이는 키는 버림). 값 본문(body)은 step6 3분할이 의미 분석.
    """
    chunk_key: str                           # 키 (예: "<namespace>.<id>") — Neo4j unique key
    body: str                                # 값 텍스트 (step6 분석 대상)
    file_path: str = ""                      # 출처 구조화 파일 경로


# ── Example Mapping 노드 (analyze_function 3분할 결과 → EM 빌더가 생성) ──

@dataclass(frozen=True)
class Rule:
    """비즈니스 규칙 — 같은 statement 는 함수 간 1 노드 공유.

    키 = ``rule_hash`` (정규화 statement MD5). per-function 정보(local rule_id /
    flow_id / branch 등)는 노드가 아닌 ``HAS_RULE`` 엣지 속성에 (노드 공유라서).
    """
    rule_hash: str                           # 정규화 statement MD5[:12] — Neo4j unique key
    statement: str                           # 비즈니스 규칙 문장


@dataclass(frozen=True)
class Example:
    """Rule 의 given/when/then 시나리오 (Example Mapping)."""
    example_id: str                          # "<function_id>::<rule_id>::<idx>" — unique key
    description: str = ""
    given: str = ""                          # JSON 직렬화 (narrative/inputs/preconditions)
    when: str = ""                           # 자연어 한 줄
    then: str = ""                           # JSON 직렬화 (return/exception/reads/writes 등)
    is_boundary: bool = False                # 경계 케이스 여부


@dataclass(frozen=True)
class Question:
    """코드만으로 답 안 되는 모호점 — 사람 검토 큐 (Example Mapping)."""
    question_id: str                         # "<function_id>::Q<idx>" — unique key
    text: str                                # 한 줄 질문
    reason: str = ""                         # 코드 근거 부재 사유
