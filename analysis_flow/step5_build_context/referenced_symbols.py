"""REFERENCES/INIT_BY → Rule/Example용 구조화 값 근거."""
from __future__ import annotations

from data_models.analysis_context import ReferencedSymbolContext
from data_models.graph_nodes import Constant, Variable


def _build_reference_context(state: dict) -> dict[str, list[ReferencedSymbolContext]]:
    """코드노드별 참조 심볼과 모든 초기화 출처를 결정론적으로 구성한다."""
    var_by_id: dict[str, Variable] = {v.id: v for v in state.get("variables", [])}
    const_by_id: dict[str, Constant] = {c.id: c for c in state.get("constants", [])}
    initby_by_target: dict[str, list[str]] = {}
    for edge in state.get("init_by_edges", []):
        initby_by_target.setdefault(edge.from_id, []).append(edge.source_id)

    refsym_by_fn: dict[str, list[ReferencedSymbolContext]] = {}
    seen_sym: dict[str, set[str]] = {}
    for edge in state.get("references_edges", []):
        # 본문이 참조한 심볼뿐 아니라 그 INIT_BY 출처도 레코드로 포함한다. 출처 이름만
        # 남기면 DEFAULT_STATUS="PAID" 같은 실제 선언값이 프롬프트 전에 다시 유실된다.
        pending = [edge.target_id]
        while pending:
            target_id = pending.pop(0)
            if target_id in seen_sym.setdefault(edge.source_id, set()):
                continue
            symbol = _build_symbol(target_id, var_by_id, const_by_id, initby_by_target)
            if symbol is None:
                continue
            seen_sym[edge.source_id].add(target_id)
            refsym_by_fn.setdefault(edge.source_id, []).append(symbol)
            pending.extend(initby_by_target.get(target_id, ()))
    return refsym_by_fn


def _build_symbol(
    target_id: str,
    var_by_id: dict[str, Variable],
    const_by_id: dict[str, Constant],
    initby_by_target: dict[str, list[str]],
) -> ReferencedSymbolContext | None:
    """REFERENCES 1개 → 값 의미와 모든 초기화 출처를 보존한 구조 레코드."""
    constant = const_by_id.get(target_id)
    if constant is not None:
        symbol = ReferencedSymbolContext(
            id=constant.id,
            name=constant.name,
            kind="constant",
            data_type=constant.var_type,
            declared_value=constant.value,
            value_semantics="immutable_constant",
        )
    else:
        variable = var_by_id.get(target_id)
        if variable is None:
            return None
        symbol = ReferencedSymbolContext(
            id=variable.id,
            name=variable.name,
            kind="variable",
            data_type=variable.var_type,
            declared_value=variable.init_value,
            value_semantics="declaration_initializer" if variable.init_value else "mutable_variable",
        )

    source_names = []
    for source_id in initby_by_target.get(target_id, []):
        source = const_by_id.get(source_id) or var_by_id.get(source_id)
        if source is not None:
            source_names.append(source.name)
    return ReferencedSymbolContext(
        **{**symbol.__dict__, "initialization_sources": tuple(sorted(set(source_names)))},
    )
