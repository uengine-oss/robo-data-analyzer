"""결정론 수집 — 0.1 초 이내.

폴더 하나를 받아 프레임워크·DB 분석에 쓸 증거를 한 덩어리로 모은다.
LLM 호출 없음. 오직 파일 시스템 스캔과 tree-sitter AST 기반 추출.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .tools.tree_sitter_tools import (
    SKIP_DIRS,
    _ALIAS_EXT_TO_LANG,
    extract_function_signatures,
    extract_imports_sample,
    extract_skeleton,
)

# --- 상수 ---------------------------------------------------------------

# 매니페스트: 언어·빌드 시스템의 프로젝트 기술 파일. 본문이 프레임워크 판별 핵심 증거.
WELL_KNOWN_MANIFESTS = [
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", "requirements.txt", "pyproject.toml",
    "Pipfile", "Gemfile", "composer.json", "Makefile", "CMakeLists.txt",
    "mix.exs", "rebar.config", "pubspec.yaml",
]

# 설정 파일: 런타임 접속/환경 정보. DB 연결 단서가 흔히 들어있음.
CONFIG_PATTERNS = [
    "application.yml", "application.yaml", "application.properties",
    ".env", "docker-compose.yml", "docker-compose.yaml",
    "settings.py", "config.py", "schema.prisma",
    "tsconfig.json", "nest-cli.json",
]

# W3C/JCP 스펙·공식 도구가 고정한 리소스 파일 이름. 내용 피크 생략.
STANDARD_RESOURCE_NAMES = frozenset({
    # Java EE / Jakarta EE
    "ejb-jar.xml", "web.xml", "persistence.xml", "orm.xml",
    "beans.xml", "faces-config.xml", "application.xml",
    # 로깅 표준
    "logback.xml", "logback-spring.xml",
    "log4j.xml", "log4j2.xml",
    "log4j.properties", "log4j2.properties",
    # MyBatis 프레임워크 고정 이름
    "mybatis-config.xml",
})

# LLM 에게 보여줄 리소스 확장자. 프로그래밍 언어 소스 파일은 제외.
_RESOURCE_EXTS = (".xml", ".yml", ".yaml", ".sql", ".properties")


# --- 자료 구조 ---------------------------------------------------------

@dataclass
class CodebaseInfo:
    """collect() 가 만드는 한 덩어리 증거 상자."""

    root: str
    primary_language: Optional[str] = None

    # 프로젝트 지문용
    extensions: dict[str, int] = field(default_factory=dict)

    # 매니페스트/설정 — 본문 포함
    manifest_name: str = ""
    manifest_content: str = ""
    config_name: str = ""
    config_content: str = ""

    # 소스 증거
    import_frequency: dict[str, int] = field(default_factory=dict)
    sample_file_count: int = 0
    representative_file: str = ""
    representative_skeleton: str = ""
    function_index: list[tuple[str, list[str]]] = field(default_factory=list)

    # 리소스 파일 목록. 표준/커스텀 분리.
    standard_resources: list[str] = field(default_factory=list)
    custom_resources: list[tuple[str, str]] = field(default_factory=list)


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
    """주 언어 파일 중 대표 1 개 선택 → tree-sitter 로 뼈대 추출."""
    if not language:
        return "", ""
    exts = [e for e, l in _ALIAS_EXT_TO_LANG.items() if l == language]
    candidates = [p for p in _iter_files(root) if p.suffix.lower() in exts]
    if not candidates:
        return "", ""

    chosen = candidates[0]
    if imports:
        top_tokens = [
            k.split()[-1].strip("<>\";")
            for k in list(imports.keys())[:5]
        ]
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
    """파일 앞쪽에서 빈 줄·보일러플레이트를 뺀 라인 n 개. 주석도 포함한다."""
    results: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("<?xml") or line.startswith("<?"):
                    continue
                if line.startswith("<!DOCTYPE"):
                    continue
                if line.startswith("#!"):
                    continue
                if line.lower().startswith("# -*-"):
                    continue
                results.append(line[:max_chars])
                if len(results) >= n:
                    break
    except OSError:
        return []
    return results


def _scan_resources(
    root: Path,
    max_files: int = 120,
) -> tuple[list[str], list[tuple[str, str]]]:
    """리소스 파일을 표준/커스텀으로 나눠 반환.

    표준 (STANDARD_RESOURCE_NAMES): 이름만. 내용 피크 없음.
    커스텀: 경로 + 앞쪽 의미 라인 미리보기.
    매니페스트/설정으로 이미 본문 수집되는 파일은 제외.
    """
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

    for p in candidates[:max_files]:
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
    """폴더를 한 번 훑어 CodebaseInfo 로 반환. 1 초 미만."""
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

    imports, sampled, lang = extract_imports_sample(
        str(root_path), sample_size=sample_size
    )
    info.import_frequency = imports
    info.sample_file_count = sampled
    info.primary_language = lang

    rep_path, rep_skeleton = _pick_representative_file(root_path, lang, imports)
    info.representative_file = rep_path
    info.representative_skeleton = rep_skeleton

    # 함수 인덱스 — 주 언어 파일 중 큰 순으로 상위 N 개의 함수 시그니처 목록.
    # LLM 이 (file, function) 을 이 목록에서만 선택하도록 한다.
    if lang:
        exts = [e for e, l in _ALIAS_EXT_TO_LANG.items() if l == lang]
        src_files: list[Path] = []
        for p in _iter_files(root_path):
            if p.suffix.lower() in exts:
                src_files.append(p)
        # 큰 파일 우선 (보통 더 많은 함수)
        src_files.sort(key=lambda p: -p.stat().st_size if p.exists() else 0)
        idx: list[tuple[str, list[str]]] = []
        for p in src_files[:8]:  # 최대 8 파일
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


def render_analysis_context(info: CodebaseInfo, template: str) -> str:
    """CodebaseInfo 를 LLM 이 읽을 한 장짜리 텍스트 보고서로 렌더."""
    standard_block = (
        "\n".join(f"- {p}" for p in info.standard_resources)
        if info.standard_resources else "(없음)"
    )
    custom_block = (
        "\n".join(f"- {p}: {preview}" for p, preview in info.custom_resources)
        if info.custom_resources else "(없음)"
    )
    if info.function_index:
        idx_lines: list[str] = []
        for path, sigs in info.function_index:
            idx_lines.append(f"- {path}")
            for sig in sigs:
                idx_lines.append(f"    · {sig}")
        function_index_block = "\n".join(idx_lines)
    else:
        function_index_block = "(없음)"

    return template.format(
        extensions=info.extensions or "(비어있음)",
        manifest_name=info.manifest_name or "(없음)",
        manifest_content=info.manifest_content or "(없음)",
        config_name=info.config_name or "(없음)",
        config_content=info.config_content or "(없음)",
        import_frequency=info.import_frequency or "(비어있음)",
        sample_file_count=info.sample_file_count,
        representative_file=info.representative_file or "(없음)",
        representative_skeleton=info.representative_skeleton or "(없음)",
        function_index=function_index_block,
        standard_resources=standard_block,
        custom_resources=custom_block,
    )


