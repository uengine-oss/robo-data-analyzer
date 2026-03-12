"""Step 1: 프로젝트 스캔 (기존 step1_scan 통합)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

from config.settings import settings
from analyzer.strategy.framework.common.constants import (
    SKIP_DIRS,
    CATEGORY_JAVA_AST,
    CATEGORY_XML_SQLMAP,
    CATEGORY_XML_ARTIFACT,
    CATEGORY_XML_CMP_MAPPING,
    CATEGORY_XML,
    CATEGORY_YAML,
    CATEGORY_PROPERTIES,
    CATEGORY_JSON,
    CATEGORY_DOCS,
)
from analyzer.strategy.framework.models import (
    FileCatalogEntry,
    ProcessingPlan,
)

logger = logging.getLogger(__name__)


def _detect_encoding_bom(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            raw = f.read(4)
        if raw.startswith(b"\xef\xbb\xbf"):
            return "utf-8"
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return "utf-16"
        return None
    except Exception:
        return None


def _detect_encoding_chardet(path: Path) -> str:
    try:
        import chardet  # type: ignore[import]

        with open(path, "rb") as f:
            raw = f.read(8192)
        result = chardet.detect(raw)
        if result and result.get("encoding"):
            return result["encoding"] or "utf-8"
    except ImportError:
        pass
    enc = _detect_encoding_bom(path)
    return enc or "utf-8"


def _read_signature(path: Path, max_bytes: int = 2048) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except Exception:
        return b""


def _classify_file(path: Path, ext: str, signature: bytes) -> str:
    ext_lower = ext.lower()
    sig_str = (signature.decode("utf-8", errors="ignore") if signature else "")[:2048]
    sig_lower = sig_str.lower()

    if ext_lower == ".java":
        return CATEGORY_JAVA_AST

    if ext_lower in (".xml",):
        path_lower = path.name.lower()
        if "cmp-rdbms-jar" in path_lower or "jbosscmp-jdbc" in path_lower:
            return CATEGORY_XML_CMP_MAPPING
        if "ejb-jar" in path_lower or "ejb-jar" in sig_lower:
            return CATEGORY_XML_ARTIFACT
        if "entitymodel" in path_lower or "entitymodel" in sig_lower:
            return CATEGORY_XML_ARTIFACT
        if "<mapper" in sig_str or ("<?xml" in sig_str and "mapper" in sig_lower):
            return CATEGORY_XML_SQLMAP
        return CATEGORY_XML

    if ext_lower in (".yaml", ".yml"):
        return CATEGORY_YAML

    if ext_lower == ".properties":
        return CATEGORY_PROPERTIES

    if ext_lower == ".json":
        return CATEGORY_JSON

    return CATEGORY_DOCS


def _file_hash(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return None


def _process_one_file(
    path: Path,
    path_str: str,
    catalog: List[FileCatalogEntry],
    plan: ProcessingPlan,
) -> None:
    try:
        stat = path.stat()
        ext = path.suffix
        signature = _read_signature(path)
        encoding = _detect_encoding_chardet(path)
        category = _classify_file(path, ext, signature)
        entry = FileCatalogEntry(
            path=path_str,
            ext=ext,
            size=stat.st_size,
            encoding=encoding,
            category=category,
            hash_val=_file_hash(path),
        )
        catalog.append(entry)
        if category == CATEGORY_JAVA_AST:
            plan.java_ast_jobs.append(path_str)
        elif category == CATEGORY_XML_SQLMAP:
            plan.xml_sqlmap_jobs.append(path_str)
        elif category == CATEGORY_XML_ARTIFACT:
            plan.xml_artifact_jobs.append(path_str)
        elif category == CATEGORY_XML_CMP_MAPPING:
            plan.xml_cmp_mapping_jobs.append(path_str)
        elif category == CATEGORY_YAML:
            plan.yaml_jobs.append(path_str)
        elif category == CATEGORY_PROPERTIES:
            plan.properties_jobs.append(path_str)
        elif category == CATEGORY_JSON:
            plan.json_jobs.append(path_str)
        else:
            plan.docs_jobs.append(path_str)
    except Exception as e:
        logger.debug("스캔 스킵 %s: %s", path, e)


def scan_project(
    project_root: Optional[str] = None,
    scan_roots: Optional[List[Tuple[str, str]]] = None,
    use_pathlib: bool = True,
) -> Tuple[List[FileCatalogEntry], ProcessingPlan]:
    catalog: List[FileCatalogEntry] = []
    plan = ProcessingPlan()

    if scan_roots:
        files_with_prefix: List[Tuple[Path, str, Path]] = []
        for root_dir, path_prefix in scan_roots:
            root = Path(root_dir).resolve()
            if not root.is_dir():
                logger.debug("[FW:STEP1] 스캔 스킵 (디렉터리 없음) | root=%s prefix=%s", root, path_prefix)
                continue
            if use_pathlib:
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    if any(s in p.parts for s in SKIP_DIRS):
                        continue
                    if "ddl" in p.parts:
                        continue
                    files_with_prefix.append((p, path_prefix, root))
            else:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "ddl"]
                    for name in filenames:
                        p = Path(dirpath) / name
                        files_with_prefix.append((p, path_prefix, root))
        logger.debug("[FW:STEP1] 스캔 진입 | scan_roots=%d use_pathlib=%s", len(scan_roots), use_pathlib)
        for path, path_prefix, root in files_with_prefix:
            try:
                rel = path.resolve().relative_to(root)
                path_str = (path_prefix + "/" + str(rel)).replace("\\", "/")
                _process_one_file(path, path_str, catalog, plan)
            except Exception as e:
                logger.debug("스캔 스킵 %s: %s", path, e)
    else:
        base_dir = Path(project_root or (Path(settings.path.data_dir) / "source"))
        logger.debug("[FW:STEP1] 스캔 진입 | project_root=%s use_pathlib=%s", base_dir, use_pathlib)
        if not base_dir.is_dir():
            logger.warning("data 디렉터리가 없음: %s", base_dir)
            logger.info("[FW:STEP1] 스킵 | reason=root_not_dir path=%s", base_dir)
            return [], ProcessingPlan()

        if use_pathlib:
            files_to_check: List[Path] = []
            for p in base_dir.rglob("*"):
                if not p.is_file():
                    continue
                if any(s in p.parts for s in SKIP_DIRS):
                    continue
                if "ddl" in p.parts:
                    continue
                files_to_check.append(p)
        else:
            files_to_check = []
            for dirpath, dirnames, filenames in os.walk(base_dir):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "ddl"]
                for name in filenames:
                    files_to_check.append(Path(dirpath) / name)

        for path in files_to_check:
            try:
                rel = path.relative_to(base_dir) if base_dir != path else path.name
                path_str = str(rel).replace("\\", "/")
                _process_one_file(path, path_str, catalog, plan)
            except Exception as e:
                logger.debug("스캔 스킵 %s: %s", path, e)

    logger.debug(
        "[FW:STEP1] 스캔 완료 | catalog=%d java_ast=%d xml_sqlmap=%d",
        len(catalog),
        len(plan.java_ast_jobs),
        len(plan.xml_sqlmap_jobs),
    )
    return catalog, plan
