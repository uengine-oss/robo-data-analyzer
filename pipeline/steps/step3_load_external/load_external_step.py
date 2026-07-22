"""Step3 external-file loading through Analyzer-owned boundaries (spec 078).

Route each inventoried file to its span adapter, verify the source-slice
invariants, and persist ``PROJECT_FILE ─HAS_CHUNK→ EXTERNAL_CHUNK`` only.
Files without a span adapter stay honestly ``unanalyzed``; parse failures are
``failed`` — never a partial chunk set.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Callable, Optional

from langchain_core.runnables import RunnableConfig

from graph.edges.external_links import upsert_external_edges
from graph.nodes.external import upsert_external_nodes
from graph.schema.graph_edges import HasChunk
from graph.schema.graph_nodes import ExternalChunk, ProjectFile
from graph.schema.node_ids import project_file_id
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.step_config import step_config
from pipeline.steps.step3_load_external.chunk_builder import (
    build_derived_chunks,
    build_source_chunks,
)
from pipeline.steps.step3_load_external.encoding_reader import read_text
from pipeline.steps.step3_load_external.inventory import InventoryItem, inventory_project
from pipeline.steps.step3_load_external.parsers.document_adapters import (
    DOCUMENT_ADAPTER_SUFFIXES,
    OptionalDependencyMissing,
    extract_document,
)
from pipeline.steps.step3_load_external.parsers.span_common import SemanticSpan
from pipeline.steps.step3_load_external.parsers.span_config import (
    parse_csv_spans,
    parse_ini_spans,
    parse_json_spans,
    parse_jsonl_spans,
    parse_properties_spans,
    parse_yaml_spans,
)
from pipeline.steps.step3_load_external.parsers.span_markdown import parse_markdown_spans
from pipeline.steps.step3_load_external.parsers.span_xml import parse_xml_spans
from pipeline.steps.step3_load_external.span_verifier import (
    SpanVerificationError,
    verify_spans,
)
from shared.events.processing_events import step_processing_scope, track_processing
from shared.events.runtime_logger import log_info, log_warning
from shared.files.cache import SourceFileCache


_STEP = "step3.load_external"

SpanAdapter = Callable[[str], list[SemanticSpan]]

# 확장자 → span 어댑터. 여기 없는 config/text 형식은 정직하게 unanalyzed 로 남긴다.
_SPAN_ADAPTERS: dict[str, SpanAdapter] = {
    ".xml": parse_xml_spans,
    ".yaml": parse_yaml_spans,
    ".yml": parse_yaml_spans,
    ".json": parse_json_spans,
    ".jsonl": parse_jsonl_spans,
    ".properties": parse_properties_spans,
    ".env": parse_properties_spans,
    ".ini": parse_ini_spans,
    ".cfg": parse_ini_spans,
    ".conf": parse_ini_spans,
    ".csv": parse_csv_spans,
    ".tsv": lambda source: parse_csv_spans(source, "\t"),
    ".md": parse_markdown_spans,
    ".markdown": parse_markdown_spans,
}


def _project_node(project: str, item: InventoryItem) -> ProjectFile:
    return ProjectFile(
        id=project_file_id(project, item.relpath),
        name=Path(item.relpath).name,
        project=project,
        relpath=item.relpath,
        ext=item.ext,
        category=item.category,
        status=item.status,
        reason=item.reason,
    )


def _adapter_suffix(item: InventoryItem) -> str:
    return ".env" if Path(item.relpath).name.lower().endswith(".env") else item.ext


async def load_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Inventory, chunk, verify, persist and stream all external-file observations."""
    del state  # step2 and step3 are parallel, state-independent producers by contract.
    cfg = step_config(config)
    codebase = cfg.get("codebase")
    source_cache = cfg.get("source_cache")
    if codebase is None:
        raise RuntimeError("step3.load_external requires configurable.codebase")
    if not isinstance(source_cache, SourceFileCache):
        raise RuntimeError("step3.load_external requires the run-scoped SourceFileCache")

    root = Path(codebase.root).resolve()
    project = root.name
    stream = cfg.get("stream")
    neo = cfg.get("neo_client")
    pause = cfg.get("pause")
    started = time.monotonic()
    inventory = inventory_project(root, source_cache)
    project_files: list[ProjectFile] = []
    external_chunks: list[ExternalChunk] = []
    structural_edges: list[HasChunk] = []

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    with step_processing_scope(_STEP, stream):
        for item in inventory:
            await gate()
            async with track_processing("ProjectFile", item.relpath):
                file_node = _project_node(project, item)
                if item.status in ("failed", "unanalyzed") or item.category == "source":
                    project_files.append(file_node)
                    continue

                file_id = file_node.id
                try:
                    if item.category == "document" and item.ext in DOCUMENT_ADAPTER_SUFFIXES:
                        extracted = await extract_document(item.path, item.ext)
                        segments = [
                            extracted.markdown[span.char_start:span.char_end]
                            for span in parse_markdown_spans(extracted.markdown)
                        ]
                        nodes, edges = build_derived_chunks(
                            file_id=file_id, relpath=item.relpath, segments=segments)
                        external_chunks.extend(nodes)
                        structural_edges.extend(edges)
                        file_node = replace(file_node, status="analyzed", reason="")
                    else:
                        adapter = _SPAN_ADAPTERS.get(_adapter_suffix(item))
                        if adapter is None:
                            file_node = replace(
                                file_node, status="unanalyzed", reason="no_span_adapter")
                        else:
                            decoded = read_text(item.path, source_cache)
                            verified = verify_spans(decoded.text, adapter(decoded.text))
                            nodes, edges = build_source_chunks(
                                file_id=file_id,
                                relpath=item.relpath,
                                source=decoded.text,
                                verified=verified,
                            )
                            external_chunks.extend(nodes)
                            structural_edges.extend(edges)
                            status = "analyzed" if nodes else "unanalyzed"
                            reason = "" if nodes else "no_semantic_chunks"
                            file_node = replace(file_node, status=status, reason=reason)
                except OptionalDependencyMissing:
                    file_node = replace(
                        file_node, status="unanalyzed", reason="optional_dependency_missing")
                except Exception as error:  # AdapterParseError·SpanVerificationError 포함 fail-closed
                    file_node = replace(file_node, status="failed", reason="parse_failed")
                    log_warning(
                        _STEP,
                        "parse_failed",
                        f"외부 파일 파싱 실패: {type(error).__name__}",
                        file_path=item.relpath,
                    )
                project_files.append(file_node)

    await gate()
    if neo is not None:
        await upsert_external_nodes(neo, [*project_files, *external_chunks])
        await gate()
        await upsert_external_edges(neo, structural_edges)

    await emit_nodes(stream, project_files)
    await emit_nodes(stream, external_chunks)
    await emit_edges(stream, structural_edges)
    await gate()
    emit_message(
        stream,
        f"✅ 외부 파일 적재 완료 — 파일 {len(project_files)}, 의미 청크 {len(external_chunks)}",
    )
    log_info(
        _STEP,
        "done",
        project_files=len(project_files),
        external_chunks=len(external_chunks),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "project_files": project_files,
        "external_chunks": external_chunks,
        "external_edges": structural_edges,
    }
