from __future__ import annotations

import pytest

from graph.schema.graph_nodes import CodeNode
from pipeline.analysis_ir.parser_facts.expression_ownership import (
    ExpressionOwnershipError,
    attach_routine_expression_ledger,
    routine_expression_ledger,
)


def _node(node_id: str, label: str, parent_id: str | None = None) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path="sample.c",
        start_line=1,
        end_line=10,
        label=label,
        parent_id=parent_id,
    )


def _fact(fact_id: str, owner: str, offset: int) -> dict:
    return {
        "fact_id": fact_id,
        "code_node_id": owner,
        "source_range": {"char_offset": offset, "char_length": 1},
    }


def test_assigns_nested_expression_facts_to_one_routine_in_source_order() -> None:
    routine = _node("routine", "FUNCTION")
    branch = _node("branch", "IF", routine.id)
    statement = _node("statement", "RETURN", branch.id)
    facts = (_fact("later", statement.id, 8), _fact("earlier", branch.id, 3))

    ledger = attach_routine_expression_ledger(
        [routine, branch, statement], facts,
    )

    assert [item["fact_id"] for item in ledger[routine.id]] == ["earlier", "later"]
    assert routine.extra["_resolved_expression_facts"] == ledger[routine.id]
    assert "_resolved_expression_facts" not in branch.extra


def test_expression_owner_without_routine_ancestor_fails_closed() -> None:
    orphan = _node("orphan", "RETURN")

    with pytest.raises(ExpressionOwnershipError, match="no routine ancestor"):
        routine_expression_ledger([orphan], [_fact("expr", orphan.id, 1)])
