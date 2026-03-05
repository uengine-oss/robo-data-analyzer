"""공통 StatementNode (복사본). exp.config 사용, config.settings / util 미사용."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Set

try:
    from exp import config as _exp_config
    MAX_CONTEXT_TOKEN = _exp_config.MAX_CONTEXT_TOKEN
except Exception:
    MAX_CONTEXT_TOKEN = 300


@dataclass(slots=True)
class StatementNode:
    """평탄화된 AST 노드 (exp용)."""
    node_id: int
    start_line: int
    end_line: int
    node_type: str
    code: str
    token: int
    has_children: bool
    analyzable: bool
    unit_key: Optional[str] = None
    unit_name: Optional[str] = None
    unit_kind: Optional[str] = None
    schema_name: Optional[str] = None
    dml: bool = False
    package_name: Optional[str] = None
    name: Optional[str] = None
    signature: Optional[str] = None
    modifiers: Optional[str] = None
    return_type: Optional[str] = None
    parameters: Optional[str] = None
    generic_type: Optional[str] = None
    extends_type: Optional[str] = None
    implements_types: Optional[str] = None
    field_type: Optional[str] = None
    annotations: Optional[str] = None
    comment: Optional[str] = None
    lines: List[Tuple[int, str]] = field(default_factory=list)
    parent: Optional["StatementNode"] = None
    children: List["StatementNode"] = field(default_factory=list)
    summary: Optional[str] = None
    context: Optional[str] = None
    sibling_context: Optional[str] = None
    ok: bool = True
    external_refs: Optional[List[dict]] = None
    completion_event: asyncio.Event = field(init=False, repr=False)
    context_ready_event: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "completion_event", asyncio.Event())
        object.__setattr__(self, "context_ready_event", asyncio.Event())

    def get_raw_code(self) -> str:
        return "\n".join(f"{line_no}: {text}" for line_no, text in self.lines)

    def get_placeholder_code(
        self, preserve_types: Optional[Set[str]] = None, include_assigns: bool = False
    ) -> str:
        if not self.children:
            return self.get_raw_code()
        preserve_types = preserve_types or set()
        assign_node_set: Set[Tuple[int, int]] = set()
        if include_assigns:
            ASSIGN_TYPES = {"ASSIGNMENT", "NEW_INSTANCE"}

            def find_assign_nodes_recursive(node: StatementNode) -> List[StatementNode]:
                results = []
                for child in node.children:
                    if child.node_type in ASSIGN_TYPES:
                        results.append(child)
                    results.extend(find_assign_nodes_recursive(child))
                return results

            assign_nodes = find_assign_nodes_recursive(self)
            assign_node_set = {(n.start_line, n.end_line) for n in assign_nodes}
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.get_raw_code()
        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(filtered_children, key=lambda c: c.start_line)
        for child in sorted_children:
            while line_index < total_lines and self.lines[line_index][0] < child.start_line:
                line_no, text = self.lines[line_index]
                result_lines.append(f"{line_no}: {text}")
                line_index += 1
            child_span = (child.start_line, child.end_line)
            should_preserve = (
                child.node_type in preserve_types
                or (include_assigns and child_span in assign_node_set)
            )
            if should_preserve:
                while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                    line_no, text = self.lines[line_index]
                    result_lines.append(f"{line_no}: {text}")
                    line_index += 1
            else:
                result_lines.append(f"{child.start_line}: ...code...")
                while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                    line_index += 1
        while line_index < total_lines:
            line_no, text = self.lines[line_index]
            result_lines.append(f"{line_no}: {text}")
            line_index += 1
        return "\n".join(result_lines)

    def get_skeleton_code(self) -> str:
        if not self.children:
            return self.get_raw_code()
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.get_raw_code()
        result_lines: List[str] = []
        sorted_children = sorted(filtered_children, key=lambda c: c.start_line)
        in_child_block = False
        for line_no, text in self.lines:
            is_child_line = any(
                c.start_line <= line_no <= c.end_line for c in sorted_children
            )
            if is_child_line:
                if not in_child_block:
                    result_lines.append("    ....")
                    in_child_block = True
            else:
                in_child_block = False
                result_lines.append(f"{line_no}: {text}")
        return "\n".join(result_lines)

    def get_previous_sibling(self) -> Optional[StatementNode]:
        if not self.parent:
            return None
        siblings = self.parent.children
        if not siblings or len(siblings) < 2:
            return None
        sorted_siblings = sorted(siblings, key=lambda n: n.start_line)
        current_idx = -1
        for i, sib in enumerate(sorted_siblings):
            if sib.node_id == self.node_id:
                current_idx = i
                break
        if current_idx <= 0:
            return None
        return sorted_siblings[current_idx - 1]
