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

from data_models.graph_edges import (
    BelongsTo,
    HasMember,
    ParentOf,
    StatementNext,
)
from data_models.graph_nodes import Constant, DataType, CodeNode, Module, Package, Variable
from shared.config.env_catalog import ENV
from shared.lang.ontology import ROUTINE_LABELS, role_of
from shared.lang.source_extension import DBMS_EXTS, EXT_TO_LANG
from shared.neo4j.ids import child_id, code_file_id, code_ns_id, positioned
from shared.log.logger import log_error, log_warning

_COMPONENT = "analysis_flow.step2_load_ast.antlr_json_reader"

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

def ast_and_source_roots(source_path: Path) -> tuple[Path, Path]:
    """``project.root`` → ``(AST 루트, 소스 루트)``.

    소스 루트 = AST 의 ``filePath`` 가 상대로 매달린 **antlr 파싱 base** (``_src_path``
    가 ``source_root / filePath`` 로 원문 슬라이싱). 이 데이터 규약(``data/source/<project>``
    + AST ``filePath="<project>/file"``)에서 base 는 항상 ``data/source``:
    - ``project.root == data/source`` (HTTP 업로드·일괄) → 소스루트=``data/source``,
      AST=``data/analysis`` 전체.
    - ``project.root == data/source/<project>`` (CLI 단일 대상) → 소스루트=``data/source``
      (base 복원 — 이중경로 차단), AST=``data/analysis/<project>`` 로 스코프(그 프로젝트만).
    - ``data/source`` 밖 (Electron 경로모드, 사용자가 고른 로컬 폴더) → 소스루트=그 폴더,
      AST=``data/analysis`` 전체 (antlr 가 그 구조로 미러).
    """
    analysis_root = ENV.data.dir / "analysis"
    source_base = (ENV.data.dir / "source").resolve()
    root = source_path.resolve()
    try:
        rel = root.relative_to(source_base)
    except ValueError:
        return analysis_root, root           # 경로모드 — base = 고른 폴더 그대로
    if rel == Path("."):
        return analysis_root, source_base    # 업로드·일괄 — 전체
    return analysis_root / rel, source_base   # CLI 단일 대상 — 그 프로젝트로 스코프


def read_antlr_json(json_path: Path, source_root: Path) -> ParseResult:
    """antlr JSON 파일 1개 → ParseResult.

    Args:
        json_path: antlr-parser 가 만든 ``*.json`` (루트 노드 = ``FILE``).
        source_root: 원본 소스 디렉토리 (``data/source``) — JSON 의 ``filePath`` 를
            절대 소스 경로로 풀어 ``file_path`` 에 박는다 (step6 본문 슬라이싱용).

    Returns:
        ParseResult. JSON 파싱 실패 시 빈 결과 (오류 로깅 후 — 다른 파일은 계속).
    """
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        # antlr JSON 은 정상이라면 항상 유효 — 깨졌다면 antlr-parser 산출물 문제.
        # 절대원칙 #3: silent 금지. 오류 기록 후 이 파일만 건너뜀.
        log_error(_COMPONENT, "read_antlr_json", e, json_path=str(json_path))
        return ParseResult()

    if not isinstance(raw, dict):
        log_warning(
            _COMPONENT, "read_antlr_json",
            "JSON 루트가 객체(FILE 노드)가 아님 — 건너뜀",
            json_path=str(json_path),
        )
        return ParseResult()

    file_name = raw.get("fileName") or json_path.name
    ext = Path(file_name).suffix.lstrip(".").lower()

    if ext in DBMS_EXTS:
        return _read_dbms(raw, source_root, json_path)

    lang = EXT_TO_LANG.get(ext)
    if lang is None:
        log_warning(
            _COMPONENT, "read_antlr_json",
            "지원하지 않는 확장자 — 건너뜀 (antlr-parser 미지원 언어)",
            json_path=str(json_path), ext=ext,
        )
        return ParseResult()

    return _read_framework(raw, lang, source_root, json_path)


