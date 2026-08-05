"""step6_build_context — 모든 CodeNode 에 ``AnalysisContext`` 생성.

흐름:
1. 코드노드 메타 + Linking 결과 (reads/writes/callers/callees) 로 결정적 컨텍스트 생성.
2. ``strategy.context_builder.augment()`` 호출 → strategy 별 ``extra`` 슬롯 채움.
summary 슬롯은 None 으로 둠 → ``step7_analyze_code`` 이 Bottom-up DAG 로 채움.

이 단계는 LLM을 호출하지 않는다. ``SourceFileCache`` 가 file_path → 텍스트 라인을 캐싱한다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.files.cache import SourceFileCache
from shared.files.source_excerpt import raw_node_text
from pipeline.execution.step_config import step_config

from pipeline.steps.step6_build_context.control_subtree import (
    build_control_subtrees,
    build_statement_facts,
)
from pipeline.steps.step6_build_context.referenced_symbols import _build_reference_context
from pipeline.steps.step6_build_context.external_evidence import build_external_evidence_context
from pipeline.models.analysis_context import AnalysisContext
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info, log_warning

_STEP = "step6.build_context"


def _estimate_tokens(text: str) -> int:
    """본문 배치 예산용 보수적 토큰 추정."""
    n_ascii = sum(1 for char in text if ord(char) < 128)
    n_other = len(text) - n_ascii
    return n_ascii // 4 + int(n_other / 1.5)


async def build_context_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """모든 CodeNode 에 ``AnalysisContext`` 생성."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    started = time.monotonic()

    builder = strategy.context_builder
    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    rw_edges = state.get("reads_writes_edges", [])
    calls_edges = state.get("calls_edges", [])
    log_info(
        _STEP, "start",
        code_nodes=len(code_nodes), reads_writes_edges=len(rw_edges), calls_edges=len(calls_edges),
        builder=type(builder).__name__,
    )

    # code_id → reads/writes/callers/callees 집계 (sorted tuple 강제 — 캐시 친화)
    reads_by_fn: dict[str, set[str]] = {}
    writes_by_fn: dict[str, set[str]] = {}
    # (fn, table) → 결정론 dml_type 집합. SQL_KEY 접미(_i/_u/_d 등)에서 나온 권위 op 다
    # (spec 100). 뒤에서 "단일 op" 만 골라 LLM 의 op 오귀속(예: _u 인데 INSERT)을 교정한다.
    op_by_fn: dict[str, dict[str, set[str]]] = {}
    for e in rw_edges:
        bucket = reads_by_fn if e.relation == "READS" else writes_by_fn
        bucket.setdefault(e.source_id, set()).add(e.target_id)
        dml = getattr(e, "dml_type", "") or ""
        if dml:
            op_by_fn.setdefault(e.source_id, {}).setdefault(e.target_id, set()).add(dml)

    callers_by_fn: dict[str, set[str]] = {}
    callees_by_fn: dict[str, set[str]] = {}
    for e in calls_edges:
        callees_by_fn.setdefault(e.caller_id, set()).add(e.callee_id)
        callers_by_fn.setdefault(e.callee_id, set()).add(e.caller_id)

    # 참조 컨텍스트 — REFERENCES + INIT_BY (참조 변수/상수).
    refsym_by_fn = _build_reference_context(state)
    external_by_fn = build_external_evidence_context(state, code_nodes)

    # 파일 본문 캐시 — 같은 file 여러 코드노드가 공유 (성능: N*M read → N read).
    body_cache = cfg.get("source_cache") or SourceFileCache()   # run 스코프 공유 (FR-008)

    # 컨텍스트 생성 (결정적 메타)
    contexts: dict[str, AnalysisContext] = {}
    control_subtrees = build_control_subtrees(code_nodes)
    statement_facts = build_statement_facts(
        code_nodes, state.get("variables", []), state.get("constants", []),
    )
    # parser FUNCTION_CALL 좌표 (spec 016) — branch 직접 호출 귀속의 AST 정본.
    # (routine 상대 1-based line, 호출명) 쌍. source 정규식 `이름(` 재탐색을 대체한다.
    routine_start = {fn.id: fn.start_line for fn in code_nodes}
    call_lines_by_fn: dict[str, list[tuple[int, str]]] = {}
    for site in state.get("call_sites", []) or []:
        caller = str(site.get("caller_id") or "")
        start = routine_start.get(caller)
        name = str(site.get("call_text") or "")
        if not caller or start is None or not name:
            continue
        line = int(site.get("line") or 0) - start + 1
        if line >= 1:
            call_lines_by_fn.setdefault(caller, []).append((line, name))
    for lines in call_lines_by_fn.values():
        lines.sort()
    for fn in code_nodes:
        # 원문은 step2 가 이미 채웠다 (code_text) — 같은 (file,start,end) 를 여기서 다시
        # 잘라내지 않는다 (spec 046 FR-007: 슬라이싱 계산은 한 곳). 없을 때만 캐시에서 슬라이스
        # (code_text 가 빈 경우 = 소스 read 실패 — FR-004 가 이미 경고를 남겼다).
        body_text = raw_node_text(fn) or body_cache.slice(
            fn.file_path, fn.start_line, fn.end_line
        )
        ctx = AnalysisContext(
            id=fn.id,
            signature=fn.signature,
            file_path=fn.file_path,
            start_line=fn.start_line,
            end_line=fn.end_line,
            reads=tuple(sorted(reads_by_fn.get(fn.id, ()))),
            writes=tuple(sorted(writes_by_fn.get(fn.id, ()))),
            callers=tuple(sorted(callers_by_fn.get(fn.id, ()))),
            callees=tuple(sorted(callees_by_fn.get(fn.id, ()))),
            referenced_symbols=tuple(sorted(refsym_by_fn.get(fn.id, ()), key=lambda symbol: (symbol.kind, symbol.name, symbol.id))),
            external_evidence=external_by_fn.get(fn.id, ()),
            token_count=_estimate_tokens(body_text),
        )
        ctx = await builder.augment(ctx, fn, state)
        if fn.id in control_subtrees:
            ctx.extra["control_subtree"] = control_subtrees[fn.id]
        # parser 소유 구조 statement 정본 (spec 016) — branch_inventory 가 종료행·
        # 반환식·대입·entry 를 이것으로만 조립한다. statement 가 없는 routine 도
        # "없음"이 사실이므로 빈 튜플을 명시 제공한다(source 재탐색 금지).
        ctx.extra["ast_statements"] = tuple(statement_facts.get(fn.id, ()))
        ctx.extra["ast_call_lines"] = tuple(call_lines_by_fn.get(fn.id, ()))
        # 권위 op 맵 — 한 (fn,table) 이 **단일 dml_type** 일 때만(모호하면 제외). LLM 이
        # 낡은 주석에 속아 op 를 뒤바꾸는 환각(감사 최대 결함)을 결정론으로 교정하는 근거.
        # SELECT → 폐쇄형 op "READ" 로 정규화(AFFECTS_TABLE 계약), 나머지는 그대로.
        fn_ops = op_by_fn.get(fn.id, {})
        table_op = {
            table: ("READ" if next(iter(ops)) == "SELECT" else next(iter(ops)))
            for table, ops in fn_ops.items() if len(ops) == 1
        }
        if table_op:
            ctx.extra["table_op"] = table_op
        contexts[fn.id] = ctx

    log_info(
        _STEP, "done",
        contexts=len(contexts),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}
