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
    ParentOf,
    StatementNext,
)
from graph.schema.graph_nodes import Constant, DataType, CodeNode, Module, Package, Variable
from shared.config.app_settings import SETTINGS
from pipeline.source_languages import ROUTINE_LABELS, role_of
from pipeline.source_languages import DBMS_EXTS, EXT_TO_LANG
from graph.schema.node_ids import child_id, code_file_id, code_ns_id, positioned
from shared.events.runtime_logger import log_error, log_warning

_COMPONENT = "pipeline.stages.ast.antlr_json_reader"

# (옛 ``_DBMS_EXTS`` / ``_ROUTINE_LABELS`` 삭제 — 각각 ``source_extension.DBMS_EXTS`` 와
#  ``ontology.ROUTINE_LABELS`` 가 단일 정의처다. 종전엔 여기와 batch_analyze 가 목록을
#  따로 들어 실제로 갈라졌다: batch_analyze 쪽엔 METHOD 가 빠져 있었다. spec 046 FR-003/FR-005.)


from pipeline.parsing.facts.node_builders import (
    ParseResult, _clean_signature, _derive_structure_edges, _file_module_id,
    _folder_package, _make_code_node, _make_constant, _make_data_type,
    _make_module, _make_variable, _node_id, _absolute_source_path,
    _required_source_id, _unique_id,
)

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
    source_id = _required_source_id(raw, json_path)
    src_path = _absolute_source_path(source_id, source_root, json_path)
    file_stem = Path(source_id).stem
    rel = source_id
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
    role = role_of(ntype, "sql")

    # 변수·상수 id = <파일 모듈>:<이름> — Framework 와 동일 규칙 (spec 047).
    if role == "variable":
        vid = _unique_id(
            child_id(ctx.module_id, node.get("name") or ntype),
            int(node.get("startLine") or 0), ctx.seen,
        )
        ctx.result.variables.append(_make_variable(node, vid, ctx.module_id, ctx.src_path))
        return None

    if role == "constant":
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
