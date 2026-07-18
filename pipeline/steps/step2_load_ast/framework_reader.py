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


from pipeline.steps.step2_load_ast.node_builders import (
    ParseResult, _clean_signature, _derive_structure_edges, _file_module_id,
    _folder_package, _make_code_node, _make_constant, _make_data_type,
    _make_module, _make_variable, _node_id, _src_path, _unique_id,
)

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
    if lang == "c":
        _normalize_c_switch_cases(raw)

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


def _normalize_c_switch_cases(node: dict) -> None:
    """Repair parser CASE ownership to cover its statements until the next label.

    The C parser emits statements between labels as SWITCH children and may nest
    stacked labels (``case X: default:``).  Flattening labels and deriving their
    ranges from sibling boundaries preserves the source-level control tree used
    by PARENT_OF and REFERENCES.
    """
    children = list(node.get("children") or [])
    if node.get("type") == "SWITCH":
        flattened: list[dict] = []
        for child in children:
            if child.get("type") == "CASE":
                flattened.extend(_flatten_c_case_labels(child))
            else:
                flattened.append(child)
        children = []
        active_case: dict | None = None
        for child in flattened:
            if child.get("type") == "CASE":
                children.append(child)
                active_case = child
            elif active_case is None:
                children.append(child)
            else:
                active_case.setdefault("children", []).append(child)
        node["children"] = children

        case_indexes = [i for i, child in enumerate(children) if child.get("type") == "CASE"]
        switch_end = int(node.get("endLine") or 0)
        for position, child_index in enumerate(case_indexes):
            case = children[child_index]
            if position + 1 < len(case_indexes):
                next_case = children[case_indexes[position + 1]]
                end_line = int(next_case.get("startLine") or 0) - 1
            else:
                end_line = switch_end
            start_line = int(case.get("startLine") or 0)
            case["endLine"] = max(start_line, end_line)

    for child in children:
        _normalize_c_switch_cases(child)


def _flatten_c_case_labels(case: dict) -> list[dict]:
    """Turn parser-nested stacked CASE labels into SWITCH-level siblings."""
    own_children: list[dict] = []
    nested: list[dict] = []
    for child in case.get("children") or []:
        (nested if child.get("type") == "CASE" else own_children).append(child)
    case["children"] = own_children
    result = [case]
    for child in nested:
        result.extend(_flatten_c_case_labels(child))
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
