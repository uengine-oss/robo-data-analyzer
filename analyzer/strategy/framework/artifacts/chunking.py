"""Chunking - Create semantic chunks from source files.

Java 파일은 제외됩니다 (ANTLR로 처리).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, AsyncGenerator

from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.artifacts.data_models import (
    Chunk,
    ChunkingStrategy,
    ChunkMetadata,
    FileCategory,
    FileInfo,
    FormatRegistry,
    PhaseResult,
    PhaseStatus,
    ProjectStructure,
)


def _chunk_id(file_path: str, chunk_type: str, name: str) -> str:
    """Generate a deterministic chunk ID."""
    raw = f"{file_path}::{chunk_type}::{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def run_chunking(
    project: ProjectStructure,
    format_registry: FormatRegistry,
    generated_codes: dict | None = None,
    chunks_out: list[Chunk] | None = None,
) -> AsyncGenerator[bytes, None]:
    """청킹 전략 도출 및 실행
    
    Java 파일은 제외됩니다.
    
    Args:
        project: Phase 1.5에서 생성된 프로젝트 구조
        format_registry: Phase 2.5에서 생성된 포맷 레지스트리
        generated_codes: LLM 생성 코드 (Phase 2.5에서 생성, 현재는 미사용)
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    # Phase 3 시작 이벤트
    yield emit_phase_event(3, "비타겟 청킹", "started", 0, {"canPause": True})
    
    try:
        strategies: dict[str, ChunkingStrategy] = {}
        all_chunks: list[Chunk] = []
        errors: list[str] = []
        generated_codes = generated_codes or {}
        chunk_stats_by_category: dict[str, dict[str, int]] = {}
        
        # Define strategies per category
        log_process("ANALYZE", "PHASE_3", "청킹 전략 도출 중...")
        for category_str, _schema in format_registry.schemas.items():
            try:
                category = FileCategory(category_str)
            except ValueError:
                continue
            strategy = _derive_strategy(category)
            strategies[category_str] = strategy
            log_process(
                "ANALYZE", "PHASE_3",
                f"전략: {category_str} -> {strategy.strategy_description} (보존: {', '.join(strategy.metadata_preserved)})"
            )
        
        # Execute chunking for each file
        log_process("ANALYZE", "PHASE_3", "파일 처리 중...")
        total = len(project.files)
        completed = 0
        
        for file_info in project.files:
            cat_str = file_info.category.value
            if cat_str not in strategies:
                continue
            
            try:
                chunks = await _chunk_file_hardcoded(file_info, strategies[cat_str])
                all_chunks.extend(chunks)
                
                # Collect statistics
                chunk_types: dict[str, int] = {}
                for chunk in chunks:
                    ct = chunk.metadata.chunk_type
                    chunk_types[ct] = chunk_types.get(ct, 0) + 1
                    if chunk.sub_chunks:
                        for sub in chunk.sub_chunks:
                            sct = sub.metadata.chunk_type
                            chunk_types[sct] = chunk_types.get(sct, 0) + 1
                
                if cat_str not in chunk_stats_by_category:
                    chunk_stats_by_category[cat_str] = {"files": 0, "chunks": 0}
                chunk_stats_by_category[cat_str]["files"] += 1
                chunk_stats_by_category[cat_str]["chunks"] += len(chunks)
                
                yield emit_message(f"   ✓ {file_info.relative_path}: {len(chunks)}개 청크")
                
                completed += 1
                progress = int(completed / total * 100)
                yield emit_phase_event(3, "비타겟 청킹", "progress", progress, {
                    "completed": completed,
                    "total": total,
                    "file": file_info.relative_path,
                })
                
            except Exception as e:
                error_msg = f"청킹 실패 ({file_info.relative_path}): {e}"
                errors.append(error_msg)
                log_process("ANALYZE", "PHASE_3", f"청킹 오류 ({file_info.relative_path}): {e}")
                yield emit_message(f"   ❌ {file_info.relative_path}: {str(e)[:50]}")
        
        # Log statistics
        log_process("ANALYZE", "PHASE_3", f"총 청크: {len(all_chunks)}개")
        if chunk_stats_by_category:
            log_process("ANALYZE", "PHASE_3", "카테고리별 청킹 통계:")
            for cat, stats in chunk_stats_by_category.items():
                log_process("ANALYZE", "PHASE_3", f"  {cat}: {stats['files']}개 파일 -> {stats['chunks']}개 청크")
        
        # Chunk type distribution
        chunk_type_counts: dict[str, int] = {}
        for chunk in all_chunks:
            ct = chunk.metadata.chunk_type
            chunk_type_counts[ct] = chunk_type_counts.get(ct, 0) + 1
            for sub in chunk.sub_chunks:
                sct = sub.metadata.chunk_type
                chunk_type_counts[sct] = chunk_type_counts.get(sct, 0) + 1
        
        log_process("ANALYZE", "PHASE_3", f"청크 타입 분포: {chunk_type_counts}")
        
        # Phase 3 완료 이벤트
        yield emit_phase_event(3, "비타겟 청킹", "completed", 100, {
            "total_chunks": len(all_chunks),
            "chunk_type_counts": chunk_type_counts,
        })
        
        # Phase 결과 반환 (요약 정보만 전송)
        yield emit_phase_result(3, {
            "total_chunks": len(all_chunks),
            "chunk_stats_by_category": chunk_stats_by_category,
        })
        
        # 호출 측에 실제 Chunk 객체 리스트 전달
        if chunks_out is not None:
            chunks_out.clear()
            chunks_out.extend(all_chunks)
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 3 실패: {e}", exc_info=True)
        yield emit_phase_event(3, "비타겟 청킹", "failed", 0, {"error": str(e)})
        raise


