"""antlr JSON AST → 코드 노드 + 분석 단서.

antlr-parser 가 만든 ``data/analysis/target/<p>/*.json`` 트리만 읽는다 — 소스 재파싱 X.
Framework / DBMS 공용 단일 reader (언어 해석은 ontology 가 흡수).

생산물 (ParseResult):
- Module / CodeNode / Package / Variable / Constant / DataType 노드
- PARENT_OF (트리 중첩) 엣지
- call_sites (FUNCTION_CALL) · import_refs (INCLUDE/IMPORT) — 노드 아님, 후속 step 단서

노드 라벨 = 자기 antlr ``type`` 하나 (reader 가 ``node["type"]`` 직결, Framework/DBMS 공통).
ontology ``role_of`` 는 분류 전용 — 어느 dataclass/엣지/internal 인지만 정한다 (라벨엔 안 씀).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from graph.schema.graph_edges import (
    BelongsTo,
    HasMember,
    ParentOf,
    StatementNext,
)
from graph.schema.graph_nodes import Constant, DataType, CodeNode, Module, Package, Variable
from analyzer_settings import SETTINGS
from pipeline.languages import ROUTINE_LABELS, role_of
from pipeline.languages import DBMS_EXTS, EXT_TO_LANG
from graph.schema.node_ids import child_id, code_file_id, code_ns_id, positioned
from runtime_events.runtime_logger import log_error, log_warning

_COMPONENT = "pipeline.steps.step2_load_ast.antlr_json_reader"

# DBMS 순수 선언 노드 — Variable 노드로 수집 (CodeNode 아님, 분석 제외).
# ★ CURSOR 정의는 제외 — 쿼리 로직(선별 조건 = 비즈니스 규칙)을 담으므로 CodeNode 로 분석한다
#   (자식 SELECT 요약 → 커서가 톱레벨 문장으로 규칙 추출). DECLARE(skip) 안이라 유효부모=루틴.
_DBMS_VAR_TYPES: frozenset[str] = frozenset({
    "PARAMETER", "VARIABLE", "PACKAGE_VARIABLE",
})

# (옛 ``_DBMS_EXTS`` / ``_ROUTINE_LABELS`` 삭제 — 각각 ``source_extension.DBMS_EXTS`` 와
#  ``ontology.ROUTINE_LABELS`` 가 단일 정의처다. 종전엔 여기와 batch_analyze 가 목록을
#  따로 들어 실제로 갈라졌다: batch_analyze 쪽엔 METHOD 가 빠져 있었다. spec 046 FR-003/FR-005.)


@dataclass
class ParseResult:
    """한 antlr JSON 파일의 노드 / 엣지 / 분석 단서."""
    code_nodes: list[CodeNode] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    packages: list[Package] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    constants: list[Constant] = field(default_factory=list)
    data_types: list[DataType] = field(default_factory=list)
    parent_of_edges: list[ParentOf] = field(default_factory=list)
    has_member_edges: list[HasMember] = field(default_factory=list)          # 그릇 → 루틴/데이터 멤버
    belongs_to_edges: list[BelongsTo] = field(default_factory=list)          # 그릇 → Package(폴더) 소속
    statement_next_edges: list[StatementNext] = field(default_factory=list)  # 구문 → 다음 구문 (DBMS 실행 흐름)
    call_sites: list[dict] = field(default_factory=list)   # FUNCTION_CALL: {call_id, caller_id, call_text, file, line}
    import_refs: list[dict] = field(default_factory=list)  # INCLUDE/IMPORT: {name, file, line}


# ── 파일 진입점 ───────────────────────────────────────────────

def _make_module(
    node: dict, module_id: str, name: str, src_path: str,
    start_line: int, end_line: int,
) -> Module:
    return Module(
        id=module_id, name=name, file_path=src_path,
        label=node["type"], start_line=start_line, end_line=end_line,
        annotations=node.get("annotations") or "",
        comment=node.get("comment") or "",
    )


def _make_code_node(
    node: dict, fid: str, module_id: str, package_id: str,
    src_path: str, parent_fid: str | None,
) -> CodeNode:
    """라벨 = ``node["type"]`` (Framework METHOD/FUNCTION · DBMS PROCEDURE/SELECT/IF 등).

    ``module_id`` (소속 모듈 id) → ``owner_id`` (HAS_MEMBER 빌드·그룹핑).
    ``package_id`` (소속 폴더 id) → ``package`` (external 호출 스키마 귀속, call_linker).
    """
    sl = int(node.get("startLine") or 0)
    return CodeNode(
        id=fid,
        name=node.get("name") or "",          # 표시명 (구문은 "" — 라벨[라인]으로 표시)
        label=node["type"],
        signature=_clean_signature(node.get("signature"))
                  or node.get("name") or node.get("type") or "",
        file_path=src_path,
        start_line=sl,
        end_line=int(node.get("endLine") or sl),
        owner_id=module_id,
        package=package_id,
        parent_id=parent_fid,
        annotations=node.get("annotations") or "",
        comment=node.get("comment") or "",
        return_type=node.get("returnType") or "",
        params=node.get("parameters") or "",
    )


def _make_variable(
    node: dict, vid: str, module_id: str, src_path: str,
) -> Variable:
    sl = int(node.get("startLine") or 0)
    return Variable(
        id=vid,
        owner_id=module_id,
        name=node.get("name") or "",
        file_path=src_path,
        start_line=sl,
        end_line=int(node.get("endLine") or sl),
        var_type=node.get("variableType") or "",
        comment=node.get("comment") or "",
        annotations=node.get("annotations") or "",
        init_value=node.get("initValue") or "",
        label=node["type"],
    )


def _make_constant(
    node: dict, cid: str, module_id: str, src_path: str,
) -> Constant:
    sl = int(node.get("startLine") or 0)
    return Constant(
        id=cid,
        owner_id=module_id,
        name=node.get("name") or "",
        file_path=src_path,
        start_line=sl,
        end_line=int(node.get("endLine") or sl),
        value=node.get("initValue") or "",
        var_type=node.get("variableType") or "",
        comment=node.get("comment") or "",
        label=node["type"],
    )


def _make_data_type(
    node: dict, did: str, module_id: str, src_path: str,
) -> DataType:
    sl = int(node.get("startLine") or 0)
    return DataType(
        id=did,
        owner_id=module_id,
        name=node.get("name") or "",
        file_path=src_path,
        start_line=sl,
        end_line=int(node.get("endLine") or sl),
        label=node["type"],
        comment=node.get("comment") or "",
    )


# ── 헬퍼 ──────────────────────────────────────────────────────

def _node_id(node: dict, ctx: _FwCtx, container_id: str) -> str:
    """Framework 노드 → 고유 id = ``<컨테이너>:<이름>`` (spec 047).

    ``container_id`` 는 **직접 담고 있는 것**이다 — 파일이면 파일 id, 구조체 멤버면
    **구조체 id**. 종전엔 항상 모듈 id 를 넘겨서 구조체 이름이 id 에서 사라졌고,
    그 결과 구조체 필드와 전역변수(같은 이름)가 겹쳤다.

    같은 컨테이너에 같은 이름이 합법인 경우(Java 오버로드)만 ``@<line>`` 접미.
    """
    name = node.get("name") or node.get("type") or ""
    sl = int(node.get("startLine") or 0)
    return _unique_id(child_id(container_id, name), sl, ctx.seen)


def _derive_structure_edges(result: ParseResult, package_id: str) -> None:
    """노드 리스트에서 구조 엣지 도출 — HAS_MEMBER(그릇→멤버) / BELONGS_TO(그릇→폴더).

    HAS_MEMBER = 그릇이 멤버를 가짐 (멤버 종류는 노드 라벨로 구분):
    - 루틴(PROCEDURE/FUNCTION/METHOD/TRIGGER)만 — 일반 구문(SELECT/IF…)·구조 노드는
      멤버가 아니라 루틴 본문의 일부라 제외 (PARENT_OF 중첩 + NEXT 흐름으로만 연결).
    - 데이터(변수/상수). struct 멤버변수는 walk 중 HAS_MEMBER 로 이미 생성됨 → 중복 제외.
    BELONGS_TO = 그릇(모듈/파일/클래스)이 든 폴더(Package) 소속.
    """
    for fn in result.code_nodes:
        if fn.label in ROUTINE_LABELS:
            result.has_member_edges.append(HasMember(fn.owner_id, fn.id))
    member_ids = {e.child_id for e in result.has_member_edges}
    for v in result.variables:
        if v.id not in member_ids:
            result.has_member_edges.append(HasMember(v.owner_id, v.id))
    for c in result.constants:
        result.has_member_edges.append(HasMember(c.owner_id, c.id))
    if package_id:
        for m in result.modules:
            result.belongs_to_edges.append(BelongsTo(m.id, package_id))


def _folder_package(rel_path: str) -> tuple[str, Package | None]:
    """파일 상대경로 → (package_id, Package 노드). 파일이 든 폴더가 곧 Package.

    최상위 파일(폴더 없음)은 ``("", None)``. 예: ``"src/order/Foo.java"`` →
    ``("src.order", Package("src.order", "order"))``.
    """
    parts = [p for p in Path(rel_path).parent.as_posix().split("/") if p and p != "."]
    if not parts:
        return "", None
    package_id = code_ns_id(".".join(parts))
    return package_id, Package(id=package_id, name=parts[-1])


def _src_path(raw: dict, source_root: Path, json_path: Path) -> str:
    """antlr JSON 의 ``filePath`` → 절대 소스 경로 (step2 code_text 슬라이싱용).

    ``filePath`` 가 없으면 폴백하지만, ``fileName`` 은 디렉토리 없는 basename 이고
    ``json_path.stem`` 은 확장자까지 잃는다 → **존재하지 않을 경로를 지어낸다**.
    그러면 SourceFileCache 가 못 읽어 code_text 가 통째로 빈다(spec 045 FR-007)
    → 조용히 넘기지 않고 경고로 표면화한다.
    """
    rel = raw.get("filePath")
    if not rel:
        rel = raw.get("fileName") or json_path.stem
        log_warning(
            _COMPONENT, "src_path_fallback",
            "antlr JSON 에 filePath 가 없어 폴백으로 경로를 구성했다 — "
            "실제 파일과 다를 수 있고, 그 경우 이 파일의 code_text 가 전부 빈다.",
            fallback=str(rel), json_path=str(json_path),
        )
    return str(source_root / rel)


def _file_module_id(file_path: str) -> str:
    """소스 상대경로 → file-level module id — ``code:<경로(확장자 포함)>`` (spec 047).

    예: ``"sub/dir/foo.c"`` → ``"code:sub/dir/foo.c"``.

    **확장자를 버리지 않는다**: 종전엔 ``foo.c`` 와 ``foo.h`` 가 둘 다 ``"foo"`` 가 되어
    같은 노드로 뭉갰고, 확장자 없는 ``"cart"`` 는 **테이블 cart 의 id 와도 충돌**했다.
    파일시스템이 경로의 유일성을 보장하므로 그걸 그대로 쓴다.
    """
    return code_file_id(file_path)


def _clean_signature(sig: str | None) -> str:
    """시그니처 공백 정규화 — antlr 가 줄바꿈 포함해 줄 수 있음 (예: ``"long\\nfoo(...)"``)."""
    if not sig:
        return ""
    return " ".join(sig.split())


def _unique_id(base: str, start_line: int, seen: set[str]) -> str:
    """동명 충돌 방지 — 중복 시 ``@<startLine>`` 접미 (Java 오버로드 등, spec 047).

    접미 구분자는 ``@`` 다 — 경로 구분자 ``:`` 와 **달라야** 경로 해석이 흐려지지 않는다
    (종전엔 둘 다 ``:`` 라 ``code:foo.c:bar:12`` 가 "bar 안의 12" 처럼 보였다).

    위치 기반 id(파일경로+컨테이너경로+이름) 도입 후엔 여기 걸릴 일이 거의 없다 —
    같은 컨테이너에 같은 이름은 대부분 언어가 금지한다. Java 오버로드만 예외.
    """
    if base not in seen:
        seen.add(base)
        return base
    candidate = positioned(base, start_line)
    n = 2
    while candidate in seen:
        candidate = f"{positioned(base, start_line)}#{n}"
        n += 1
    seen.add(candidate)
    return candidate