# ── Framework (java / c / python) ─────────────────────────────

@dataclass
class _FwCtx:
    """한 Framework 파일을 순회하는 동안 고정인 값."""
    lang: str
    package: str             # 파일이 든 폴더 id (최상위면 "")
    src_path: str
    result: ParseResult
    seen: set[str]


def _read_framework(
    raw: dict, lang: str, source_root: Path, json_path: Path,
) -> ParseResult:
    """Framework 파일 → Module / CodeNode / Variable / Constant / DataType 노드.

    ``FILE`` 자체가 모듈인 언어(c / python)는 file-level Module 을 만들고,
    Java 는 ``packageName`` 을 namespace 로 써서 CLASS 가 Module 이 된다.
    """
    result = ParseResult()
    src_path = _src_path(raw, source_root, json_path)
    rel = raw.get("filePath") or raw.get("fileName") or json_path.name
    package_id, package = _folder_package(rel)
    if package is not None:
        result.packages.append(package)

    if role_of("FILE", lang) == "module":
        # c / python — **파일이 곧 모듈**이다 (start/end = 0 → 파일 전체).
        module_id = _file_module_id(rel)
        result.modules.append(_make_module(
            raw, module_id, Path(raw.get("fileName") or json_path.name).stem,
            src_path, 0, 0,
        ))
    else:
        # java — FILE 은 internal(노드 X). **클래스가 모듈**이고, 그 위 네임스페이스가 패키지다.
        # 자바는 파일이 아니라 **패키지+클래스**가 유일성의 원천이다 (언어가 보장).
        # 여기 module_id 는 아직 "패키지 네임스페이스"이고, _fw_module 이 클래스를 이어붙인다.
        module_id = code_ns_id(raw.get("packageName") or "")

    ctx = _FwCtx(lang=lang, package=package_id, src_path=src_path, result=result, seen=set())
    # 파일(FILE) 진입 — 아직 구조 부모도 소유 routine 도 없다(둘 다 None). 함수 진입 시 갱신.
    _walk_framework(raw, ctx, module_id, None, None)
    _derive_structure_edges(result, package_id)
    return result


def _walk_framework(
    node: dict, ctx: _FwCtx, module_id: str,
    parent_fid: str | None, owner_routine_id: str | None,
) -> None:
    """children 을 role 별 핸들러로 디스패치 (``_FW_DISPATCH``).

    두 컨텍스트를 분리 스레딩한다 (spec 054 FR-008):
    - ``parent_fid``       : PARENT_OF(구조 부모). 제어문으로 내려가면 그 제어문으로 바뀐다.
    - ``owner_routine_id`` : CALLS/READS/WRITES 귀속용 소유 routine(가장 가까운 FUNCTION/METHOD).
                             제어문을 지나쳐도 바뀌지 않는다 — 호출의 주어는 언제나 함수다.
    """
    for child in node.get("children") or []:
        handler = _FW_DISPATCH.get(role_of(child.get("type", ""), ctx.lang))
        if handler is not None:
            handler(child, ctx, module_id, parent_fid, owner_routine_id)


# ── role 별 핸들러 — 노드 1개 생성 + 필요 시 재귀 ──

def _fw_module(child: dict, ctx: _FwCtx, module_id: str,
               parent_fid: str | None, owner_routine_id: str | None) -> None:
    """중첩 컨테이너(java 클래스·inner class) → Module 노드.

    id = ``<상위 컨테이너>:<이름>`` (spec 047) — 패키지·바깥클래스 경로가 끝까지 반영된다.
    """
    name = child.get("name") or ""
    sub_id = child_id(module_id, name)
    ctx.result.modules.append(_make_module(
        child, sub_id, name, ctx.src_path,
        int(child.get("startLine") or 0), int(child.get("endLine") or 0),
    ))
    _walk_framework(child, ctx, sub_id, parent_fid, owner_routine_id)


