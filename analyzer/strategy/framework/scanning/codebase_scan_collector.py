"""코드베이스 스캔 + 결정론 메타 수집.

원본 위치 (이전):
  - `analyzer/strategy/framework/scanning/file_classifier.py` (Step 1: 파일 분류)
  - `analyzer/strategy/framework/scanning/metadata_collector.py` (Step 2: 결정론 수집)
  - `analyzer/strategy/framework/scanning/profile_models.py` (CodebaseInfo dataclass)

책임 (한 도메인 = 한 파일 — 코드베이스 정적 스캔/수집):
  1. `scan_project()` — 프로젝트 루트 스캔 + 파일 분류 (FileCatalogEntry + ProcessingPlan).
  2. `collect()` — 확장자/매니페스트/imports/대표 파일 뼈대/함수 인덱스 수집.
  3. `CodebaseInfo` dataclass — 결정론 수집 결과 한 덩어리.

LLM 호출 없음. 순수 파일시스템 + tree-sitter AST. 0.1~1 초 목표.
호출자 (codebase_profiler) 가 본 모듈을 호출해 카탈로그 + 메타 모음 후
scanning agent 에 자연어 요약을 시킨다.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import chardet  # type: ignore[import]

from analyzer.strategy.framework.common.domain.analysis_constants import (
    SKIP_DIRS,
    CATEGORY_SOURCE_AST,
    CATEGORY_SQL,
    CATEGORY_ENTITY,
    CATEGORY_CONFIG,
    CATEGORY_DOCS,
)
from analyzer.strategy.framework.common.models.node_models import (
    FileCatalogEntry,
    ProcessingPlan,
)
from analyzer.strategy.framework.common.domain.ontology import get_ontology
from analyzer.strategy.framework.scanning.tree_sitter_tools import (
    extract_function_signatures,
    extract_imports_sample,
    extract_skeleton,
    language_extensions,
)
from config.settings import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 결정론 수집 결과 데이터 모델 (구 profile_models.CodebaseInfo)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class CodebaseInfo:
    """결정론 수집 결과 한 덩어리."""

    root: str
    primary_language: Optional[str] = None

    extensions: dict[str, int] = field(default_factory=dict)

    manifest_name: str = ""
    manifest_content: str = ""
    config_name: str = ""
    config_content: str = ""

    import_frequency: dict[str, int] = field(default_factory=dict)
    sample_file_count: int = 0

    representative_file: str = ""
    representative_skeleton: str = ""
    function_index: list[tuple[str, list[str]]] = field(default_factory=list)

    standard_resources: list[str] = field(default_factory=list)
    custom_resources: list[tuple[str, str]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Step 1: 프로젝트 스캔 + 파일 분류 (구 file_classifier)
# ═══════════════════════════════════════════════════════════════════════════


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
    except Exception as e:  # noqa: SILENT_FAIL — BOM 감지는 옵셔널 휴리스틱, 실패는 chardet 에 위임
        logger.debug("[FILE_CLASSIFIER] BOM 감지 실패 | path=%s | %s: %s", path, type(e).__name__, e)
        return None


def _detect_encoding_chardet(path: Path) -> str:
    """chardet 으로 인코딩 감지. chardet 은 requirements 강제 의존이라 항상 사용."""
    with open(path, "rb") as f:
        raw = f.read(8192)
    result = chardet.detect(raw)
    if result and result.get("encoding"):
        return result["encoding"] or "utf-8"
    enc = _detect_encoding_bom(path)
    return enc or "utf-8"


def _read_signature(path: Path, max_bytes: int = 2048) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except Exception as e:  # noqa: SILENT_FAIL — 시그니처 읽기 실패는 빈 시그니처로 간주, _classify_file 가 ext 로 분류
        logger.error("[FILE_CLASSIFIER] 시그니처 읽기 실패 | path=%s | %s: %s", path, type(e).__name__, e)
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


def _file_hash(path: Path) -> str:
    """파일 SHA256 해시(앞 16자) 반환. 읽기 실패는 fail-fast."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except Exception as exc:
        logger.error(
            "[FILE_CLASSIFIER] 파일 해시 계산 실패 | path=%s | %s: %s",
            path, type(exc).__name__, exc, exc_info=True,
        )
        raise RuntimeError(
            f"파일 해시 계산 실패: {path} ({type(exc).__name__}: {exc})"
        ) from exc
    return h.hexdigest()[:16]


