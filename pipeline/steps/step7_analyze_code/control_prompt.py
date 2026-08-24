"""Shared model-visible control-flow index with absolute source coordinates."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import lang_of
from pipeline.models.analysis_context import AnalysisContext
from pipeline.quality_gates.branch_inventory import BranchInventory, build_inventory
from pipeline.steps.step7_analyze_code.rule_row_ownership import owning_routine
from shared.files.cache import SourceFileCache


def build_control_inventory(
    fn: CodeNode,
    ctx: AnalysisContext,
    cache: SourceFileCache,
) -> BranchInventory:
    """프롬프트·정답지 실체화가 공유하는 분기 대장 — 게이트와 같은 정의처.

    지원하지 않는 언어는 ``lang_of``가 명시적으로 실패시킨다. 분기 없음과 언어 미지원은
    같은 상태가 아니며, 미지원 소스를 조용히 무규칙으로 저장하면 정확도 결함이다.
    """
    statements = ctx.extra.get("ast_statements")
    if statements is None:
        # parser statement 계약(spec 016) 부재는 corpus/배선 결함 — source 재탐색으로
        # 복원하지 않고 좌표와 함께 실패한다 (No Silent Failure).
        raise ValueError(
            f"ast_statements missing for {fn.id!r} — "
            "step6 build_statement_facts 가 모든 routine 에 명시 제공해야 한다 (spec 016)"
        )
    return build_inventory(
        cache.slice(ctx.file_path, ctx.start_line, ctx.end_line),
        ctx.extra.get("control_subtree"),
        lang_of(Path(fn.file_path).suffix),
        line_offset=max(fn.start_line - 1, 0),
        handler_markers=ctx.extra.get("handler_markers") or (),
        declared_values=tuple(
            symbol.declared_value
            for symbol in ctx.referenced_symbols
            if symbol.value_semantics == "immutable_constant"
            and symbol.declared_value
        ),
        statements=statements,
        call_lines=ctx.extra.get("ast_call_lines") or (),
        call_occurrences=ctx.extra.get("ast_call_occurrences") or (),
        # DBMS DML 효과 좌표 (spec 119). framework 는 키가 없어 빈 튜플이다.
        table_ops=ctx.extra.get("ast_table_ops") or (),
    )


def routine_inventory(
    fn: CodeNode,
    contexts: Mapping[str, AnalysisContext],
    code_by_id: Mapping[str, CodeNode],
    cache: SourceFileCache,
) -> tuple[CodeNode, BranchInventory]:
    """rule 의무의 실제 소유자(루틴)와 그 분기표 (spec 117).

    statement/control 사실의 생산자가 루틴 id 로만 집계하므로, 배치 노드가 루틴이 아니면
    자기 좌표로 분기표를 만들 수 없다. 소유 루틴으로 올라가 한 번 만들고, 행 배분은
    ``rule_row_ownership`` 이 소유한다.

    framework 는 분석 대상이 루틴뿐이라 소유 루틴 = 자기 자신이고 결과가 달라지지 않는다.
    """
    routine = owning_routine(fn, code_by_id)
    routine_ctx = contexts.get(routine.id)
    if routine_ctx is None:
        raise ValueError(
            f"소유 routine 의 분석 컨텍스트가 없다: {fn.id!r} → {routine.id!r}"
        )
    return routine, build_control_inventory(routine, routine_ctx, cache)


def control_index(
    fn: CodeNode,
    ctx: AnalysisContext,
    cache: SourceFileCache,
) -> str:
    """Render parser-owned control structure without inventing branch meaning."""
    inventory = build_control_inventory(fn, ctx, cache)
    return inventory.branch_index()
