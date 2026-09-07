from graph.schema.graph_nodes import CodeNode
from pipeline.meaning.source_outline import render_source_outline


def _node(
    node_id: str,
    label: str,
    start: int,
    end: int,
    parent_id: str | None = None,
) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path="sample.c",
        start_line=start,
        end_line=end,
        label=label,
        parent_id=parent_id,
    )


def test_outline_keeps_exact_ast_ancestry_without_internal_values() -> None:
    function = _node("fn", "FUNCTION", 12, 30)
    branch = _node("if", "IF", 14, 25, "fn")
    call = _node("secret-call-id", "FUNCTION_CALL", 18, 18, "if")
    assignment = _node("assign", "ASSIGNMENT", 20, 20, "if")

    rendered = render_source_outline(
        function,
        {node.id: node for node in (function, branch, call, assignment)},
    )

    assert rendered == (
        "12-30 : FUNCTION\n"
        "  14-25 : IF\n"
        "    18 : CALL\n"
        "    20 : ASSIGNMENT"
    )
    assert "secret-call-id" not in rendered


def test_unshown_parser_container_does_not_invent_an_outline_level() -> None:
    function = _node("fn", "FUNCTION", 1, 10)
    block = _node("block", "BLOCK", 2, 9, "fn")
    returned = _node("return", "RETURN", 8, 8, "block")

    assert render_source_outline(
        function,
        {node.id: node for node in (function, block, returned)},
    ) == "1-10 : FUNCTION\n  8 : RETURN"
