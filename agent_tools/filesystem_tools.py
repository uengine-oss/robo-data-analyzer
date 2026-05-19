"""filesystem 도구 — Profile Agent 가 코드베이스 탐색.

LangChain ``@tool`` 데코레이터 → Agent 자율 호출.
도구별 한도 (LLM 컨텍스트 폭증 방지 — 매 호출 input_tokens 6K 이하 목표):
- glob_search: 200 개
- grep: 80 줄
- read_file: 100 줄 (default — 파일 앞쪽 훑기용 ≈ 3K 토큰)
- read_file_range: 20 줄 (anchor 라인 위아래 정밀 정독용)

운영 보강 (원본 ``scanning_tools.py`` 흡수):
- ``_SKIP_DIRS`` 필터: ``node_modules`` / ``.git`` / ``__pycache__`` 등 무시.
- 절대경로 / 상대경로 모두 정상 처리 — root 인자는 ``Path`` 가 OS 자동 (Windows / POSIX),
  glob **패턴**만 ``_normalize_glob_pattern`` 으로 상대화 (Python 3.12+ ``Path.rglob`` 제약).
"""
from __future__ import annotations

import re
from pathlib import Path

from langchain.tools import tool


# 빌드/캐시/VCS 디렉토리 — grep / glob 순회에서 자동 skip.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "vendor", "bower_components",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env",
    "build", "dist", "out", "target", ".gradle", ".idea", ".vscode",
    ".next", ".nuxt", ".cache", ".tox",
})


def _normalize_glob_pattern(raw: str) -> str:
    """glob **패턴**(예: ``**/*.c``) 정규화 — leading slash / 드라이브 letter strip.

    ★ glob 패턴 한정. ``base`` / ``path`` 같은 root 인자는 절대경로 그대로 보존
    (``Path("C:/...").exists()`` / ``Path("/abs/...").iterdir()`` 모두 정상 동작).

    Python 3.12+ 의 ``Path.rglob`` 은 **glob 패턴**이 절대경로면 ``NotImplementedError``
    → 패턴만 상대 형식으로 강제. base path 와는 무관.
    """
    p = (raw or "*").strip().replace("\\", "/")
    # Windows drive letter (C:, D: 등) 제거 — glob 패턴에 박혀있으면 안 됨.
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    return p.lstrip("/")


def _resolve_root(raw: str) -> Path:
    """``ls`` / ``glob_search.base`` / ``read_file.path`` 등 **root 인자** 정규화.

    LLM 이 Windows / POSIX 절대경로 / 상대경로 어떤 형태 줘도 그대로 ``Path`` 로.
    ``Path`` 가 OS 표기 자동 처리 (Windows 에선 ``C:\\...`` / ``/...`` / 상대 모두 OK).
    """
    return Path((raw or ".").strip())


def _is_skipped(p: Path) -> bool:
    """경로의 어떤 segment 가 ``_SKIP_DIRS`` 에 속하면 skip."""
    return any(part in _SKIP_DIRS for part in p.parts)


@tool
def ls(path: str) -> str:
    """디렉토리 안 항목 목록 (DIR / FILE 라벨 포함)."""
    p = _resolve_root(path)
    if not p.exists():
        return f"NOT_FOUND: {path}"
    if not p.is_dir():
        return f"NOT_A_DIR: {path}"
    entries = []
    for item in sorted(p.iterdir()):
        if item.is_dir() and item.name in _SKIP_DIRS:
            continue
        kind = "DIR" if item.is_dir() else "FILE"
        entries.append(f"{kind:<5} {item.name}")
    return "\n".join(entries[:500])


@tool
def glob_search(pattern: str, base: str = ".") -> str:
    """glob 패턴으로 파일 경로 검색 (최대 200 개). 빌드/캐시 디렉토리는 skip."""
    base_path = _resolve_root(base)
    glob_pat = _normalize_glob_pattern(pattern)
    if not base_path.exists():
        return f"NOT_FOUND: {base}"
    results = []
    try:
        candidates = base_path.glob(glob_pat)
    except (NotImplementedError, ValueError) as e:
        return f"INVALID_GLOB: '{pattern}' ({e})"
    for p in sorted(candidates):
        if _is_skipped(p):
            continue
        results.append(str(p))
        if len(results) >= 200:
            break
    return "\n".join(results) if results else "NO_MATCHES"


@tool
def grep(pattern: str, path_glob: str = "**/*", base: str = ".") -> str:
    """정규식으로 파일 내용 검색 (최대 80 줄). 빌드/캐시 디렉토리는 skip.

    결과 형식: ``path:line:content``.
    """
    base_path = _resolve_root(base)
    glob_pat = _normalize_glob_pattern(path_glob or "**/*")
    if not base_path.exists():
        return f"NOT_FOUND: {base}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"INVALID_REGEX: {e}"

    try:
        candidates = list(base_path.glob(glob_pat))
    except (NotImplementedError, ValueError) as e:
        return f"INVALID_GLOB: '{path_glob}' ({e})"

    results: list[str] = []
    for p in sorted(candidates):
        if not p.is_file():
            continue
        if _is_skipped(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                results.append(f"{p}:{line_no}:{line.rstrip()}")
                if len(results) >= 80:
                    return "\n".join(results)
    return "\n".join(results) if results else "NO_MATCHES"


@tool
def read_file(path: str, max_lines: int = 100) -> str:
    """파일 앞쪽 N 줄 (default 100, 절대 한도 100). 절대경로 / 상대경로 모두 가능.

    100 줄 초과 본문은 ``read_file_range(path, start, end)`` 로 정밀 정독.
    """
    p = _resolve_root(path)
    if not p.is_file():
        return f"NOT_A_FILE: {path}"
    # LLM 이 max_lines=500 처럼 큰 값 주는 거 차단 (입력 토큰 폭증 방지).
    effective_max = min(max_lines, 100)
    lines: list[str] = []
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= effective_max:
                    break
                lines.append(line.rstrip())
    except OSError as e:
        return f"READ_ERROR: {e}"
    return "\n".join(lines)


@tool
def read_file_range(path: str, start: int, end: int) -> str:
    """파일의 ``start ~ end`` 라인 정밀 읽기 (20 줄 한도).

    Anchor 라인 (이미 grep / list_functions 등으로 찾은 위치) 의 위아래 컨텍스트
    정독용. 큰 본문은 ``read_file`` (100 줄 한도) 또는 grep 으로 우선 탐색.

    Args:
        path: 파일 경로 (절대 / 상대 모두 가능).
        start: 시작 라인 번호 (1-indexed, inclusive).
        end: 종료 라인 번호 (inclusive). ``end - start > 20`` 이면 자동 trim.
    """
    p = _resolve_root(path)
    if not p.is_file():
        return f"NOT_A_FILE: {path}"
    if end - start > 20:
        end = start + 20

    lines: list[str] = []
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, start=1):
                if i < start:
                    continue
                if i > end:
                    break
                lines.append(f"{i}: {line.rstrip()}")
    except OSError as e:
        return f"READ_ERROR: {e}"
    return "\n".join(lines)