def _process_one_file(
    path: Path,
    path_str: str,
    catalog: List[FileCatalogEntry],
    plan: ProcessingPlan,
    lang: str,
) -> None:
    """단일 파일을 분류하여 catalog/plan 에 추가. 실패는 fail-fast."""
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
    except Exception as e:
        logger.error(
            "[FILE_CLASSIFIER] 파일 분류 실패 | path=%s | %s: %s",
            path, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"파일 분류 실패: {path} ({type(e).__name__}: {e})"
        ) from e
    catalog.append(entry)
    # category → plan job list 라우팅 (미매칭 카테고리는 docs_jobs)
    getattr(plan, _CATEGORY_JOB_ATTR.get(category, "docs_jobs")).append(path_str)


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
                logger.error(
                    "[PHASE:1:SCAN] 파일 처리 실패 | path=%s | %s: %s",
                    path, type(e).__name__, e, exc_info=True,
                )
                raise
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
                logger.error(
                    "[PHASE:1:SCAN] 파일 처리 실패 | path=%s | %s: %s",
                    path, type(e).__name__, e, exc_info=True,
                )
                raise

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


# ═══════════════════════════════════════════════════════════════════════════
# Step 2: 결정론 메타 수집 (구 metadata_collector)
# ═══════════════════════════════════════════════════════════════════════════


# 매니페스트: 프로젝트 기술 파일 (프레임워크 판별 핵심).
WELL_KNOWN_MANIFESTS = [
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", "requirements.txt", "pyproject.toml",
    "Pipfile", "Gemfile", "composer.json", "Makefile", "CMakeLists.txt",
    "mix.exs", "rebar.config", "pubspec.yaml",
]

# 런타임 접속/환경 설정 파일.
CONFIG_PATTERNS = [
    "application.yml", "application.yaml", "application.properties",
    ".env", "docker-compose.yml", "docker-compose.yaml",
    "settings.py", "config.py", "schema.prisma",
    "tsconfig.json", "nest-cli.json",
]

# 이름만으로 역할 확정되는 표준 리소스 파일.
STANDARD_RESOURCE_NAMES = frozenset({
    "ejb-jar.xml", "web.xml", "persistence.xml", "orm.xml",
    "beans.xml", "faces-config.xml", "application.xml",
    "logback.xml", "logback-spring.xml",
    "log4j.xml", "log4j2.xml",
    "log4j.properties", "log4j2.properties",
    "mybatis-config.xml",
})

# LLM 에게 보여줄 리소스 확장자 (프로그래밍 언어 소스 제외).
_RESOURCE_EXTS = (".xml", ".yml", ".yaml", ".sql", ".properties")


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _count_extensions(root: Path) -> dict[str, int]:
    counter: Counter = Counter()
    for p in _iter_files(root):
        suf = p.suffix.lower()
        if suf:
            counter[suf] += 1
    return dict(counter.most_common(20))


def _find_first(root: Path, names: list[str]) -> Optional[Path]:
    for p in _iter_files(root):
        if p.name in names:
            return p
    return None


