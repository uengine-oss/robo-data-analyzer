"""Inventory external files and build verified source records."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
import time
from typing import Callable, Optional

from langchain_core.runnables import RunnableConfig

from graph.schema.node_ids import project_file_id
from pipeline.execution.graph_delta_stream import emit_message
from pipeline.execution.step_config import step_config
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.steps.step3_load_external.chunk_builder import (
    build_derived_records,
    build_source_records,
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
from pipeline.steps.step3_load_external.span_verifier import verify_spans
from shared.events.processing_events import step_processing_scope, track_processing
from shared.events.runtime_logger import log_info, log_warning
from shared.files.cache import SourceFileCache


_STEP = "step3.load_external"

SpanAdapter = Callable[[str], list[SemanticSpan]]


@dataclass(frozen=True)
class SpanAdapterContract:
    adapter_id: str
    adapter_version: str
    parse: SpanAdapter

# 확장자 → span 어댑터. 여기 없는 config/text 형식은 정직하게 unanalyzed 로 남긴다.
_SPAN_ADAPTER_VERSION = "analyzer-external-span-adapter/v1"
_SPAN_ADAPTERS: dict[str, SpanAdapterContract] = {
    ".xml": SpanAdapterContract("analyzer.external.xml", _SPAN_ADAPTER_VERSION, parse_xml_spans),
    ".yaml": SpanAdapterContract("analyzer.external.yaml", _SPAN_ADAPTER_VERSION, parse_yaml_spans),
    ".yml": SpanAdapterContract("analyzer.external.yaml", _SPAN_ADAPTER_VERSION, parse_yaml_spans),
    ".json": SpanAdapterContract("analyzer.external.json", _SPAN_ADAPTER_VERSION, parse_json_spans),
    ".jsonl": SpanAdapterContract("analyzer.external.jsonl", _SPAN_ADAPTER_VERSION, parse_jsonl_spans),
    ".properties": SpanAdapterContract("analyzer.external.properties", _SPAN_ADAPTER_VERSION, parse_properties_spans),
    ".env": SpanAdapterContract("analyzer.external.properties", _SPAN_ADAPTER_VERSION, parse_properties_spans),
    ".ini": SpanAdapterContract("analyzer.external.ini", _SPAN_ADAPTER_VERSION, parse_ini_spans),
    ".cfg": SpanAdapterContract("analyzer.external.ini", _SPAN_ADAPTER_VERSION, parse_ini_spans),
    ".conf": SpanAdapterContract("analyzer.external.ini", _SPAN_ADAPTER_VERSION, parse_ini_spans),
    ".csv": SpanAdapterContract("analyzer.external.csv", _SPAN_ADAPTER_VERSION, parse_csv_spans),
    ".tsv": SpanAdapterContract(
        "analyzer.external.tsv", _SPAN_ADAPTER_VERSION,
        lambda source: parse_csv_spans(source, "\t"),
    ),
    ".md": SpanAdapterContract("analyzer.external.markdown", _SPAN_ADAPTER_VERSION, parse_markdown_spans),
    ".markdown": SpanAdapterContract("analyzer.external.markdown", _SPAN_ADAPTER_VERSION, parse_markdown_spans),
}


def _source_file(project: str, item: InventoryItem) -> ExternalSourceFile:
    return ExternalSourceFile(
        source_file_id=project_file_id(project, item.relpath),
        project_relative_path=item.relpath,
        extension=item.ext,
        category=item.category,
        status=item.status,
        disposition_reason=item.reason,
    )


def _adapter_suffix(item: InventoryItem) -> str:
    return ".env" if Path(item.relpath).name.lower().endswith(".env") else item.ext


async def load_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Inventory files and preserve only adapter-verified source records."""
    del state  # step2 and step3 are parallel, state-independent producers by contract.
    cfg = step_config(config)
    codebase = cfg.get("codebase")
    source_cache = cfg.get("source_cache")
    project_input_inventory = cfg.get("project_input_inventory")
    if codebase is None:
        raise RuntimeError("step3.load_external requires configurable.codebase")
    if not isinstance(source_cache, SourceFileCache):
        raise RuntimeError("step3.load_external requires the run-scoped SourceFileCache")

    root = Path(codebase.root).resolve()
    project = root.name
    stream = cfg.get("stream")
    pause = cfg.get("pause")
    started = time.monotonic()
    if project_input_inventory is None:
        closure = cfg.get("source_ast_closure")
        selected_source_paths = (
            tuple(artifact.source_path for artifact in closure.artifacts)
            if closure is not None and getattr(codebase, "selected_source_ids", ())
            else None
        )
        inventory = inventory_project(
            root,
            source_cache,
            selected_source_paths=selected_source_paths,
        )
    else:
        inventory = list(project_input_inventory)
    source_files: list[ExternalSourceFile] = []
    source_records: list[ExternalSourceRecord] = []

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    # 파일별 독립 처리 — gather 가 inventory 순서를 보존해 결정론 유지. 8 = 추출 IO 상한.
    _sem = asyncio.Semaphore(8)

    async def _process_one(item) -> tuple:
        await gate()
        async with _sem:
            async with track_processing("ExternalSourceFile", item.relpath):
                source_file = _source_file(project, item)
                if item.status in ("failed", "unanalyzed") or item.category == "source":
                    return source_file, ()

                file_id = source_file.source_file_id
                try:
                    if item.category == "document" and item.ext in DOCUMENT_ADAPTER_SUFFIXES:
                        extracted = await extract_document(
                            item.path,
                            item.ext,
                            source_cache.read_bytes(item.path),
                        )
                        segments = [
                            extracted.markdown[span.char_start:span.char_end]
                            for span in parse_markdown_spans(extracted.markdown)
                        ]
                        records = build_derived_records(
                            file_id=file_id,
                            relpath=item.relpath,
                            segments=segments,
                            external_adapter_id=f"analyzer.external.document{item.ext}",
                            external_adapter_version="analyzer-external-document-adapter/v1",
                        )
                        return replace(
                            source_file, status="analyzed", disposition_reason="",
                        ), records
                    adapter_contract = _SPAN_ADAPTERS.get(_adapter_suffix(item))
                    if adapter_contract is None:
                        return replace(
                            source_file,
                            status="unanalyzed",
                            disposition_reason="no_span_adapter",
                        ), ()
                    decoded = read_text(item.path, source_cache)
                    verified = verify_spans(
                        decoded.text, adapter_contract.parse(decoded.text),
                    )
                    records = build_source_records(
                        file_id=file_id,
                        relpath=item.relpath,
                        source=decoded.text,
                        verified=verified,
                        external_adapter_id=adapter_contract.adapter_id,
                        external_adapter_version=adapter_contract.adapter_version,
                    )
                    status = "analyzed" if records else "unanalyzed"
                    reason = "" if records else "no_semantic_records"
                    return replace(
                        source_file,
                        status=status,
                        disposition_reason=reason,
                    ), records
                except OptionalDependencyMissing:
                    return replace(
                        source_file,
                        status="unanalyzed",
                        disposition_reason="optional_dependency_missing",
                    ), ()
                except Exception as error:  # AdapterParseError·SpanVerificationError 포함 fail-closed
                    log_warning(
                        _STEP,
                        "parse_failed",
                        f"외부 파일 파싱 실패: {type(error).__name__}",
                        file_path=item.relpath,
                    )
                    return replace(
                        source_file,
                        status="failed",
                        disposition_reason="parse_failed",
                    ), ()

    with step_processing_scope(_STEP, stream):
        results = await asyncio.gather(*[_process_one(item) for item in inventory])
        for source_file, records in results:
            source_files.append(source_file)
            source_records.extend(records)

    await gate()
    emit_message(
        stream,
        f"✅ 외부 원문 확인 완료 — 파일 {len(source_files)}, 검증 레코드 {len(source_records)}",
    )
    log_info(
        _STEP,
        "done",
        external_source_files=len(source_files),
        external_source_records=len(source_records),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "external_source_files": source_files,
        "external_source_records": source_records,
    }
