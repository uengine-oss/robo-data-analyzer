"""CallLinker — AST CALL 노드 → ``Calls`` 엣지 (정규식·LLM 0).

antlr 이 이미 뽑은 ``CALL`` 노드(``…:CALL:<line>`` function_id + 콜리 이름 = signature)를
호출의 단일 출처로 쓴다. 생 텍스트 정규식이 아니므로 문자열 상수·선언·**주석처리된 호출**은
애초에 CALL 노드가 아니어서 오인식이 원천 차단된다.

분류 (사용자 확정 기준):
- 콜리 이름이 빌트인/표준(``DBMS_``/``UTL_``/… prefix) → **드롭** (분석 대상 아님).
- 콜리 단순명이 분석대상 정의 노드(PROCEDURE/FUNCTION/TRIGGER 루트)와 일치 → **internal**
  (대상 = 그 정의 노드 — CALL-site 가 아니라 정의 노드로 해석해 방향 정확).
- 그 외 실제 프로시저명 → **external** — 그래프에 ``:FUNCTION:EXTERNAL`` stub 생성
  (upsert_calls). 스키마(package) = 호출 수식어(``SCH.PROC``) 또는 없으면 호출자 자기
  스키마(bare = 자기 스키마의 프로시저). 모듈·함수 = 프로시저명.

caller = 그 CALL 을 감싼 프로시저 루트 (parent_id 체인 최상위). caller == callee 자기루프 드롭.
"""
from __future__ import annotations

from data_models.graph_edges import Calls
from data_models.graph_nodes import Function

# 내장 패키지/표준 라이브러리 prefix → 드롭 (분석 대상 프로시저 아님).
_BUILTIN_PREFIXES = ("DBMS_", "UTL_", "SYS_", "ORA_", "OWA_", "STANDARD")


class CallLinker:
    """AST CALL 노드 → ``Calls`` 엣지."""

    async def link(self, functions: list[Function]) -> list[Calls]:
        if not functions:
            return []

        fn_by_id = {fn.function_id: fn for fn in functions}
        # 정의 노드(루트: parent_id None) 단순명 → function_id.
        # callee 는 정의 노드로만 해석 (CALL-site 노드 후보 제외 → 방향 garbled 차단).
        roots_by_name: dict[str, str] = {}
        for fn in functions:
            if fn.parent_id is None:
                roots_by_name.setdefault(_proc_name(fn.signature), fn.function_id)

        edges: list[Calls] = []
        seen: set[tuple[str, str]] = set()
        for fn in functions:
            if fn.own_ast_type() != "CALL":
                continue                       # CALL 노드만 (자기 타입 — 자손 오염 X)
            callee_raw = fn.signature.strip()
            if not callee_raw or any(callee_raw.upper().startswith(p) for p in _BUILTIN_PREFIXES):
                continue                       # 빌트인/표준 → 드롭

            caller_id = _root_of(fn, fn_by_id)
            simple = _proc_name(callee_raw)
            internal_id = roots_by_name.get(simple)
            if internal_id is not None:
                if caller_id == internal_id:
                    continue                   # 자기루프 드롭
                if (caller_id, internal_id) in seen:
                    continue
                seen.add((caller_id, internal_id))
                edges.append(Calls(
                    caller_function_id=caller_id,
                    callee_function_id=internal_id,
                    scope="internal",
                ))
            else:
                # external = 분석대상 밖 실 프로시저. 스키마(package) = 호출 수식어
                # (SCH.PROC) 또는 bare 면 호출자 자기 스키마. 모듈·함수 = 프로시저명.
                # upsert_calls 가 internal 과 대칭으로 :FUNCTION:EXTERNAL stub 생성.
                if (caller_id, simple) in seen:
                    continue
                seen.add((caller_id, simple))
                caller_root = fn_by_id.get(caller_id)
                schema = _qualifier(callee_raw) or (getattr(caller_root, "package_id", "") or "")
                edges.append(Calls(
                    caller_function_id=caller_id,
                    callee_function_id=simple,
                    scope="external",
                    callee_module_id=simple,
                    callee_package_id=schema.upper(),
                    callee_name=simple,
                ))
        return edges


def _root_of(fn: Function, fn_by_id: dict[str, Function]) -> str:
    """fn 의 프로시저 루트 function_id — parent_id 체인 최상위 (= 호출자)."""
    cur = fn
    while cur.parent_id is not None:
        parent = fn_by_id.get(cur.parent_id)
        if parent is None:
            break
        cur = parent
    return cur.function_id


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