def _fw_code(child: dict, ctx: _FwCtx, module_id: str,
             parent_fid: str | None, owner_routine_id: str | None) -> None:
    fid = _node_id(child, ctx, module_id)
    ctx.result.code_nodes.append(_make_code_node(
        child, fid, module_id, ctx.package, ctx.src_path, parent_fid,
    ))
    if parent_fid is not None:
        ctx.result.parent_of_edges.append(ParentOf(parent_id=parent_fid, child_id=fid))
    # 함수는 구조 부모이자 소유 routine — 둘 다 자기 자신(fid)으로 갱신하며 하강.
    _walk_framework(child, ctx, module_id, fid, fid)


def _fw_control(child: dict, ctx: _FwCtx, module_id: str,
                parent_fid: str | None, owner_routine_id: str | None) -> None:
    """제어 흐름 뼈대(IF/ELSE/LOOP/SWITCH/CASE) → CodeNode (spec 007/054).

    그래프에 보존(라벨=antlr type)하되 routine 이 아니므로 분석 대상에서 제외된다
    (``ROUTINE_LABELS`` 단일 진실 — build_context/step6 가 필터). 구조 부모는 이 제어문
    으로 바뀌지만 **소유 routine 은 불변**이라, 아래 FUNCTION_CALL 의 CALLS 는 함수에 귀속된다.
    id 규칙은 DBMS 구조 노드와 동일: ``<부모>:<TYPE>@<줄>`` (같은 부모의 동종 노드는 줄로 구분).
    """
    sl = int(child.get("startLine") or 0)
    container = parent_fid if parent_fid is not None else module_id
    cid = _unique_id(positioned(child_id(container, child.get("type", "")), sl), sl, ctx.seen)
    ctx.result.code_nodes.append(_make_code_node(
        child, cid, module_id, ctx.package, ctx.src_path, parent_fid,
    ))
    if parent_fid is not None:
        ctx.result.parent_of_edges.append(ParentOf(parent_id=parent_fid, child_id=cid))
    _walk_framework(child, ctx, module_id, cid, owner_routine_id)


def _fw_variable(child: dict, ctx: _FwCtx, module_id: str,
                 parent_fid: str | None, owner_routine_id: str | None) -> None:
    vid = _node_id(child, ctx, module_id)
    ctx.result.variables.append(_make_variable(child, vid, module_id, ctx.src_path))


def _fw_constant(child: dict, ctx: _FwCtx, module_id: str,
                 parent_fid: str | None, owner_routine_id: str | None) -> None:
    cid = _node_id(child, ctx, module_id)
    ctx.result.constants.append(_make_constant(child, cid, module_id, ctx.src_path))


def _fw_data_type(child: dict, ctx: _FwCtx, module_id: str,
                  parent_fid: str | None, owner_routine_id: str | None) -> None:
    did = _node_id(child, ctx, module_id)
    ctx.result.data_types.append(_make_data_type(child, did, module_id, ctx.src_path))
    # struct / union 멤버 → Variable 노드 + HAS_MEMBER (모듈 소속 아닌 자료형 소속).
    # ★ 멤버의 컨테이너는 **구조체(did)** 다 — 모듈이 아니다 (spec 047).
    #   종전엔 module_id 를 넘겨 구조체 이름이 id 에서 사라졌고, 그래서
    #   `member.c` 의 구조체 필드 `member_id` 가 **DB 컬럼 `member.member_id` 와 겹쳤다**.
    #   (owner_id 는 여전히 모듈 — 소속 모듈은 바뀌지 않는다.)
    for member in child.get("children") or []:
        role = role_of(member.get("type", ""), ctx.lang)
        if role == "variable":
            vid = _node_id(member, ctx, did)          # ← 컨테이너 = 구조체
            ctx.result.variables.append(_make_variable(member, vid, module_id, ctx.src_path))
            ctx.result.has_member_edges.append(HasMember(did, vid))
        elif role == "data_type":
            _fw_data_type(member, ctx, module_id, parent_fid, owner_routine_id)  # 중첩 struct


