"""Step 1: 프로젝트 스캔 (기존 step1_scan 통합)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

from config.settings import settings
from analyzer.strategy.framework.common.analysis_constants import (
    SKIP_DIRS,
    CATEGORY_SOURCE_AST,
    CATEGORY_SQL,
    CATEGORY_ENTITY,
    CATEGORY_CONFIG,
    CATEGORY_DOCS,
)
from analyzer.strategy.framework.common.ontology import get_ontology
from analyzer.strategy.framework.common.node_models import (
    FileCatalogEntry,
    ProcessingPlan,
)

logger = logging.getLogger(__name__)

# category → ProcessingPlan 속성명 매핑 (라우팅 테이블)
_CATEGORY_JOB_ATTR = {
    CATEGORY_SOURCE_AST: "source_ast_jobs",
    CATEGORY_SQL: "sql_jobs",
    CATEGORY_ENTITY: "entity_jobs",
    CATEGORY_CONFIG: "config_jobs",
    CATEGORY_DOCS: "docs_jobs",
}


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


def _classify_file(path: Path, ext: str, signature: bytes, lang: str) -> str:
    ext_lower = ext.lower()
    sig_str = (signature.decode("utf-8", errors="ignore") if signature else "")[:2048]
    sig_lower = sig_str.lower()

    onto = get_ontology()
    if ext_lower in onto.source_extensions(lang):
        return CATEGORY_SOURCE_AST

    if ext_lower in (".xml",):
        # 시그니처(내용) 기반 분류 — 태그로만 판단
        if "<entity" in sig_lower or "<hibernate-mapping" in sig_lower:
            return CATEGORY_ENTITY
        if "<mapper" in sig_lower or "<sqlmap" in sig_lower:
            return CATEGORY_SQL
        return CATEGORY_CONFIG

    if ext_lower in (".yaml", ".yml"):
        return CATEGORY_CONFIG

    if ext_lower == ".properties":
        return CATEGORY_CONFIG

    if ext_lower == ".json":
        return CATEGORY_CONFIG

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
    lang: str,
) -> None:
    try:
        stat = path.stat()
        ext = path.suffix
        signature = _read_signature(path)
        encoding = _detect_encoding_chardet(path)
        category = _classify_file(path, ext, signature, lang=lang)
        entry = FileCatalogEntry(
            path=path_str,
            ext=ext,
            size=stat.st_size,
            encoding=encoding,
            category=category,
            hash_val=_file_hash(path),
        )
        catalog.append(entry)
        # category → plan job list 라우팅 (미매칭 카테고리는 docs_jobs 로 폴백)
        getattr(plan, _CATEGORY_JOB_ATTR.get(category, "docs_jobs")).append(path_str)
    except Exception as e:
        logger.debug("스캔 스킵 %s: %s", path, e)


def scan_project(
    lang: str,
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
                logger.debug("[PHASE:1:SCAN] 스캔 스킵 (디렉터리 없음) | root=%s prefix=%s", root, path_prefix)
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
        logger.debug("[PHASE:1:SCAN] 스캔 진입 | scan_roots=%d use_pathlib=%s", len(scan_roots), use_pathlib)
        for path, path_prefix, root in files_with_prefix:
            try:
                rel = path.resolve().relative_to(root)
                path_str = (path_prefix + "/" + str(rel)).replace("\\", "/")
                _process_one_file(path, path_str, catalog, plan, lang=lang)
            except Exception as e:
                logger.debug("스캔 스킵 %s: %s", path, e)
    else:
        base_dir = Path(project_root or (Path(settings.path.data_dir) / "source"))
        logger.debug("[PHASE:1:SCAN] 스캔 진입 | project_root=%s use_pathlib=%s", base_dir, use_pathlib)
        if not base_dir.is_dir():
            logger.error("[PHASE:1:SCAN] data 디렉터리 없음 | path=%s", base_dir)
            logger.info("[PHASE:1:SCAN] 스킵 | reason=root_not_dir path=%s", base_dir)
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
                _process_one_file(path, path_str, catalog, plan, lang=lang)
            except Exception as e:
                logger.debug("스캔 스킵 %s: %s", path, e)

    logger.debug(
        "[PHASE:1:SCAN] 스캔 완료 | catalog=%d source_ast=%d sql=%d entity=%d config=%d lang=%s",
        len(catalog),
        len(plan.source_ast_jobs),
        len(plan.sql_jobs),
        len(plan.entity_jobs),
        len(plan.config_jobs),
        lang,
    )
    return catalog, plan
