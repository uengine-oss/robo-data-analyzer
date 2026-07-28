"""그래프 엣지 dataclass — Framework / DBMS 양쪽이 공유.

설계 원칙
─────────
- Framework / DBMS 양쪽이 같은 dataclass 사용 (분기 0개).
- DBMS 만의 PARENT_OF 도 공통 엣지로 포함 (CodeNode 의 nested 표현).
- Neo4j 에 박힐 때는 ``graph/edges/<type>.py`` 의 Cypher 쿼리가 변환.

endpoint 필드 = 가리키는 노드의 ``id`` (관계 역할명)
────────────────────────────────────────────────────
끝점은 관계 역할로 부른다 — 호출 ``caller_id``/``callee_id``, 트리·포함 ``parent_id``/
``child_id``(parent=컨테이너), 그 외 ``source_id``/``target_id``. 값은 그 노드의 ``id``.
같은 끝점 쌍이 raw 중복 들어와도 **Neo4j MERGE 가 자동 dedup** — evidence_* 같은 부속
메타는 정체성 무관.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Union


# ── 공용 헬퍼 — dml_type 표준화 (정체성 외부) ────────────────

def derive_dml_type(standard_op: Optional[str]) -> str:
    """``standard_op`` (SELECT/INSERT/UPDATE/DELETE/MERGE) → ``dml_type`` 문자열.

    None / 빈값 / 정규화 실패 시 빈 문자열. ReadsWrites 만드는 모든 지점이
    공유 — 비표준 op 박는 사고 차단 (단일 진실).
    """
    if not standard_op:
        return ""
    v = standard_op.strip().upper()
    if v in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
        return v
    return ""


# ── 스키마 구조 엣지 (DDL 파싱에서만 생성) ──────────────────

@dataclass(frozen=True)
class HasColumn:
    """Table → Column (1:N). parent=table, child=column."""
    parent_id: str                           # table id "db.schema.table"
    child_id: str                            # column id "db.schema.table.column"


@dataclass(frozen=True)
class BelongsTo:
    """네임스페이스 소속. child → parent(네임스페이스).

    DDL: Table → Schema (child=table, parent=schema "db.schema").
    AST: 그릇(FILE/CLASS) → Package (child=그릇 id, parent=폴더/패키지 id).
    """
    child_id: str
    parent_id: str


@dataclass(frozen=True)
class Fk:
    """Table → Table (외래키).

    DDL 에서만 생성. ``fk_type`` 은 sqlglot 분석 결과.
    ``is_candidate=True`` 는 DDL 에 명시되지 않은 추론된 FK (사람 검토 큐).
    """
    from_id: str                             # 출발 table id
    to_id: str                               # 도착 table id
    from_column: str
    to_column: str
    fk_type: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"
    is_candidate: bool = False


# ── CodeNode ↔ Table (Linking 결과) ────────────────────────

@dataclass(frozen=True)
class ReadsWrites:
    """CodeNode → Table (R/W 엣지). source=function, target=table.

    Linking 1 단계 결정론 grep / DBMS DML 매칭 / Linking Agent (stage3) 가 생성.

    정체성 = (source_id, target_id, relation). dml_type 과 evidence_* 는 부속 메타 —
    같은 코드노드가 같은 테이블을 같은 동작으로 여러 라인에서 호출해도 그래프엔 1 엣지
    (Neo4j MERGE 가 dedup). dml_type 은 ``derive_dml_type(standard_op)`` 헬퍼로 생성.
    """
    source_id: str
    target_id: str
    relation: Literal["READS", "WRITES"]
    dml_type: str = ""                       # SELECT/INSERT/UPDATE/DELETE/MERGE — relation 보다 세분 (DBMS)
    evidence_file: str = ""                  # 발견된 파일
    evidence_line: int = 0                   # 발견된 라인


def normalize_relation(value: str) -> Optional[Literal["READS", "WRITES"]]:
    """R/W 표기 정규화 — ``READ``/``READS`` → ``READS``, ``WRITE``/``WRITES`` → ``WRITES``.

    대소문자 무시. 그 외 값은 ``None`` — 호출자가 기본값 적용·skip 을 결정한다.
    ``ReadsWrites`` 를 만드는 여러 곳(정규식 링킹·catalog 리니지)이 공유.
    """
    v = value.strip().upper()
    if v in ("READ", "READS"):
        return "READS"
    if v in ("WRITE", "WRITES"):
        return "WRITES"
    return None


# ── CodeNode ↔ CodeNode (호출 / 부모-자식) ─────────────────

@dataclass(frozen=True)
class Calls:
    """CodeNode → CodeNode (호출 관계).

    Framework: 결정론 분류 (link_calls_by_name) — caller/callee = 함수.
    DBMS: AST CALL 노드 → 대상 (link_calls_by_ast) — caller = CALL 노드, 빌트인 드롭·재귀 보존.
    프로시저 단위 호출은 저장하지 않고 ``call_linker.routine_call_pairs`` 로 파생.

    scope:
    - ``internal``: 같은 코드베이스 안 코드노드 — 콜리 정의 노드가 이미 존재.
    - ``external``: 분석 범위 밖 실 루틴/프로시저 (예: 자기 스키마의 미분석 프로시저,
      다른 스키마/라이브러리). 콜리 정의 노드가 없어 ``upsert_calls`` 가
      ``:FUNCTION:EXTERNAL`` stub 노드를 모듈·스키마와 함께 internal 과 대칭으로 생성.

    external 분해 메타 (``callee_*``) — strategy 별 linker 가 **언어 의미**로 채운다
    (DBMS: module=프로시저·package=스키마, Framework: module=클래스·package=패키지).
    internal 은 콜리 노드가 이미 있어 빈 값.

    정체성 = (caller_id, callee_id). scope·callee_* 는 부속 — 같은 호출이 두 번 분류돼도 그래프엔 1 엣지.
    """
    caller_id: str
    callee_id: str
    scope: Literal["internal", "external"] = "internal"
    callee_module: str = ""                  # external 콜리의 모듈 id (DBMS=프로시저명 / FW=클래스)
    callee_package: str = ""                 # external 콜리의 패키지 id — 스키마(DBMS) / 패키지(FW)
    callee_name: str = ""                    # external 콜리 표시명
    recursive: bool = False                  # 원문 호출이 자기 루틴을 직접 지목했을 때만 True


@dataclass(frozen=True)
class ParentOf:
    """CodeNode → nested CodeNode. parent=상위, child=중첩.

    DBMS 의 PL/SQL procedure 안 SELECT/IF/CASE 같은 분석 단위가 parent 의 child.
    Framework 는 거의 안 씀 (메서드는 평면 구조).

    Bottom-up DAG 알고리즘은 ``Calls`` / ``ParentOf`` 둘 다 같은 그래프 입력으로 처리.
    """
    parent_id: str
    child_id: str


# 컴포넌트 멤버십/그룹간 호출은 엣지로 저장하지 않는다 — 멤버십은 FUNCTION.component_id 속성,
# 그룹↔그룹 호출은 CALLS + component_id 로 온디맨드 GROUP BY (build_components_step 참고).


# ── 코드 구조 엣지 (load_ast 가 antlr JSON 트리에서 생성) ──

@dataclass(frozen=True)
class HasMember:
    """그릇 → 멤버 ("가지다"). parent=그릇, child=멤버.

    그릇 = FILE / CLASS / PACKAGE / DataType. 멤버 = 루틴(PROCEDURE/FUNCTION/METHOD/
    TRIGGER) · 데이터(VARIABLE/CONSTANT/FIELD/PARAMETER) · struct 멤버변수.
    멤버 종류는 **노드 라벨**로 구분 (관계는 범용). 일반 구문(SELECT/IF…)은 멤버가
    아니라 루틴 본문의 일부 → HAS_MEMBER 아님, PARENT_OF(중첩)+NEXT(흐름) 로만.
    """
    parent_id: str
    child_id: str


@dataclass(frozen=True)
class StatementNext:
    """구문 → 다음 구문 (DBMS 실행 흐름). prev=이전 구문, next=다음 구문.

    같은 부모 아래 형제 구문을 등장(실행) 순서로 연결 — 절차적 로직 흐름 추적.
    rel 타입(NEXT)은 다른 소비처와 공유하나 끝점이 구문 노드라 라벨로 구분.
    Framework 는 구문 단위 흐름 개념 없음 → 생성 안 함.
    """
    prev_id: str
    next_id: str


# ── 코드 참조 엣지 (결정론 식별자 매칭) ────────────────────

@dataclass(frozen=True)
class References:
    """CodeNode → Variable / Constant (본문이 참조하는 변수·상수). source=function, target=var/const.

    MATCH 는 ``:ANALYZER_OWNED`` + ``id`` (변수/상수 종류 라벨이 열린집합이라 마커로 시크).
    정체성 = (source_id, target_id). evidence_line 은 부속.
    """
    source_id: str
    target_id: str
    evidence_line: int = 0


@dataclass(frozen=True)
class InitBy:
    """변수/상수 → 출처 노드 (정의 시 참조하는 다른 변수·상수).

    의미 = "정의 시 외부 참조" — ``=`` 우변뿐 아니라 변수 size 표현식
    (``static char x [LEN +1]``) · 상수 식 (``#define B (A+1)``) 등 모두 포함.
    Python 쪽이 source 라인을 직접 읽어 토큰 추출하므로 antlr 가 못 준 의존도 잡힌다.

    MATCH 는 ``:ANALYZER_OWNED`` + ``id`` (종류 라벨 무관).
    """
    from_id: str                              # 출발: Variable 또는 Constant id
    source_id: str


# ── Example Mapping 엣지 (analyze_code 3분할 → EM 빌더가 생성) ──

@dataclass(frozen=True)
class HasRule:
    """CodeNode → shared Rule occurrence.

    ``occurrence_id`` is function-scoped because the same statement can appear
    more than once in one routine. The Rule node remains meaning-deduplicated.
    """
    source_id: str
    target_id: str
    occurrence_id: str = ""
    ordinal: int = 0
    anchor_line: int = 0


@dataclass(frozen=True)
class HasExample:
    """Rule → Example. source=rule, target=example."""
    source_id: str
    target_id: str
    occurrence_id: str = ""
    ordinal: int = 0


@dataclass(frozen=True)
class HasQuestion:
    """CodeNode → Question. source=function, target=question."""
    source_id: str
    target_id: str


@dataclass(frozen=True)
class AffectsTable:
    """Example → Table. R/W 필터가 확정한 Table ID를 끝점까지 보존한다."""
    source_id: str
    target_id: str
    op: str


# ── External-file relationships (spec 078) ──────────────────────────

def _validate_external_edge(source_id: str, target_id: str, evidence_line: int | None = None) -> None:
    if not source_id.strip() or not target_id.strip():
        raise ValueError("external relationship endpoints must be non-empty")
    if evidence_line is not None and (
        isinstance(evidence_line, bool) or not isinstance(evidence_line, int) or evidence_line < 0
    ):
        raise ValueError("evidence_line must be a non-negative integer")


@dataclass(frozen=True)
class ReferTo:
    """METHOD/FUNCTION → EXTERNAL_CHUNK | PROJECT_FILE: verified exact occurrence.

    ``matched_key`` is the chunk key, file name or path suffix that the code's
    determined string equalled; the target is a chunk when a key pinned one
    chunk, and a file when the string named the file itself (spec 081).
    ``via_symbol`` names the constant the value was folded from; empty for a
    direct literal.
    The relation asserts occurrence only — never call/consumption semantics.
    """
    source_id: str
    target_id: str
    matched_key: str
    evidence_file: str = ""
    evidence_line: int = 0
    via_symbol: str = ""

    def __post_init__(self) -> None:
        _validate_external_edge(self.source_id, self.target_id, self.evidence_line)
        if not self.matched_key.strip():
            raise ValueError("REFER_TO requires the matched key")


@dataclass(frozen=True)
class HasChunk:
    source_id: str
    target_id: str

    def __post_init__(self) -> None:
        _validate_external_edge(self.source_id, self.target_id)


ExternalEdge = Union[ReferTo, HasChunk]
