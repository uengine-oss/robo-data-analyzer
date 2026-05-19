"""``bundle_search_lines`` — Linking Agent 코드 검색 도구.

**라인 1개만 반환** (본문 X / 주변 줄 X) — $400 사고 원인 차단.
``bundle_get_chunk`` 같은 본문 통째 반환 도구는 폐기.

운영 보강: 빌드/캐시 디렉토리 (``node_modules`` / ``.git`` / ``__pycache__`` 등) 자동 skip.
"""
from __future__ import annotations

import re
from pathlib import Path

from langchain.tools import tool


# filesystem_tools 와 동일한 skip set — 두 도구가 같은 정책.
# 단일 source 로 두려면 별도 모듈로 추출 가능하나, 두 곳뿐이라 일단 inline.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "vendor", "bower_components",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env",
    "build", "dist", "out", "target", ".gradle", ".idea", ".vscode",
    ".next", ".nuxt", ".cache", ".tox",
})


def _is_skipped(p: Path) -> bool:
    return any(part in _SKIP_DIRS for part in p.parts)


@tool
def bundle_search_lines(query: str, base: str = ".") -> str:
    """코드 패턴 검색 — 매칭 라인 1개씩 (본문 X, 최대 30 개).

    형식: ``path:line:content`` (한 라인만).
    빌드/캐시 디렉토리는 자동 skip.

    Args:
        query: 검색어 (substring 매칭, 대소문자 무시 — regex 도 시도).
        base: 검색 시작 디렉토리.
    """
    base_path = Path(base)
    needle_lower = query.lower()
    # regex 도 지원 — 단순 substring + regex 둘 다 시도
    try:
        regex = re.compile(query, re.IGNORECASE)
        use_regex = True
    except re.error:
        use_regex = False

    results: list[str] = []
    for p in sorted(base_path.rglob("*")):
        if not p.is_file():
            continue
        if _is_skipped(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            hit = (
                regex.search(line) if use_regex
                else needle_lower in line.lower()
            )
            if hit:
                results.append(f"{p}:{line_no}:{line.rstrip()}")
                if len(results) >= 30:
                    return "\n".join(results)
    return "\n".join(results) if results else "NO_MATCHES"
