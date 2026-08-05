"""Shared model-visible control-flow index with absolute source coordinates."""
from __future__ import annotations

from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import lang_of
from pipeline.models.analysis_context import AnalysisContext
from pipeline.quality_gates.branch_inventory import BranchInventory, build_inventory
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
    )


def control_index(
    fn: CodeNode,
    ctx: AnalysisContext,
    cache: SourceFileCache,
) -> str:
    """Render parser-owned control structure without inventing branch meaning."""
    inventory = build_control_inventory(fn, ctx, cache)
    return inventory.branch_index()
