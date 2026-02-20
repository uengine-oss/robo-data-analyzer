"""Phase 2.5 (nontarget): Schema Discovery - Infer schemas for each file format, then generate parser code via LLM.

Java 파일은 제외됩니다 (ANTLR로 처리).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncGenerator

from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.nontarget.data_models import (
    FileCategory,
    FileInfo,
    FormatRegistry,
    GeneratedCode,
    PhaseResult,
    PhaseStatus,
    ProjectStructure,
    SchemaDefinition,
)


async def run_phase2_5_schema_discovery(
    project: ProjectStructure,
    api_key: str,
    schema_sample_count: int = 5,
    use_code_generation: bool = True,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.5 (nontarget): 스키마 발견 - 모든 파일 형식에 대한 스키마 추론 및 LLM을 통한 파서 코드 생성
    
    Java 파일은 제외됩니다.
    
    Args:
        project: Phase 1.5에서 생성된 프로젝트 구조
        api_key: LLM API 키
        schema_sample_count: 스키마 추론에 사용할 샘플 파일 수
        use_code_generation: LLM 코드 생성 활성화 여부
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    # Phase 2.5 시작 이벤트
    yield emit_phase_event(2.5, "비타겟 스키마 발견", "started", 0, {"canPause": True})
    
    try:
        registry = FormatRegistry()
        
        # Group files by category
        log_process("ANALYZE", "PHASE_2.5", "카테고리별 파일 그룹화 중...")
        files_by_category: dict[FileCategory, list[FileInfo]] = {}
        for f in project.files:
            files_by_category.setdefault(f.category, []).append(f)
        
        # Process each category (Java 제외)
        log_process("ANALYZE", "PHASE_2.5", "스키마 발견 중...")
        categories_to_process = [
            (cat, files) for cat, files in files_by_category.items()
            if cat not in (FileCategory.BINARY, FileCategory.UNKNOWN)
        ]
        
        completed = 0
        total = len(categories_to_process)
        
        for category, files in categories_to_process:
            log_process("ANALYZE", "PHASE_2.5", f"{category.value} 스키마 발견 중 ({len(files)}개 파일)")
            schema = await _discover_schema(category, files, schema_sample_count)
            if schema:
                registry.schemas[category.value] = schema
                if schema.parser_code:
                    registry.parser_codes[category.value] = schema.parser_code
                yield emit_message(f"   ✓ {category.value}: 스키마 발견 완료")
            else:
                yield emit_message(f"   ⚠️ {category.value}: 스키마 발견 실패")
            
            completed += 1
            progress = int(completed / total * 100)
            yield emit_phase_event(2.5, "비타겟 스키마 발견", "progress", progress, {
                "completed": completed,
                "total": total,
                "category": category.value,
            })
        
        # Build similarity map
        log_process("ANALYZE", "PHASE_2.5", "유사도 맵 구축 중...")
        registry.similarity_map = _build_similarity_map(registry.schemas)
        
        # Dynamic code generation via LLM
        generated_codes: dict[str, dict[str, Any]] = {}
        if use_code_generation and api_key:
            log_process("ANALYZE", "PHASE_2.5", "LLM을 통한 동적 코드 생성 중...")
            # TODO: CodeGenerator 통합 필요 (나중에 구현)
            # generated_codes = await _generate_dynamic_code(registry, project, files_by_category, api_key)
            log_process("ANALYZE", "PHASE_2.5", f"{len(generated_codes)}개 코드 아티팩트 생성")
        else:
            log_process("ANALYZE", "PHASE_2.5", "코드 생성 스킵 (비활성화 또는 API 키 없음)")
        
        log_process(
            "ANALYZE", "PHASE_2.5",
            f"요약: {len(registry.schemas)}개 스키마 발견, {len(generated_codes)}개 코드 아티팩트 생성"
        )
        
        # Phase 2.5 완료 이벤트
        yield emit_phase_event(2.5, "비타겟 스키마 발견", "completed", 100, {
            "schemas": len(registry.schemas),
            "generated_codes": len(generated_codes),
        })
        
        # Phase 결과 반환
        yield emit_phase_result(2.5, {
            "format_registry": registry,
            "generated_codes": generated_codes,
        })
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 2.5 실패: {e}", exc_info=True)
        yield emit_phase_event(2.5, "비타겟 스키마 발견", "failed", 0, {"error": str(e)})
        raise


async def _discover_schema(
    category: FileCategory,
    files: list[FileInfo],
    schema_sample_count: int,
) -> SchemaDefinition | None:
    """Discover schema for a given file category."""
    sample_files = files[:schema_sample_count]
    sample_paths = [str(f.relative_path) for f in sample_files]
    
    # Java는 제외 (ANTLR로 처리)
    # if category == FileCategory.JAVA_SOURCE:
    #     return None
    
    if category in (
        FileCategory.XML_CONFIG,
        FileCategory.XML_SPRING,
        FileCategory.XML_SQLMAP,
        FileCategory.XML_WEB,
        FileCategory.XML_OTHER,
    ):
        return await _discover_xml_schema(category, sample_files, sample_paths)
    elif category == FileCategory.JSON:
        return await _discover_json_schema(sample_files, sample_paths, schema_sample_count)
    elif category == FileCategory.YAML:
        return await _discover_yaml_schema(sample_files, sample_paths, schema_sample_count)
    elif category == FileCategory.PROPERTIES:
        return await _discover_properties_schema(sample_files, sample_paths, schema_sample_count)
    elif category == FileCategory.BUILD_FILE:
        return await _discover_build_schema(sample_files, sample_paths)
    elif category in (FileCategory.CUSTOM, FileCategory.TEXT):
        return await _discover_custom_schema(sample_files, sample_paths, schema_sample_count)
    return None


async def _discover_xml_schema(
    category: FileCategory,
    files: list[FileInfo],
    sample_paths: list[str],
) -> SchemaDefinition:
    """Discover XML schema by inferring XSD from instances."""
    schema_content: dict[str, Any] = {"type": "xml", "sub_type": category.value}
    inferred_elements: list[dict[str, Any]] = []
    
    for file_info in files[:3]:
        elements = await asyncio.to_thread(_extract_xml_structure, file_info.path)
        if elements:
            inferred_elements.append(elements)
    
    if inferred_elements:
        schema_content["structure_samples"] = inferred_elements
    
    # Detect namespaces
    namespaces = set()
    for file_info in files[:3]:
        ns = await asyncio.to_thread(_extract_xml_namespaces, file_info.path)
        namespaces.update(ns)
    if namespaces:
        schema_content["namespaces"] = list(namespaces)
    
    return SchemaDefinition(
        format_name=f"xml:{category.value}",
        file_category=category,
        schema_type="xsd",
        schema_content=schema_content,
        sample_files=sample_paths,
        confidence=0.9,
    )


def _extract_xml_structure(path: Path) -> dict[str, Any] | None:
    """Extract XML structure (element names, attributes, nesting)."""
    try:
        from lxml import etree
        # Read raw bytes so lxml can handle encoding declaration
        with open(path, "rb") as f:
            raw = f.read()
        root = etree.fromstring(raw)
        
        def _node_info(elem: Any, depth: int = 0) -> dict[str, Any]:
            local_tag = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else str(elem.tag)
            info: dict[str, Any] = {
                "tag": local_tag,
                "attributes": list(elem.attrib.keys()),
            }
            if depth < 3:
                children_tags = set()
                children_info = []
                for child in elem:
                    if isinstance(child.tag, str):
                        ctag = etree.QName(child.tag).localname
                        if ctag not in children_tags:
                            children_tags.add(ctag)
                            children_info.append(_node_info(child, depth + 1))
                if children_info:
                    info["children"] = children_info
            return info
        
        return _node_info(root)
    except Exception as e:
        log_process("ANALYZE", "PHASE_2.5", f"XML 구조 추출 실패 ({path}): {e}", level="DEBUG")
        return None


def _extract_xml_namespaces(path: Path) -> list[str]:
    """Extract namespaces from an XML file."""
    try:
        from lxml import etree
        with open(path, "rb") as f:
            raw = f.read()
        root = etree.fromstring(raw)
        return [ns for ns in root.nsmap.values() if ns]
    except Exception:
        return []


async def _discover_json_schema(
    files: list[FileInfo],
    sample_paths: list[str],
    schema_sample_count: int,
) -> SchemaDefinition:
    """Discover JSON schema using genson."""
    schema_content: dict[str, Any] = {"type": "json"}
    
    try:
        from genson import SchemaBuilder
        builder = SchemaBuilder()
        
        for file_info in files[:schema_sample_count]:
            try:
                with open(file_info.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                builder.add_object(data)
            except Exception:
                continue
        
        schema = builder.to_schema()
        schema_content["json_schema"] = schema
    except ImportError:
        log_process("ANALYZE", "PHASE_2.5", "genson 사용 불가; JSON 스키마 추론 스킵", level="WARNING")
    except Exception as e:
        log_process("ANALYZE", "PHASE_2.5", f"JSON 스키마 추론 실패: {e}", level="DEBUG")
    
    return SchemaDefinition(
        format_name="json",
        file_category=FileCategory.JSON,
        schema_type="json_schema",
        schema_content=schema_content,
        sample_files=sample_paths,
        confidence=0.9,
    )


async def _discover_yaml_schema(
    files: list[FileInfo],
    sample_paths: list[str],
    schema_sample_count: int,
) -> SchemaDefinition:
    """Discover YAML schema by parsing and inferring structure."""
    schema_content: dict[str, Any] = {"type": "yaml"}
    
    try:
        import yaml
        
        structures = []
        for file_info in files[:schema_sample_count]:
            try:
                with open(file_info.path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    structures.append({"top_keys": list(data.keys())[:20]})
            except Exception:
                continue
        
        if structures:
            schema_content["structures"] = structures
        
        # Also try genson for JSON-schema-like inference
        try:
            from genson import SchemaBuilder
            builder = SchemaBuilder()
            for file_info in files[:schema_sample_count]:
                try:
                    with open(file_info.path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if data:
                        builder.add_object(data)
                except Exception:
                    continue
            schema_content["inferred_schema"] = builder.to_schema()
        except (ImportError, Exception):
            pass
    
    except ImportError:
        log_process("ANALYZE", "PHASE_2.5", "PyYAML 사용 불가; YAML 스키마 추론 스킵", level="WARNING")
    
    return SchemaDefinition(
        format_name="yaml",
        file_category=FileCategory.YAML,
        schema_type="json_schema",
        schema_content=schema_content,
        sample_files=sample_paths,
        confidence=0.85,
    )


async def _discover_properties_schema(
    files: list[FileInfo],
    sample_paths: list[str],
    schema_sample_count: int,
) -> SchemaDefinition:
    """Discover properties file schema by analyzing key patterns."""
    schema_content: dict[str, Any] = {"type": "properties"}
    
    all_keys: list[str] = []
    for file_info in files[:schema_sample_count]:
        keys = await asyncio.to_thread(_extract_property_keys, file_info.path)
        all_keys.extend(keys)
    
    if all_keys:
        # Analyze key hierarchy patterns (e.g., spring.datasource.url)
        prefixes: dict[str, int] = {}
        for key in all_keys:
            parts = key.split(".")
            if len(parts) > 1:
                prefix = parts[0]
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
        
        schema_content["key_prefixes"] = dict(sorted(prefixes.items(), key=lambda x: -x[1])[:20])
        schema_content["total_keys"] = len(all_keys)
    
    return SchemaDefinition(
        format_name="properties",
        file_category=FileCategory.PROPERTIES,
        schema_type="custom",
        schema_content=schema_content,
        sample_files=sample_paths,
        confidence=0.9,
    )


def _extract_property_keys(path: Path) -> list[str]:
    """Extract keys from a properties file."""
    keys: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("!"):
                    if "=" in line:
                        key = line.split("=", 1)[0].strip()
                        keys.append(key)
                    elif ":" in line:
                        key = line.split(":", 1)[0].strip()
                        keys.append(key)
    except OSError:
        pass
    return keys


async def _discover_build_schema(
    files: list[FileInfo],
    sample_paths: list[str],
) -> SchemaDefinition:
    """Discover build file schema."""
    return SchemaDefinition(
        format_name="build_file",
        file_category=FileCategory.BUILD_FILE,
        schema_type="custom",
        schema_content={"type": "build_file"},
        sample_files=sample_paths,
        confidence=1.0,
    )


async def _discover_custom_schema(
    files: list[FileInfo],
    sample_paths: list[str],
    schema_sample_count: int,
) -> SchemaDefinition:
    """Discover schema for custom/unknown file formats using pattern detection."""
    schema_content: dict[str, Any] = {"type": "custom"}
    patterns: list[dict[str, Any]] = []
    
    for file_info in files[:schema_sample_count]:
        pattern = await asyncio.to_thread(_detect_custom_patterns, file_info.path)
        if pattern:
            patterns.append(pattern)
    
    if patterns:
        schema_content["detected_patterns"] = patterns
    
    return SchemaDefinition(
        format_name="custom",
        file_category=FileCategory.CUSTOM,
        schema_type="custom",
        schema_content=schema_content,
        sample_files=sample_paths,
        confidence=0.5,
    )


def _detect_custom_patterns(path: Path) -> dict[str, Any] | None:
    """Detect patterns in a custom-format file."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [f.readline() for _ in range(50)]
        lines = [l for l in lines if l]
        
        if not lines:
            return None
        
        pattern: dict[str, Any] = {"file": str(path.name)}
        
        # Check for delimiter patterns
        for delim in ["\t", "|", ",", ";"]:
            counts = [line.count(delim) for line in lines if line.strip()]
            if counts and len(set(counts)) <= 2 and counts[0] > 0:
                pattern["delimiter"] = delim
                pattern["columns"] = counts[0] + 1
                break
        
        # Check for section patterns
        section_markers = [l for l in lines if l.strip().startswith("[") and l.strip().endswith("]")]
        if section_markers:
            pattern["has_sections"] = True
        
        # Check for key-value patterns
        kv_count = sum(1 for l in lines if "=" in l or ": " in l)
        if kv_count > len(lines) * 0.5:
            pattern["key_value"] = True
        
        return pattern if len(pattern) > 1 else None
    except Exception:
        return None


def _build_similarity_map(
    schemas: dict[str, SchemaDefinition]
) -> dict[str, list[str]]:
    """Build a map of similar file format schemas."""
    similarity: dict[str, list[str]] = {}
    xml_types = [k for k in schemas if k.startswith("xml")]
    if len(xml_types) > 1:
        for t in xml_types:
            similarity[t] = [other for other in xml_types if other != t]
    return similarity

