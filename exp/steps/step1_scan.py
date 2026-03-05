"""Step 1: 프로젝트 스캔 + 분류. pathlib/os.walk, chardet or BOM."""

from __future__ import annotations

import os
import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from exp.config import get_data_dir, get_source_dir
from exp.models import FileCatalogEntry, ProcessingPlan

logger = logging.getLogger(__name__)

# DDL 폴더는 스캔 제외
SKIP_DIRS = {"ddl", ".git", "node_modules", "__pycache__", ".venv", "venv"}
CATEGORY_JAVA_AST = "java_ast"
CATEGORY_XML_SQLMAP = "xml_sqlmap"
CATEGORY_XML_EJB = "xml_ejb"
CATEGORY_XML_ENTITYMODEL = "xml_entitymodel"
CATEGORY_XML_CMP_MAPPING = "xml_cmp_mapping"
CATEGORY_XML = "xml"  # 시그니처로 세부 구분 못 할 때
CATEGORY_YAML = "yaml"
CATEGORY_PROPERTIES = "properties"
CATEGORY_JSON = "json"
CATEGORY_DOCS = "docs"  # 비정형문서 전용 (etc 사용 금지)


def _detect_encoding_bom(path: Path) -> Optional[str]:
    """BOM으로 인코딩 추정."""
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
    """chardet 사용 (있으면), 없으면 BOM 또는 utf-8."""
    try:
        import chardet
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
    """파일 앞부분만 읽기 (분류용)."""
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except Exception:
        return b""


def _classify_file(path: Path, ext: str, signature: bytes) -> str:
    """확장자 + 시그니처 범용 활용. 구조화 포맷은 해당 타입만, 비정형만 docs. etc 사용 금지."""
    ext_lower = ext.lower()
    sig_str = (signature.decode("utf-8", errors="ignore") if signature else "")[:2048]
    sig_lower = sig_str.lower()

    if ext_lower == ".java":
        return CATEGORY_JAVA_AST

    if ext_lower in (".xml",):
        # 무조건 xml. 시그니처 범용 활용해 세부 구분, 못 구분해도 xml.
        path_lower = path.name.lower()
        # EJB CMP 매핑 XML (weblogic/jboss 등) - 파일명 기반 휴리스틱
        if "cmp-rdbms-jar" in path_lower or "jbosscmp-jdbc" in path_lower:
            return CATEGORY_XML_CMP_MAPPING
        if "ejb-jar" in path_lower or "ejb-jar" in sig_lower:
            return CATEGORY_XML_EJB
        if "entitymodel" in path_lower or "entitymodel" in sig_lower:
            return CATEGORY_XML_ENTITYMODEL
        if "<mapper" in sig_str or ("<?xml" in sig_str and "mapper" in sig_lower):
            return CATEGORY_XML_SQLMAP
        # 그 외 xml은 기본 xml (sqlmap으로 처리하거나 일반 xml)
        return CATEGORY_XML_SQLMAP

    if ext_lower in (".yaml", ".yml"):
        # 시그니처 범용: ---, key:, YAML 패턴
        if "---" in sig_str or ":" in sig_str or "spring:" in sig_lower:
            return CATEGORY_YAML
        return CATEGORY_YAML

    if ext_lower == ".properties":
        return CATEGORY_PROPERTIES

    if ext_lower == ".json":
        return CATEGORY_JSON

    return CATEGORY_DOCS


def _file_hash(path: Path) -> Optional[str]:
    """파일 해시 (선택)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return None


def scan_project(
    project_root: Optional[str] = None,
    scan_roots: Optional[List[Tuple[str, str]]] = None,
    use_pathlib: bool = True,
) -> Tuple[List[FileCatalogEntry], ProcessingPlan]:
    """프로젝트 스캔: source + analysis/nontarget 순회, 경로+타입 분류. DDL 폴더 제외.
    scan_roots가 있으면 [(root_dir, path_prefix), ...] 로 여러 루트 스캔 (path_str = prefix/rel).
    없으면 project_root 하나만 스캔 (path_str = rel)."""
    catalog: List[FileCatalogEntry] = []
    plan = ProcessingPlan()

    if scan_roots:
        # source + nontarget 등 여러 루트 스캔
        files_with_prefix: List[Tuple[Path, str, Path]] = []  # (path, path_prefix, root)
        for root_dir, path_prefix in scan_roots:
            root = Path(root_dir).resolve()
            if not root.is_dir():
                logger.debug("[EXP:STEP1] 스캔 스킵 (디렉터리 없음) | root=%s prefix=%s", root, path_prefix)
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
        logger.debug("[EXP:STEP1] 스캔 진입 | scan_roots=%d use_pathlib=%s", len(scan_roots), use_pathlib)
        for path, path_prefix, root in files_with_prefix:
            try:
                rel = path.resolve().relative_to(root)
                path_str = (path_prefix + "/" + str(rel)).replace("\\", "/")
                _process_one_file(path, path_str, catalog, plan)
            except Exception as e:
                logger.debug("스캔 스킵 %s: %s", path, e)
    else:
        root = Path(project_root or get_source_dir())
        logger.debug("[EXP:STEP1] 스캔 진입 | project_root=%s use_pathlib=%s", root.name, use_pathlib)
        if not root.is_dir():
            logger.warning("data 디렉터리가 없음: %s", root)
            logger.info("[EXP:STEP1] 스킵 | reason=root_not_dir path=%s", root)
            return [], ProcessingPlan()

        if use_pathlib:
            files_to_check = []
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if any(s in p.parts for s in SKIP_DIRS):
                    continue
                if "ddl" in p.parts:
                    continue
                files_to_check.append(p)
        else:
            files_to_check = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "ddl"]
                for name in filenames:
                    files_to_check.append(Path(dirpath) / name)

        for path in files_to_check:
            try:
                rel = path.relative_to(root) if root != path else path.name
                path_str = str(rel).replace("\\", "/")
                _process_one_file(path, path_str, catalog, plan)
            except Exception as e:
                logger.debug("스캔 스킵 %s: %s", path, e)

    # 요약은 pipeline에서 한 번만 출력 (중복 제거)
    logger.debug(
        "[EXP:STEP1] 스캔 완료 | catalog=%d java_ast=%d xml_sqlmap=%d ...",
        len(catalog), len(plan.java_ast_jobs), len(plan.xml_sqlmap_jobs),
    )
    return catalog, plan


def _process_one_file(
    path: Path,
    path_str: str,
    catalog: List[FileCatalogEntry],
    plan: ProcessingPlan,
) -> None:
    """한 파일 스캔 후 catalog/plan에 반영."""
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
        elif category == CATEGORY_XML_EJB:
            plan.xml_ejb_jobs.append(path_str)
        elif category == CATEGORY_XML_ENTITYMODEL:
            plan.xml_entitymodel_jobs.append(path_str)
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