def _derive_strategy(category: FileCategory) -> ChunkingStrategy:
    """Derive the chunking strategy for a file category."""
    # Java는 제외 (ANTLR로 처리)
    # if category == FileCategory.JAVA_SOURCE:
    #     return None
    
    if category == FileCategory.XML_SQLMAP:
        return ChunkingStrategy(
            file_category=category,
            chunk_unit="sql_statement",
            strategy_description="Each SQL mapping (select/insert/update/delete) as independent chunk with context.",
            metadata_preserved=["namespace", "id", "parameterClass", "resultClass"],
        )
    elif category == FileCategory.XML_SPRING:
        return ChunkingStrategy(
            file_category=category,
            chunk_unit="bean_definition",
            strategy_description="Each <bean> definition as independent chunk preserving property/constructor-arg.",
            metadata_preserved=["bean_id", "class", "scope", "depends_on"],
        )
    elif category in (FileCategory.YAML, FileCategory.PROPERTIES):
        return ChunkingStrategy(
            file_category=category,
            chunk_unit="logical_section",
            strategy_description="Split by top-level sections preserving hierarchical key structure.",
            metadata_preserved=["key_path", "profile"],
        )
    elif category in (FileCategory.XML_CONFIG, FileCategory.XML_WEB, FileCategory.XML_OTHER):
        return ChunkingStrategy(
            file_category=category,
            chunk_unit="top_level_element",
            strategy_description="Split by top-level XML elements.",
            metadata_preserved=["tag", "attributes"],
        )
    else:
        return ChunkingStrategy(
            file_category=category,
            chunk_unit="file",
            strategy_description="Whole file as single chunk (default strategy).",
            metadata_preserved=["file_path"],
        )


async def _chunk_file_hardcoded(file_info: FileInfo, strategy: ChunkingStrategy) -> list[Chunk]:
    """Chunk a single file using hardcoded logic."""
    # Java는 제외
    # if file_info.category == FileCategory.JAVA_SOURCE:
    #     return []
    
    if file_info.category == FileCategory.XML_SQLMAP:
        return await asyncio.to_thread(_chunk_sqlmap_xml, file_info)
    elif file_info.category == FileCategory.XML_SPRING:
        return await asyncio.to_thread(_chunk_spring_xml, file_info)
    elif file_info.category == FileCategory.YAML:
        return await asyncio.to_thread(_chunk_yaml, file_info)
    elif file_info.category == FileCategory.PROPERTIES:
        return await asyncio.to_thread(_chunk_properties, file_info)
    elif file_info.category in (FileCategory.XML_CONFIG, FileCategory.XML_WEB, FileCategory.XML_OTHER):
        return await asyncio.to_thread(_chunk_generic_xml, file_info)
    else:
        return await asyncio.to_thread(_chunk_whole_file, file_info)


