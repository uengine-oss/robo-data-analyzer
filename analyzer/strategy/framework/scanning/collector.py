"""결정론 수집 — 프로젝트 루트를 한 번 훑어 CodebaseInfo 로 정리.

LLM 호출 없음. 순수 파일시스템 + tree-sitter AST. 0.1~1 초 목표.
리소스 파일 개수 상한 없음 (분류 단계에서 전수 처리 전제).
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from analyzer.strategy.framework.common.constants import SKIP_DIRS
from analyzer.strategy.framework.scanning.models import CodebaseInfo
from analyzer.strategy.framework.scanning.tree_sitter_tools import (
    extract_function_signatures,
    extract_imports_sample,
    extract_skeleton,
    language_extensions,
)

logger = logging.getLogger(__name__)


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


# --- 내부 유틸 --------------------------------------------------------

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
    except OSError:
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
            except OSError:
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
    except OSError:
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


# --- 공개 API ---------------------------------------------------------

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


