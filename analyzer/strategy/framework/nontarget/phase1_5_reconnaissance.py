"""Phase 1.5 (nontarget): Project Reconnaissance - File system traversal, type identification, metadata extraction.

Java 파일은 제외됩니다 (ANTLR로 처리).
"""

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path
from typing import Any, AsyncGenerator

from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.nontarget.data_models import (
    FileCategory,
    FileInfo,
    PhaseResult,
    PhaseStatus,
    ProjectStructure,
)
from analyzer.strategy.framework.nontarget.file_identifier import identify_file_type


async def run_phase1_5_reconnaissance(
    root_path: Path,
    exclude_patterns: list[str] | None = None,
    max_file_size_mb: float = 10.0,
) -> AsyncGenerator[bytes, None]:
    """Phase 1.5 (nontarget): 프로젝트 정찰 - 파일 시스템 탐색, 타입 식별, 메타데이터 추출
    
    Java 파일은 제외됩니다 (.java, .kt 확장자 필터링).
    
    Args:
        root_path: 프로젝트 루트 경로
        exclude_patterns: 제외 패턴 리스트 (기본값: 일반적인 제외 패턴)
        max_file_size_mb: 최대 파일 크기 (MB)
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    if exclude_patterns is None:
        exclude_patterns = [
            "*.class", "*.jar", "*.war", "*.ear",
            ".git/*", ".svn/*", "node_modules/*",
            "target/*", "build/*", ".idea/*", ".vscode/*",
            "*.pyc", "__pycache__/*",
        ]
    
    # Phase 1.5 시작 이벤트
    yield emit_phase_event(1.5, "비타겟 파일 정찰", "started", 0, {"canPause": True})
    
    try:
        # 1-A: File system traverse
        log_process("ANALYZE", "PHASE_1.5", "파일 시스템 탐색 중...")
        files = await _traverse_filesystem(root_path, exclude_patterns, max_file_size_mb)
        log_process("ANALYZE", "PHASE_1.5", f"파일 {len(files)}개 발견")
        
        # Java 파일 제외
        files = [f for f in files if not f.path.suffix.lower() in (".java", ".kt")]
        log_process("ANALYZE", "PHASE_1.5", f"Java 파일 제외 후 {len(files)}개 파일")
        
        # 1-B: File type identification
        log_process("ANALYZE", "PHASE_1.5", "파일 타입 식별 중...")
        await _identify_file_types(files)
        
        # 1-C: Project metadata extraction
        log_process("ANALYZE", "PHASE_1.5", "프로젝트 메타데이터 추출 중...")
        project = _build_project_structure(root_path, files)
        _extract_project_metadata(project)
        
        # Log file type distribution
        file_type_counts = {}
        for f in files:
            cat = f.category.value
            file_type_counts[cat] = file_type_counts.get(cat, 0) + 1
        
        log_process("ANALYZE", "PHASE_1.5", f"파일 타입 분포: {file_type_counts}")
        
        # 1-D: Build output
        result_data = {
            "project_structure": project,
            "file_inventory": files,
            "framework_profile": project.framework_profile,
        }
        
        log_process(
            "ANALYZE", "PHASE_1.5",
            f"요약: {project.total_files}개 파일, build_tool={project.build_tool or 'unknown'}, "
            f"frameworks={list(project.framework_profile.get('frameworks', {}).keys()) or 'none'}"
        )
        
        # 스트리밍 (파일별)
        for file_info in files:
            yield emit_message(f"   ✓ {file_info.relative_path} ({file_info.category.value})")
        
        # Phase 1.5 완료 이벤트
        yield emit_phase_event(1.5, "비타겟 파일 정찰", "completed", 100, {
            "total_files": project.total_files,
            "file_type_counts": file_type_counts,
        })
        
        # Phase 결과 반환
        yield emit_phase_result(1.5, {
            "project_structure": project,
            "file_inventory": files,
            "framework_profile": project.framework_profile,
        })
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 1.5 실패: {e}", exc_info=True)
        yield emit_phase_event(1.5, "비타겟 파일 정찰", "failed", 0, {"error": str(e)})
        raise


async def _traverse_filesystem(
    root_path: Path,
    exclude_patterns: list[str],
    max_file_size_mb: float,
) -> list[FileInfo]:
    """Recursively traverse the directory tree and collect file information."""
    files: list[FileInfo] = []
    max_size = int(max_file_size_mb * 1024 * 1024)
    
    # 비동기로 파일 탐색 (큰 디렉토리에서 성능 향상)
    async def process_path(path: Path):
        if not path.is_file():
            return None
        
        relative = str(path.relative_to(root_path))
        
        # Check exclusion patterns
        if _is_excluded(relative, exclude_patterns):
            return None
        
        try:
            size = path.stat().st_size
        except OSError:
            return None
        
        if size > max_size:
            log_process("ANALYZE", "PHASE_1.5", f"큰 파일 건너뜀: {relative} ({size} bytes)", level="DEBUG")
            return None
        
        encoding = _detect_encoding(path)
        
        return FileInfo(
            path=path,
            relative_path=relative,
            extension=path.suffix.lower(),
            size=size,
            encoding=encoding,
        )
    
    # 모든 파일 병렬 처리
    paths = list(root_path.rglob("*"))
    tasks = [process_path(path) for path in paths]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    files = [f for f in results if f is not None and not isinstance(f, Exception)]
    
    return files


def _is_excluded(relative_path: str, exclude_patterns: list[str]) -> bool:
    """Check if a file path matches any exclusion pattern."""
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(relative_path, pattern):
            return True
        # Also check with forward slashes normalized
        if fnmatch.fnmatch(relative_path.replace("\\", "/"), pattern):
            return True
    return False


def _detect_encoding(path: Path) -> str:
    """Detect file encoding with EUC-KR support."""
    try:
        with open(path, "rb") as f:
            raw = f.read(512)
        # Check BOM
        if raw.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if raw.startswith(b"\xff\xfe"):
            return "utf-16-le"
        if raw.startswith(b"\xfe\xff"):
            return "utf-16-be"
        
        # Check for XML encoding declaration
        header_str = raw[:200].decode("ascii", errors="ignore")
        import re
        enc_match = re.search(r'encoding=["\']([^"\']+)["\']', header_str)
        if enc_match:
            declared = enc_match.group(1).lower()
            if declared in ("euc-kr", "euc_kr", "euckr"):
                return "euc-kr"
            return declared
        
        # Try UTF-8 first
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        
        # Try EUC-KR (common in Korean legacy projects)
        try:
            raw.decode("euc-kr")
            return "euc-kr"
        except UnicodeDecodeError:
            pass
        
        return "utf-8"
    except OSError:
        return "utf-8"


async def _identify_file_types(files: list[FileInfo]) -> None:
    """Identify the type of each file."""
    for file_info in files:
        category, detected_type, mime_type = identify_file_type(file_info.path)
        file_info.category = category
        file_info.detected_type = detected_type
        file_info.mime_type = mime_type


def _build_project_structure(root_path: Path, files: list[FileInfo]) -> ProjectStructure:
    """Build the project structure from discovered files."""
    type_counts: dict[str, int] = {}
    for f in files:
        cat = f.category.value
        type_counts[cat] = type_counts.get(cat, 0) + 1
    
    structure = ProjectStructure(
        root_path=root_path,
        files=files,
        directory_tree=_build_directory_tree(files),
        total_files=len(files),
        file_type_counts=type_counts,
    )
    return structure


def _build_directory_tree(files: list[FileInfo]) -> dict[str, Any]:
    """Build a nested dictionary representing the directory tree."""
    tree: dict[str, Any] = {}
    for f in files:
        parts = f.relative_path.replace("\\", "/").split("/")
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = f.category.value
    return tree


def _extract_project_metadata(project: ProjectStructure) -> None:
    """Extract project metadata like build tool, framework versions, modules."""
    # Detect build tool
    for f in project.files:
        if f.category == FileCategory.BUILD_FILE:
            if "pom.xml" in f.relative_path:
                project.build_tool = "maven"
                _extract_maven_metadata(f.path, project)
            elif "build.gradle" in f.relative_path:
                project.build_tool = "gradle"
            elif "build.xml" in f.relative_path:
                project.build_tool = "ant"
            break
    
    # Detect frameworks from file types
    frameworks: dict[str, Any] = {}
    has_spring = any(f.category == FileCategory.XML_SPRING for f in project.files)
    has_ibatis = any(f.category == FileCategory.XML_SQLMAP for f in project.files)
    
    if has_spring:
        frameworks["spring"] = {"detected": True}
    if has_ibatis:
        frameworks["ibatis"] = {"detected": True}
    
    # Java source는 이미 제외됨
    
    project.framework_profile = {
        "frameworks": frameworks,
        "java_source_roots": [],  # Java는 제외
    }
    
    # Detect modules (multi-module Maven project)
    if project.build_tool == "maven":
        _detect_maven_modules(project)


def _extract_maven_metadata(pom_path: Path, project: ProjectStructure) -> None:
    """Extract metadata from pom.xml."""
    try:
        from lxml import etree
        tree = etree.parse(str(pom_path))
        root = tree.getroot()
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}
        
        # Try with namespace first, then without
        for prefix in [ns, {}]:
            p = "m:" if prefix else ""
            java_ver = root.findtext(f".//{p}maven.compiler.source", namespaces=prefix or None)
            if not java_ver:
                java_ver = root.findtext(f".//{p}java.version", namespaces=prefix or None)
            if java_ver:
                project.java_version = java_ver
                break
    except Exception as e:
        log_process("ANALYZE", "PHASE_1.5", f"pom.xml 파싱 실패: {e}", level="DEBUG")


def _detect_maven_modules(project: ProjectStructure) -> None:
    """Detect Maven sub-modules."""
    try:
        from lxml import etree
        pom_path = project.root_path / "pom.xml"
        if not pom_path.exists():
            return
        tree = etree.parse(str(pom_path))
        root = tree.getroot()
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}
        
        modules = root.findall(".//m:module", ns)
        if not modules:
            modules = root.findall(".//module")
        
        project.modules = [m.text for m in modules if m.text]
    except Exception as e:
        log_process("ANALYZE", "PHASE_1.5", f"Maven 모듈 감지 실패: {e}", level="DEBUG")

