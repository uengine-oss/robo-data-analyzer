"""Markdown structure extraction without semantic-link promotion."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from graph.schema.graph_edges import HasChunk, HasSection
from graph.schema.graph_nodes import DocumentNode
from graph.schema.node_ids import external_chunk_id
from pipeline.steps.step3_load_external.chunking import split_text


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_markdown(file_id: str, relpath: str, text: str) -> tuple[list[DocumentNode], list[object]]:
    """Return DOCUMENT/section/chunk nodes and their structural relationships."""
    lines = text.splitlines()
    source_hash = _digest(text)
    root_id = external_chunk_id(file_id, "document", 0)
    root = DocumentNode(
        id=root_id,
        label="DOCUMENT",
        name=Path(relpath).stem,
        title=Path(relpath).stem,
        text="",
        file_path=relpath,
        start_line=1 if lines else 0,
        end_line=len(lines),
        order=0,
        parent_id="",
        content_hash=source_hash,
    )
    nodes = [root]
    edges: list[object] = [HasChunk(source_id=file_id, target_id=root_id)]
    section_stack: list[tuple[int, str]] = []
    current_parent = root_id
    buffer: list[tuple[int, str]] = []
    section_index = 0
    chunk_index = 0

    def flush() -> None:
        nonlocal buffer, chunk_index
        if not buffer:
            return
        body = "\n".join(value for _, value in buffer).strip()
        start = buffer[0][0]
        end = buffer[-1][0]
        buffer = []
        if not body:
            return
        for piece in split_text(body):
            if not piece:
                continue
            chunk_index += 1
            node_id = external_chunk_id(file_id, f"document-chunk:{chunk_index}", 0)
            nodes.append(DocumentNode(
                id=node_id,
                label="DOC_CHUNK",
                name=f"chunk {chunk_index}",
                title="",
                text=piece,
                file_path=relpath,
                start_line=start,
                end_line=end,
                order=chunk_index,
                parent_id=current_parent,
                content_hash=_digest(piece),
            ))
            edges.append(HasSection(source_id=current_parent, target_id=node_id, order=chunk_index))

    for line_number, line in enumerate(lines, 1):
        heading = _HEADING.match(line)
        if heading:
            flush()
            section_index += 1
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            parent = section_stack[-1][1] if section_stack else root_id
            section_id = external_chunk_id(file_id, f"section:{section_index}:{title}", 0)
            nodes.append(DocumentNode(
                id=section_id,
                label="DOC_SECTION",
                name=title,
                title=title,
                text="",
                file_path=relpath,
                start_line=line_number,
                end_line=line_number,
                order=section_index,
                parent_id=parent,
                content_hash=source_hash,
            ))
            edges.append(HasSection(source_id=parent, target_id=section_id, order=section_index))
            section_stack.append((level, section_id))
            current_parent = section_id
        elif not line.strip() and buffer:
            flush()
        else:
            buffer.append((line_number, line))
    flush()
    return nodes, edges
