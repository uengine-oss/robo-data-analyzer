"""Step3 external-file loading through Analyzer-owned boundaries."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.edges.external_links import upsert_external_edges
from graph.nodes.external import upsert_external_nodes
from graph.schema.graph_edges import HasChunk
from graph.schema.graph_nodes import ProjectFile, UnresolvedRef
from graph.schema.node_ids import project_file_id, unresolved_ref_id
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.step_config import step_config
from pipeline.steps.step3_load_external.chunking import make_external_chunks
from pipeline.steps.step3_load_external.encoding_reader import read_text
from pipeline.steps.step3_load_external.inventory import InventoryItem, inventory_project
from pipeline.steps.step3_load_external.parsers.config_parser import parse_config
from pipeline.steps.step3_load_external.parsers.document_adapters import (
    DOCUMENT_ADAPTER_SUFFIXES,
    OptionalDependencyMissing,
    extract_document,
)
from pipeline.steps.step3_load_external.parsers.markdown_doc import parse_markdown
from pipeline.steps.step3_load_external.parsers.xml_universal import parse_xml
from shared.events.processing_events import step_processing_scope, track_processing
from shared.events.runtime_logger import log_info, log_warning
from shared.files.cache import SourceFileCache


_STEP = "step3.load_external"


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


def _unresolved(file_node: ProjectFile, reason: str, detail: str) -> UnresolvedRef:
    bounded = " ".join(detail.split())[:300]
    return UnresolvedRef(
        id=unresolved_ref_id(file_node.id, reason, file_node.relpath),
        name=file_node.name,
        target_type=file_node.category,
        key="",
        reason=reason,
        file_path=file_node.relpath,
        detail=bounded,
    )


def _config_suffix(item: InventoryItem) -> str:
    return ".env" if Path(item.relpath).name.lower().endswith(".env") else item.ext


async def load_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Inventory, parse, persist and stream all external-file observations."""
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
    external_chunks = []
    document_nodes = []
    structural_edges = []
    unresolved_refs: list[UnresolvedRef] = []

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    with step_processing_scope(_STEP, stream):
        for item in inventory:
            await gate()
            async with track_processing("ProjectFile", item.relpath):
                file_node = _project_node(project, item)
                if item.status == "failed":
                    project_files.append(file_node)
                    unresolved_refs.append(_unresolved(file_node, "parse_failed", item.reason))
                    continue
                if item.status == "unanalyzed":
                    project_files.append(file_node)
                    if item.category == "binary":
                        unresolved_refs.append(_unresolved(file_node, "binary_unanalyzed", item.reason))
                    continue
                if item.category == "source":
                    project_files.append(file_node)
                    continue

                try:
                    file_id = file_node.id
                    if item.category == "document" and item.ext in DOCUMENT_ADAPTER_SUFFIXES:
                        extracted = await extract_document(item.path, item.ext)
                        nodes, edges = parse_markdown(file_id, item.relpath, extracted.markdown)
                        document_nodes.extend(nodes)
                        structural_edges.extend(edges)
                        file_node = replace(file_node, status="analyzed", reason="")
                    else:
                        decoded = read_text(item.path, source_cache)
                    if item.category == "document" and item.ext in DOCUMENT_ADAPTER_SUFFIXES:
                        pass
                    elif item.category == "config":
                        entries = parse_config(decoded.text, _config_suffix(item))
                        for entry in entries:
                            chunks, part_edges = make_external_chunks(
                                file_id=file_id,
                                key=entry.key,
                                kind="config",
                                text=entry.value,
                                file_path=item.relpath,
                                start_line=entry.line,
                                end_line=entry.line,
                            )
                            external_chunks.extend(chunks)
                            structural_edges.append(HasChunk(source_id=file_id, target_id=chunks[0].id))
                            structural_edges.extend(part_edges)
                        status = "analyzed" if entries else "unanalyzed"
                        reason = "" if entries else "empty_or_keyless_config"
                        file_node = replace(file_node, status=status, reason=reason)
                    elif item.category == "artifact_xml":
                        entries = parse_xml(decoded.text)
                        has_sql = any(entry.kind == "sql" for entry in entries)
                        for entry in entries:
                            chunks, part_edges = make_external_chunks(
                                file_id=file_id,
                                key=entry.key,
                                kind=entry.kind,
                                text=entry.text,
                                file_path=item.relpath,
                                start_line=entry.start_line,
                                end_line=entry.end_line,
                                sql_type=entry.sql_type,
                                related_fqns=entry.related_fqns,
                            )
                            external_chunks.extend(chunks)
                            structural_edges.append(HasChunk(source_id=file_id, target_id=chunks[0].id))
                            structural_edges.extend(part_edges)
                        file_node = replace(
                            file_node,
                            category="sql_xml" if has_sql else "artifact_xml",
                            status="analyzed" if entries else "unanalyzed",
                            reason="" if entries else "no_supported_xml_elements",
                        )
                    elif item.category == "document":
                        nodes, edges = parse_markdown(file_id, item.relpath, decoded.text)
                        document_nodes.extend(nodes)
                        structural_edges.extend(edges)
                        file_node = replace(file_node, status="analyzed", reason="")
                    else:
                        raise RuntimeError(f"registered file has no parser owner: {item.category}")
                except OptionalDependencyMissing as error:
                    file_node = replace(
                        file_node,
                        status="unanalyzed",
                        reason="optional_dependency_missing",
                    )
                    unresolved_refs.append(_unresolved(
                        file_node,
                        "optional_dependency_missing",
                        str(error),
                    ))
                except Exception as error:
                    file_node = replace(file_node, status="failed", reason="parse_failed")
                    unresolved_refs.append(_unresolved(
                        file_node,
                        "parse_failed",
                        f"{type(error).__name__}: {error}",
                    ))
                    log_warning(
                        _STEP,
                        "parse_failed",
                        f"외부 파일 파싱 실패: {type(error).__name__}",
                        file_path=item.relpath,
                    )
                project_files.append(file_node)

    await gate()
    if neo is not None:
        await upsert_external_nodes(
            neo,
            [*project_files, *external_chunks, *document_nodes, *unresolved_refs],
        )
        await gate()
        await upsert_external_edges(neo, structural_edges)

    await emit_nodes(stream, project_files)
    await emit_nodes(stream, external_chunks)
    await emit_nodes(stream, document_nodes)
    await emit_nodes(stream, unresolved_refs)
    await emit_edges(stream, structural_edges)
    await gate()
    emit_message(
        stream,
        f"✅ 외부 파일 적재 완료 — 파일 {len(project_files)}, 청크 {len(external_chunks)}, 문서 노드 {len(document_nodes)}",
    )
    log_info(
        _STEP,
        "done",
        project_files=len(project_files),
        external_chunks=len(external_chunks),
        document_nodes=len(document_nodes),
        unresolved_refs=len(unresolved_refs),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "project_files": project_files,
        "external_chunks": external_chunks,
        "document_nodes": document_nodes,
        "external_edges": structural_edges,
        "unresolved_refs": unresolved_refs,
    }