def _fw_internal(child: dict, ctx: _FwCtx, module_id: str,
                 parent_fid: str | None, owner_routine_id: str | None) -> None:
    # CALLS/import 의 주어(caller)는 구조 부모가 아니라 소유 routine 이다 (제어문 아래여도 함수).
    _collect_internal(child, ctx.src_path, ctx.result, owner_routine_id)
    if child.get("type", "") == "FUNCTION_CALL":
        # foo(bar()) — 중첩 호출도 같은 소유 routine 로
        _walk_framework(child, ctx, module_id, parent_fid, owner_routine_id)


_FW_DISPATCH = {
    "module":            _fw_module,
    "function":          _fw_code,
    "control_structure": _fw_control,
    "variable":          _fw_variable,
    "constant":          _fw_constant,
    "data_type":         _fw_data_type,
    "internal":          _fw_internal,
}


def _collect_internal(
    node: dict, src_path: str, result: ParseResult, owner_routine_id: str | None,
) -> None:
    """internal 노드 → 호출/import 단서 수집 (Neo4j 노드 X).

    ``owner_routine_id`` = 이 호출의 주어(소유 routine). 제어문 아래에 있어도 구조 부모가
    아니라 이 값(가장 가까운 함수)으로 caller 를 귀속시킨다 (spec 054 FR-009).
    """
    ctype = node.get("type", "")
    sl = int(node.get("startLine") or 0)
    if ctype == "FUNCTION_CALL" and owner_routine_id is not None:
        result.call_sites.append({
            "call_id": f"{owner_routine_id}:{sl}:{len(result.call_sites)}",
            "caller_id": owner_routine_id,
            "call_text": node.get("name") or "",
            "file": src_path,
            "line": sl,
        })
    elif ctype in ("INCLUDE", "IMPORT"):
        result.import_refs.append({
            "name": node.get("name") or "",
            "file": src_path,
            "line": sl,
        })


# ── DBMS (plsql / plpgsql) ────────────────────────────────────

@dataclass
class _DbmsCtx:
    """한 DBMS 파일을 순회하는 동안 고정인 값."""
    module_id: str           # 이 파일의 모듈(그릇) id — code:<경로>. 담긴 값과 이름을 일치시킴
                             # (옛 이름 `file_stem` 은 "파일명 어간"인데 실제론 모듈 id 를 담아
                             #  이름이 거짓말을 했다 — spec 047)
    package: str             # 파일이 든 폴더 id (최상위면 "")
    src_path: str
    result: ParseResult
    seen: set[str]


def _read_dbms(raw: dict, source_root: Path, json_path: Path) -> ParseResult:
    """DBMS 파일 → Module(파일) + CodeNode 제어흐름 트리 + Variable/Constant.

    PROCEDURE/FUNCTION/TRIGGER 가 CodeNode 루트. 그 안의 IF/LOOP/CASE/DML 등
    중첩 노드도 전부 CodeNode (``parent_id`` 트리). 선언 노드(VARIABLE 등)는
    Variable/Constant 로 수집. DBMS 는 sub-label 없음 (단일 라벨).
    """
    result = ParseResult()
    src_path = _src_path(raw, source_root, json_path)
    file_stem = Path(raw.get("fileName") or json_path.name).stem
    rel = raw.get("filePath") or raw.get("fileName") or json_path.name
    package_id, package = _folder_package(rel)
    if package is not None:
        result.packages.append(package)

    # 파일 자체가 컨테이너 Module (PL/SQL 스크립트 / 패키지 파일). 라벨 = raw["type"] = FILE.
    # id = code:<경로(확장자 포함)> — Framework 와 동일 규칙 (spec 047).
    module_id = code_file_id(rel)
    result.modules.append(_make_module(raw, module_id, file_stem, src_path, 0, 0))

    ctx = _DbmsCtx(
        module_id=module_id, package=package_id,
        src_path=src_path, result=result, seen=set(),
    )
    _walk_dbms_siblings(raw.get("children"), ctx, None)
    _derive_structure_edges(result, package_id)
    return result


