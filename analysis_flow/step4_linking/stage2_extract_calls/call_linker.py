"""CallLinker — AST CALL 노드 → ``Calls`` 엣지 (CALL 노드 → 호출 대상). 정규식·LLM 0.

antlr CALL 노드(``…:CALL:line`` + 콜리 이름 = signature)가 호출의 단일 출처. 각 CALL 노드를
호출 대상에 직접 잇는다 — 호출 지점 단위(같은 대상 N회 호출 = N 엣지).

분류:
- 콜리가 빌트인/표준(``DBMS_``/``UTL_``/… prefix) → 드롭.
- 콜리 단순명이 분석대상 정의 노드(루트)와 일치 → internal (CALL 노드 → 그 정의 노드).
- 그 외 → external — ``:FUNCTION:EXTERNAL`` stub (스키마 = 호출 수식어 또는 호출자 자기 스키마).

프로시저↔프로시저는 저장하지 않는다. 루틴 단위가 필요한 소비처는 ``routine_call_pairs`` 로 파생.
"""
from __future__ import annotations

from data_models.graph_edges import Calls
from data_models.graph_nodes import CodeNode
from shared.log.logger import log_warning

# 내장 패키지/표준 라이브러리 prefix → 드롭 (분석 대상 프로시저 아님).
_BUILTIN_PREFIXES = ("DBMS_", "UTL_", "SYS_", "ORA_", "OWA_", "STANDARD")


class CallLinker:
    """AST CALL 노드 → ``Calls`` 엣지 (CALL 노드 → 호출 대상)."""

    async def link(self, code_nodes: list[CodeNode]) -> list[Calls]:
        if not code_nodes:
            return []

        fn_by_id = {fn.id: fn for fn in code_nodes}
        # 콜리는 정의 노드(루트: parent_id None)로만 해석 → 방향 정확.
        roots_by_name: dict[str, str] = {}
        for fn in code_nodes:
            if fn.parent_id is None:
                roots_by_name.setdefault(_proc_name(fn.signature), fn.id)

        edges: list[Calls] = []
        for fn in code_nodes:
            if fn.own_ast_type() != "CALL":
                continue                       # CALL 노드만 (자기 타입 — 자손 오염 X)
            callee_raw = fn.signature.strip()
            if not callee_raw or any(callee_raw.upper().startswith(p) for p in _BUILTIN_PREFIXES):
                continue                       # 빌트인/표준 → 드롭

            simple = _proc_name(callee_raw)
            internal_id = roots_by_name.get(simple)
            if internal_id is not None:
                edges.append(Calls(
                    caller_id=fn.id,
                    callee_id=internal_id,
                    scope="internal",
                ))
            else:
                # external = 분석대상 밖 실 프로시저. 스키마 = 호출 수식어(SCH.PROC) 또는
                # bare 면 호출자(루트 루틴) 자기 스키마. upsert_calls 가 stub 생성.
                root = fn_by_id.get(_root_of(fn, fn_by_id))
                schema = _qualifier(callee_raw) or (getattr(root, "package", "") or "")
                edges.append(Calls(
                    caller_id=fn.id,
                    callee_id=simple,
                    scope="external",
                    callee_module=simple,
                    callee_package=schema.upper(),
                    callee_name=simple,
                ))
        return edges


def _root_of(fn: CodeNode, fn_by_id: dict[str, CodeNode]) -> str:
    """fn 의 프로시저 루트 id — parent_id 체인 최상위 (= 호출자)."""
    cur = fn
    while cur.parent_id is not None:
        parent = fn_by_id.get(cur.parent_id)
        if parent is None:
            break
        cur = parent
    return cur.id


def _proc_name(text: str) -> str:
    """시그니처/호출명 → 단순 프로시저명 (CREATE 헤더·스키마·따옴표·파라미터 제거, 대문자).

    예: ``CREATE OR REPLACE PROCEDURE RWIS.PRC_X(...)`` → ``PRC_X`` /
        ``RWIS."PRC_Y"`` → ``PRC_Y`` / ``TRG_Z`` → ``TRG_Z`` / ``PKG.FUNC`` → ``FUNC``.
    """
    head = text.split("(", 1)[0].strip()
    token = head.split()[-1] if head.split() else head
    return token.rsplit(".", 1)[-1].strip('"').upper()


def _qualifier(text: str) -> str:
    """호출 수식어(스키마/패키지) 추출 — ``RWIS.PRC_X`` → ``RWIS`` / ``PRC_X`` → ``''``.

    external 콜리의 소속 스키마 판정용. 수식 없으면 빈 문자열(=호출자 자기 스키마).
    """
    head = text.split("(", 1)[0].strip()
    token = head.split()[-1] if head.split() else head
    token = token.strip('"')
    if "." in token:
        return token.rsplit(".", 1)[0].strip('"').upper()
    return ""


def routine_call_pairs(
    code_nodes: list[CodeNode], calls: list[Calls],
) -> list[tuple[str, str]]:
    """저장된 CALL→대상 internal 호출을 (소유 루트 루틴, 대상 루틴) 쌍으로 해소.

    호출을 프로시저 단위로 보는 백엔드 소비처(컴포넌트·건강·분석순서)의 단일 출처.
    중첩 CALL 에서 거슬러 올라가 만난 재귀(DBMS)는 루틴 자기루프로 만들지 않는다(옛 동작 보존);
    루틴이 직접 자기를 참조하는 경우(Framework)는 보존. 중복 제거, 해소 실패는 로그로 표면화.
    """
    fn_by_id = {f.id: f for f in code_nodes}
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    unresolved = 0
    for c in calls:
        if c.scope != "internal":
            continue
        caller = fn_by_id.get(c.caller_id)
        if caller is None or c.callee_id not in fn_by_id:
            unresolved += 1
            continue
        root = _root_of(caller, fn_by_id)
        if root == c.callee_id and c.caller_id != root:
            continue                           # 중첩 호출이 거슬러 올라가 만난 재귀 → 자기루프 X
        key = (root, c.callee_id)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    if unresolved:
        log_warning(
            "call_linker.routine_call_pairs", "unresolved",
            "CALL 노드의 소유 루틴/대상 해소 실패 — 루틴 단위 파생서 제외",
            count=unresolved,
        )
    return pairs
