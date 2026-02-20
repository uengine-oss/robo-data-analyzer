"""File type identification using multiple strategies (non-LLM).

Java 파일은 제외됩니다 (ANTLR로 처리).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from analyzer.strategy.framework.nontarget.data_models import FileCategory

# Map of known extensions to categories (Java 제외)
EXTENSION_MAP: dict[str, FileCategory] = {
    ".xml": FileCategory.XML_CONFIG,
    ".yml": FileCategory.YAML,
    ".yaml": FileCategory.YAML,
    ".properties": FileCategory.PROPERTIES,
    ".json": FileCategory.JSON,
    ".class": FileCategory.BINARY,
    ".jar": FileCategory.BINARY,
    ".war": FileCategory.BINARY,
    ".ear": FileCategory.BINARY,
}

BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "build.xml", "settings.gradle"}

# XML sub-type detection keywords
XML_SPRING_INDICATORS = ["<beans", "xmlns:beans", "springframework", "<bean"]
XML_SQLMAP_INDICATORS = ["<sqlMap", "<sqlMapConfig", "ibatis", "mybatis", "<mapper"]
XML_WEB_INDICATORS = ["<web-app", "servlet"]


def identify_file_type(path: Path) -> tuple[FileCategory, str, str]:
    """Identify file type using extension and content analysis.
    
    NOTE: Java 파일은 제외됩니다 (.java, .kt 확장자는 None을 반환하거나 필터링되어야 함).

    Returns:
        Tuple of (category, detected_type_string, mime_type)
    """
    ext = path.suffix.lower()
    
    # Java 파일 제외
    if ext in (".java", ".kt"):
        return FileCategory.UNKNOWN, "java_excluded", ""
    
    mime_type, _ = mimetypes.guess_type(str(path))
    mime_type = mime_type or ""

    # Check build files first
    if path.name in BUILD_FILES:
        return FileCategory.BUILD_FILE, f"build:{path.name}", mime_type

    # Check for .sqlmap.xml pattern (iBATIS convention)
    if path.name.endswith(".sqlmap.xml"):
        return FileCategory.XML_SQLMAP, "xml:sqlmap", mime_type

    # Extension-based identification
    category = EXTENSION_MAP.get(ext, FileCategory.UNKNOWN)

    # For XML files, refine by reading content
    if category == FileCategory.XML_CONFIG:
        category, detected = _classify_xml(path)
        return category, detected, mime_type

    if category == FileCategory.UNKNOWN:
        category, detected = _identify_by_content(path)
        return category, detected, mime_type

    return category, ext.lstrip("."), mime_type


def _classify_xml(path: Path) -> tuple[FileCategory, str]:
    """Classify XML files into sub-types by reading a content sample."""
    try:
        # Read raw bytes and decode for classification
        with open(path, "rb") as f:
            raw = f.read(2048)
        # Try utf-8 first, then euc-kr
        try:
            sample = raw.decode("utf-8").lower()
        except UnicodeDecodeError:
            try:
                sample = raw.decode("euc-kr").lower()
            except UnicodeDecodeError:
                sample = raw.decode("utf-8", errors="ignore").lower()
    except OSError:
        return FileCategory.XML_CONFIG, "xml"

    if path.name in BUILD_FILES:
        return FileCategory.BUILD_FILE, f"build:{path.name}"

    if any(indicator.lower() in sample for indicator in XML_SQLMAP_INDICATORS):
        return FileCategory.XML_SQLMAP, "xml:sqlmap"

    if any(indicator.lower() in sample for indicator in XML_SPRING_INDICATORS):
        return FileCategory.XML_SPRING, "xml:spring"

    if any(indicator.lower() in sample for indicator in XML_WEB_INDICATORS):
        return FileCategory.XML_WEB, "xml:web"

    return FileCategory.XML_OTHER, "xml:other"


def _identify_by_content(path: Path) -> tuple[FileCategory, str]:
    """Identify file type by reading content when extension is unknown."""
    try:
        with open(path, "rb") as f:
            header = f.read(512)
    except OSError:
        return FileCategory.UNKNOWN, "unknown"

    # Check if binary
    try:
        header.decode("utf-8")
    except UnicodeDecodeError:
        return FileCategory.BINARY, "binary"

    text_sample = header.decode("utf-8", errors="ignore").strip()

    # Check for XML
    if text_sample.startswith("<?xml") or text_sample.startswith("<"):
        return _classify_xml(path)

    # Check for JSON
    if text_sample.startswith("{") or text_sample.startswith("["):
        return FileCategory.JSON, "json"

    # Check for YAML
    if text_sample.startswith("---") or ": " in text_sample[:200]:
        return FileCategory.YAML, "yaml"

    # Check for properties
    if "=" in text_sample[:200] and not text_sample.startswith("#!"):
        lines = text_sample.split("\n")[:5]
        kv_lines = sum(1 for line in lines if "=" in line and not line.strip().startswith("#"))
        if kv_lines >= 2:
            return FileCategory.PROPERTIES, "properties"

    # Java source는 이미 제외됨 (확장자 체크에서)

    return FileCategory.TEXT, "text"

