"""antlr JSON AST → 코드 노드 + 분석 단서.

antlr-parser 가 만든 ``data/analysis/<p>/*.json`` framework 트리만 읽는다 — 소스 재파싱 X.
DBMS tree walk는 ``dbms_reader``가 소유하고, 두 reader의 노드 생성 규약은
``node_builders``가 공유한다. 언어 역할 해석은 ontology가 흡수한다.

생산물 (ParseResult):
- Module / CodeNode / Package / Variable / Constant / DataType 노드
- PARENT_OF (트리 중첩) 엣지
- call_sites (FUNCTION_CALL) · import_refs (INCLUDE/IMPORT) — 노드 아님, 후속 step 단서

노드 라벨 = 자기 antlr ``type`` 하나 (reader 가 ``node["type"]`` 직결, Framework/DBMS 공통).
ontology ``role_of`` 는 분류 전용 — 어느 dataclass/엣지/internal 인지만 정한다 (라벨엔 안 씀).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from graph.schema.graph_edges import (
    HasMember,
    ParentOf,
)
from pipeline.languages import control_ownership_contracts, role_of
from graph.schema.node_ids import child_id, code_ns_id, positioned


from pipeline.steps.step2_load_ast.node_builders import (
    ParseResult, _derive_structure_edges, _file_module_id,
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
    _validate_control_ownership(raw, source_ref=str(json_path))
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


def _validate_control_ownership(root: dict, *, source_ref: str = "<AST>") -> None:
    """Reject non-canonical parser ownership with an actionable source coordinate."""
    contracts = control_ownership_contracts()
    grouped_parent = {child: parent for parent, child in contracts.items()}

    def visit(node: dict, parent_type: str | None = None) -> None:
        node_type = str(node.get("type") or "")
        expected_parent = grouped_parent.get(node_type)
        if expected_parent is not None and parent_type != expected_parent:
            raise ValueError(
                f"parser control ownership invalid at {source_ref}: "
                f"{node_type}@{node.get('startLine')} parent={parent_type!r}, "
                f"expected={expected_parent!r}"
            )
        children = list(node.get("children") or ())
        grouped_child = contracts.get(node_type)
        if grouped_child is not None:
            seen_group = False
            groups: list[dict] = []
            for child in children:
                if child.get("type") == grouped_child:
                    seen_group = True
                    groups.append(child)
                elif seen_group:
                    raise ValueError(
                        f"parser control ownership invalid at {source_ref}: "
                        f"{node_type}@{node.get('startLine')} has "
                        f"{child.get('type')}@{child.get('startLine')} "
                        f"after first {grouped_child}"
                    )
            parent_end = int(node.get("endLine") or 0)
            for index, group in enumerate(groups):
                start = int(group.get("startLine") or 0)
                end = int(group.get("endLine") or 0)
                next_start = (
                    int(groups[index + 1].get("startLine") or 0)
                    if index + 1 < len(groups) else parent_end + 1
                )
                if start < 1 or end < start or (next_start > 0 and end >= next_start):
                    raise ValueError(
                        f"parser control range invalid at {source_ref}: {grouped_child} "
                        f"start={start}, end={end}, next={next_start}"
                    )
        for child in children:
            visit(child, node_type)

    visit(root)


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
        child_type = child.get("type", "")
        handler = _FW_DISPATCH.get(role_of(child_type, ctx.lang))
        if handler is not None:
            handler(child, ctx, module_id, parent_fid, owner_routine_id)
            continue
        # ontology 미등록 타입 — 조용히 버리면 서브트리째 증발한다(라벨 67행 소실 사고의
        # 상위 원인 계열). 표면화용으로 기록하고, 자식으로 재귀해 내부의 아는 사실
        # (호출·statement)은 같은 구조 부모/소유 routine 문맥으로 보존한다.
        if child_type:
            ctx.result.unknown_nodes.append({
                "type": child_type,
                "file": ctx.src_path,
                "line": int(child.get("startLine") or 0),
            })
        _walk_framework(child, ctx, module_id, parent_fid, owner_routine_id)


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


def _fw_statement(child: dict, ctx: _FwCtx, module_id: str,
                  parent_fid: str | None, owner_routine_id: str | None) -> None:
    """구조 statement(RETURN/THROW/RAISE/BREAK/CONTINUE/GOTO/ASSIGNMENT) → CodeNode.

    parser spec 016 이 emit 한 leaf 사실이다. 제어 노드와 같은 id 규칙으로 보존하되
    routine 이 아니므로 LLM 분석 대상에서 제외된다(ROUTINE_LABELS 단일 진실). 표현식
    원문(expression/target/operator)은 ``_make_code_node`` 가 그대로 옮긴다 — analyzer 가
    source 를 재파싱하지 않기 위한 정본이다. leaf 라 하강하지 않는다.
    """
    sl = int(child.get("startLine") or 0)
    container = parent_fid if parent_fid is not None else module_id
    sid = _unique_id(positioned(child_id(container, child.get("type", "")), sl), sl, ctx.seen)
    ctx.result.code_nodes.append(_make_code_node(
        child, sid, module_id, ctx.package, ctx.src_path, parent_fid,
    ))
    if parent_fid is not None:
        ctx.result.parent_of_edges.append(ParentOf(parent_id=parent_fid, child_id=sid))


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
    "statement":         _fw_statement,
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