def _read_file_content(file_info: FileInfo, mode: str = "r") -> str | bytes:
    """Read file content with proper encoding detection."""
    encoding = getattr(file_info, "encoding", "utf-8") or "utf-8"
    if mode == "rb":
        with open(file_info.path, "rb") as f:
            return f.read()
    # Try detected encoding first, then fallbacks
    for enc in [encoding, "utf-8", "euc-kr", "cp949"]:
        try:
            with open(file_info.path, "r", encoding=enc, errors="strict") as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    # Last resort
    with open(file_info.path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _chunk_sqlmap_xml(file_info: FileInfo) -> list[Chunk]:
    """Chunk iBATIS SQL Mapper XML into individual SQL statements."""
    try:
        from lxml import etree

        raw_bytes = _read_file_content(file_info, mode="rb")
        tree = etree.fromstring(raw_bytes)
        root = tree
        chunks: list[Chunk] = []

        namespace = root.get("namespace", "")
        sql_tags = ["select", "insert", "update", "delete"]

        for elem in root.iter():
            local_tag = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
            if local_tag.lower() in sql_tags:
                stmt_id = elem.get("id", "")
                param_class = elem.get("parameterClass", elem.get("parameterType", ""))
                result_class = elem.get("resultClass", elem.get("resultType", elem.get("resultMap", "")))
                sql_text = etree.tostring(elem, encoding="unicode", pretty_print=True)

                cid = _chunk_id(file_info.relative_path, "sql", f"{namespace}.{stmt_id}")
                chunks.append(Chunk(
                    chunk_id=cid,
                    content=sql_text,
                    metadata=ChunkMetadata(
                        source_file=file_info.relative_path,
                        chunk_type="sql_statement",
                        properties={
                            "namespace": namespace,
                            "id": stmt_id,
                            "type": local_tag.lower(),
                            "parameterClass": param_class,
                            "resultClass": result_class,
                        },
                    ),
                ))

        return chunks
    except Exception as e:
        # 왜 SQLMAP 전용 청킹이 실패했는지 항상 보이도록 INFO 레벨로 기록
        from util.text_utils import log_process as _log_process
        _log_process(
            "ANALYZE",
            "PHASE_3",
            f"SQL map 청킹 실패 ({file_info.relative_path}): {type(e).__name__}: {e}",
            level="INFO",
        )
        return _chunk_generic_xml(file_info)


def _chunk_spring_xml(file_info: FileInfo) -> list[Chunk]:
    """Chunk Spring Bean XML into individual bean definitions."""
    try:
        from lxml import etree
        raw_bytes = _read_file_content(file_info, mode="rb")
        tree = etree.ElementTree(etree.fromstring(raw_bytes))
        root = tree.getroot()
        chunks: list[Chunk] = []
        
        ns = {"spring": "http://www.springframework.org/schema/beans"}
        
        # Try with namespace first, then without
        beans = root.findall(".//spring:bean", ns)
        if not beans:
            beans = root.findall(".//bean")
        
        for bean in beans:
            bean_id = bean.get("id", bean.get("name", ""))
            bean_class = bean.get("class", "")
            scope = bean.get("scope", "singleton")
            lazy = bean.get("lazy-init", "false")
            depends = bean.get("depends-on", "")
            
            content = etree.tostring(bean, encoding="unicode", pretty_print=True)
            cid = _chunk_id(file_info.relative_path, "bean", bean_id or bean_class)
            chunks.append(Chunk(
                chunk_id=cid,
                content=content,
                metadata=ChunkMetadata(
                    source_file=file_info.relative_path,
                    chunk_type="spring_bean",
                    properties={
                        "id": bean_id,
                        "class": bean_class,
                        "scope": scope,
                        "lazy_init": lazy,
                        "depends_on": depends,
                    },
                ),
            ))
        
        return chunks
    except Exception as e:
        # Spring XML 청킹 실패 원인도 INFO 레벨로 남겨, 왜 fallback이 발생했는지 즉시 파악 가능하게 함
        from util.text_utils import log_process as _log_process
        _log_process(
            "ANALYZE",
            "PHASE_3",
            f"Spring XML 청킹 실패 ({file_info.relative_path}): {type(e).__name__}: {e}",
            level="INFO",
        )
        return _chunk_generic_xml(file_info)


def _chunk_generic_xml(file_info: FileInfo) -> list[Chunk]:
    """Chunk generic XML by top-level elements."""
    try:
        from lxml import etree
        raw_bytes = _read_file_content(file_info, mode="rb")
        tree = etree.ElementTree(etree.fromstring(raw_bytes))
        root = tree.getroot()
        chunks: list[Chunk] = []

        for child in root:
            if isinstance(child.tag, str):
                local_tag = etree.QName(child.tag).localname
                content = etree.tostring(child, encoding="unicode", pretty_print=True)
                cid = _chunk_id(file_info.relative_path, "xml_element", local_tag)
                chunks.append(Chunk(
                    chunk_id=cid,
                    content=content,
                    metadata=ChunkMetadata(
                        source_file=file_info.relative_path,
                        chunk_type="xml_element",
                        properties={
                            "tag": local_tag,
                            "attributes": dict(child.attrib),
                        },
                    ),
                ))

        return chunks
    except Exception as e:
        # generic XML 단계까지 실패하면 최종적으로 file 단위 fallback이 일어나므로,
        # 그 이유를 INFO 레벨로 남겨 디버깅을 쉽게 한다.
        from util.text_utils import log_process as _log_process
        _log_process(
            "ANALYZE",
            "PHASE_3",
            f"Generic XML 청킹 실패 ({file_info.relative_path}): {type(e).__name__}: {e}",
            level="INFO",
        )
        return _chunk_whole_file(file_info)


def _chunk_yaml(file_info: FileInfo) -> list[Chunk]:
    """Chunk YAML files by top-level sections."""
    try:
        import yaml
        with open(file_info.path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        chunks: list[Chunk] = []
        if isinstance(data, dict):
            for key, value in data.items():
                import json as json_mod
                content = json_mod.dumps({key: value}, indent=2, default=str)
                cid = _chunk_id(file_info.relative_path, "yaml_section", key)
                chunks.append(Chunk(
                    chunk_id=cid,
                    content=content,
                    metadata=ChunkMetadata(
                        source_file=file_info.relative_path,
                        chunk_type="yaml_section",
                        properties={"key": key},
                    ),
                ))
        return chunks
    except Exception:
        return _chunk_whole_file(file_info)


def _chunk_properties(file_info: FileInfo) -> list[Chunk]:
    """Chunk properties files by prefix groups."""
    try:
        with open(file_info.path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        
        # Group by top-level prefix
        groups: dict[str, list[str]] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("!"):
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                prefix = key.split(".")[0] if "." in key else key
                groups.setdefault(prefix, []).append(stripped)
        
        chunks: list[Chunk] = []
        for prefix, entries in groups.items():
            content = "\n".join(entries)
            cid = _chunk_id(file_info.relative_path, "props_section", prefix)
            chunks.append(Chunk(
                chunk_id=cid,
                content=content,
                metadata=ChunkMetadata(
                    source_file=file_info.relative_path,
                    chunk_type="properties_section",
                    properties={"prefix": prefix, "key_count": len(entries)},
                ),
            ))
        return chunks
    except Exception:
        return _chunk_whole_file(file_info)


def _chunk_whole_file(file_info: FileInfo) -> list[Chunk]:
    """Fallback: chunk entire file as single chunk."""
    content = _read_file_content(file_info)
    cid = _chunk_id(file_info.relative_path, "file", file_info.relative_path)
    return [Chunk(
        chunk_id=cid,
        content=content if isinstance(content, str) else content.decode("utf-8", errors="ignore"),
        metadata=ChunkMetadata(
            source_file=file_info.relative_path,
            chunk_type="file",
            properties={},
        ),
    )]