def _read_head(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # noqa: SILENT_FAIL — 메타데이터 수집 보조 파일, 읽기 실패는 빈 컨텐츠로 진행
        return ""
    return "\n".join(text.splitlines()[:lines])


def _pick_representative_file(
    root: Path,
    language: Optional[str],
    imports: dict[str, int],
) -> tuple[str, str]:
    """주 언어 파일 중 대표 1 개 선택 → 뼈대 추출."""
    if not language:
        return "", ""
    exts = language_extensions(language)
    candidates = [p for p in _iter_files(root) if p.suffix.lower() in exts]
    if not candidates:
        return "", ""

    chosen = candidates[0]
    if imports:
        top_tokens = [k.split()[-1].strip("<>\";") for k in list(imports.keys())[:5]]
        for cand in candidates:
            try:
                head = cand.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:  # noqa: SILENT_FAIL — 대표 파일 후보 검색, 읽기 실패는 다음 후보로 진행
                continue
            if any(tok in head for tok in top_tokens):
                chosen = cand
                break
    return str(chosen), extract_skeleton(chosen, max_items=80)


def _first_meaningful_lines(path: Path, n: int = 4, max_chars: int = 160) -> list[str]:
    """파일 앞쪽에서 빈 줄·보일러플레이트 뺀 라인 n 개."""
    results: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith(("<?xml", "<?", "<!DOCTYPE", "#!")):
                    continue
                if line.lower().startswith("# -*-"):
                    continue
                results.append(line[:max_chars])
                if len(results) >= n:
                    break
    except OSError:  # noqa: SILENT_FAIL — 보조 컨텐츠 수집 실패는 빈 결과로 진행
        return []
    return results


def _scan_resources(root: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """리소스 파일을 표준/커스텀으로 분리. **개수 상한 없음** — 전수 수집."""
    already_captured = set(WELL_KNOWN_MANIFESTS) | set(CONFIG_PATTERNS)
    standard: list[str] = []
    custom: list[tuple[str, str]] = []

    candidates: list[Path] = []
    for p in _iter_files(root):
        if p.name in already_captured:
            continue
        if p.suffix.lower() not in _RESOURCE_EXTS:
            continue
        candidates.append(p)
    candidates.sort(key=lambda p: (len(p.parts), str(p).lower()))

    for p in candidates:
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(p)
        if p.name in STANDARD_RESOURCE_NAMES:
            standard.append(rel)
            continue
        preview_lines = _first_meaningful_lines(p)
        if not preview_lines:
            continue
        custom.append((rel, " | ".join(preview_lines)))
    return standard, custom


def collect(root: str, sample_size: int = 30) -> CodebaseInfo:
    """폴더를 한 번 훑어 CodebaseInfo 로 반환."""
    root_path = Path(root).resolve()
    info = CodebaseInfo(root=str(root_path))

    info.extensions = _count_extensions(root_path)

    manifest = _find_first(root_path, WELL_KNOWN_MANIFESTS)
    if manifest:
        info.manifest_name = manifest.name
        info.manifest_content = _read_head(manifest, 60)

    config = _find_first(root_path, CONFIG_PATTERNS)
    if config:
        info.config_name = config.name
        info.config_content = _read_head(config, 40)

    imports, sampled, lang = extract_imports_sample(str(root_path), sample_size=sample_size)
    info.import_frequency = imports
    info.sample_file_count = sampled
    info.primary_language = lang

    rep_path, rep_skeleton = _pick_representative_file(root_path, lang, imports)
    info.representative_file = rep_path
    info.representative_skeleton = rep_skeleton

    # 함수 인덱스 — 주 언어 파일 중 큰 순으로 상위 8 개의 함수 시그니처 목록.
    if lang:
        exts = language_extensions(lang)
        src_files = [p for p in _iter_files(root_path) if p.suffix.lower() in exts]
        src_files.sort(key=lambda p: -p.stat().st_size if p.exists() else 0)
        idx: list[tuple[str, list[str]]] = []
        for p in src_files[:8]:
            sigs = extract_function_signatures(p, max_sigs=15)
            if not sigs:
                continue
            try:
                rel = str(p.relative_to(root_path)).replace("\\", "/")
            except ValueError:
                rel = str(p)
            idx.append((rel, sigs))
        info.function_index = idx

    info.standard_resources, info.custom_resources = _scan_resources(root_path)
    return info


__all__ = [
    "CodebaseInfo",
    "scan_project",
    "collect",
    "WELL_KNOWN_MANIFESTS",
    "CONFIG_PATTERNS",
    "STANDARD_RESOURCE_NAMES",
]