def _walk_dbms_siblings(
    children: list[dict] | None, ctx: _DbmsCtx, parent_id: str | None,
) -> None:
    """같은 부모 아래 형제 노드 순회 + 구문 실행 흐름(NEXT) 연결.

    각 형제를 ``_walk_dbms`` 로 처리하고, 구문(CodeNode) 노드끼리 **등장(실행) 순서**로
    ``(이전)→[:NEXT]→(다음)`` 연결한다. 변수/상수(leaf 데이터, ``None`` 반환)는 흐름에
    참여하지 않아 NEXT 체인을 끊지 않는다. DBMS 전용 — Framework 는 구문 흐름 개념 없음.
    """
    prev_id: str | None = None
    for child in children or []:
        curr_id = _walk_dbms(child, ctx, parent_id)
        if curr_id is None:
            continue
        if prev_id is not None:
            ctx.result.statement_next_edges.append(StatementNext(prev_id, curr_id))
        prev_id = curr_id


def _walk_dbms(node: dict, ctx: _DbmsCtx, parent_id: str | None) -> str | None:
    """DBMS 노드 1개 재귀 처리 → 생성한 구문(CodeNode) 노드 id (없으면 ``None``).

    - 변수류         : Variable 노드 (leaf) → ``None`` (흐름 비참여).
    - CONSTANT_FIELD: Constant 노드 (leaf) → ``None``.
    - 그 외(DECLARE/TRY/구문 포함): CodeNode 노드 (제어흐름 트리, 자식 재귀) → 그 id.
      ★구조 노드(DECLARE/TRY/TRIGGER_BLOCK)도 **노드로 보존** — LLM 분석만 batch_planner 가
      제외(누락 0). 노드 종류는 antlr type 그대로 primary 라벨.
    """
    ntype = node.get("type", "")

    # 변수·상수 id = <파일 모듈>:<이름> — Framework 와 동일 규칙 (spec 047).
    if ntype in _DBMS_VAR_TYPES:
        vid = _unique_id(
            child_id(ctx.module_id, node.get("name") or ntype),
            int(node.get("startLine") or 0), ctx.seen,
        )
        ctx.result.variables.append(_make_variable(node, vid, ctx.module_id, ctx.src_path))
        return None

    if ntype == "CONSTANT_FIELD":
        cid = _unique_id(
            child_id(ctx.module_id, node.get("name") or ntype),
            int(node.get("startLine") or 0), ctx.seen,
        )
        ctx.result.constants.append(_make_constant(node, cid, ctx.module_id, ctx.src_path))
        return None

    # 그 외 전부 CodeNode (제어흐름 트리 — 구문 + 구조 노드).
    # id = <컨테이너>:<이름>@<줄> — 컨테이너는 루틴이면 파일 모듈, 중첩 구문이면 부모 구문.
    # 이름 없는 구문(IF/LOOP…)은 antlr type 이 이름 역할 → 같은 부모에 여러 개 → 줄로 구분.
    sl = int(node.get("startLine") or 0)
    container = ctx.module_id if parent_id is None else parent_id
    name = (node.get("name") or ntype) if parent_id is None else ntype
    base = positioned(child_id(container, name), sl)
    fid = _unique_id(base, sl, ctx.seen)
    # DBMS: 노드의 antlr type 그대로가 라벨 (:SELECT/:PROCEDURE/:IF/:DECLARE…). _make_code_node 이 node["type"] 직결.
    ctx.result.code_nodes.append(_make_code_node(
        node, fid, ctx.module_id, ctx.package, ctx.src_path, parent_id,
    ))
    if parent_id is not None:
        ctx.result.parent_of_edges.append(ParentOf(parent_id=parent_id, child_id=fid))
    _walk_dbms_siblings(node.get("children"), ctx, fid)
    return fid


# ── 노드 빌더 — antlr dict → dataclass (Framework / DBMS 공용) ──

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
