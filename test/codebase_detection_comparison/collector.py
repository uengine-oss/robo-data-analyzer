"""결정론 5단계 수집 — 1초 미만 목표.

확장자 빈도 / 매니페스트 / 설정파일 / import 샘플 / 대표파일 80줄 을
한 번에 모아 CodebaseInfo dataclass 로 반환. LLM 호출 없음.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .tools.tree_sitter_tools import (
    SKIP_DIRS,
    _ALIAS_EXT_TO_LANG,
    extract_imports_sample,
    extract_skeleton,
)

WELL_KNOWN_MANIFESTS = [
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", "requirements.txt", "pyproject.toml",
    "Pipfile", "Gemfile", "composer.json", "Makefile", "CMakeLists.txt",
    "mix.exs", "rebar.config", "pubspec.yaml",
]

CONFIG_PATTERNS = [
    "application.yml", "application.yaml", "application.properties",
    ".env", "docker-compose.yml", "docker-compose.yaml",
    "settings.py", "config.py", "schema.prisma",
    "tsconfig.json", "nest-cli.json",
]


@dataclass
class CodebaseInfo:
    root: str
    extensions: dict[str, int] = field(default_factory=dict)
    manifest_name: str = ""
    manifest_content: str = ""
    config_name: str = ""
    config_content: str = ""
    import_frequency: dict[str, int] = field(default_factory=dict)
    representative_file: str = ""
    representative_skeleton: str = ""
    sample_file_count: int = 0
    primary_language: Optional[str] = None


def _count_extensions(root: Path) -> dict[str, int]:
    counter: Counter = Counter()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        suf = p.suffix.lower()
        if suf:
            counter[suf] += 1
    return dict(counter.most_common(20))


def _find_first(root: Path, names: list[str]) -> Optional[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
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
    if not language:
        return "", ""
    exts = [e for e, l in _ALIAS_EXT_TO_LANG.items() if l == language]
    candidates: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in exts:
            candidates.append(p)
    if not candidates:
        return "", ""

    chosen = candidates[0]
    if imports:
        top_imports = list(imports.keys())[:5]
        for cand in candidates:
            try:
                head = cand.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            if any(tok.split()[-1].strip("<>\";") in head for tok in top_imports):
                chosen = cand
                break

    skeleton = extract_skeleton(chosen, max_items=80)
    return str(chosen), skeleton


def collect(root: str, sample_size: int = 30) -> CodebaseInfo:
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

    return info


def render_analysis_context(info: CodebaseInfo, template: str) -> str:
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
    )
