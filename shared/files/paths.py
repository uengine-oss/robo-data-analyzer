"""경로 경계 — agent 도구가 허용된 root 밖을 못 보게 한다.

코드 탐색 도구(ls / glob_search / grep / read_file / read_file_range)는
분석 대상 코드(``data/source/<project>``) 안에서만, SKILL 도구(read_skill)는
분석기 자신의 명시된 framework/external skill root 안에서만 동작해야 한다.

이 모듈이 그 경계 검사·빌드폴더 skip 을 **단일 구현**으로 제공한다 —
filesystem_tools / code_inspection_tools 가 공유.
"""
from __future__ import annotations

from pathlib import Path


# 빌드/캐시/VCS 디렉토리 — grep / glob 순회에서 자동 skip.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "vendor", "bower_components",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env",
    "build", "dist", "out", "target", ".gradle", ".idea", ".vscode",
    ".next", ".nuxt", ".cache", ".tox",
})


class PathOutsideRoot(ValueError):
    """허용 root 밖을 가리키는 경로 — 도구가 ``OUTSIDE_ROOT`` 응답으로 변환."""


def is_skipped(path: Path) -> bool:
    """경로의 segment 중 하나라도 ``SKIP_DIRS`` 에 속하면 True."""
    return any(part in SKIP_DIRS for part in path.parts)


def normalize_glob_pattern(raw: str) -> str:
    """glob **패턴** 정규화 — leading slash / 드라이브 letter 제거.

    Python 3.12+ 의 ``Path.glob`` 은 절대경로 패턴을 ``NotImplementedError`` 로
    거부한다. 패턴은 항상 상대 형식이어야 하므로 강제 상대화한다.
    (``base`` 같은 root 인자는 대상 아님 — ``resolve_within`` 이 처리.)
    """
    p = (raw or "*").strip().replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":          # Windows drive letter (C: 등)
        p = p[2:]
    return p.lstrip("/")


def resolve_within(root: Path, raw: str) -> Path:
    """``raw`` 경로를 ``root`` 기준으로 resolve 하고 root 안인지 검증한다.

    - ``raw`` 가 상대경로면 ``root`` 기준, 절대경로면 그대로 해석.
    - ``..`` / 심볼릭 링크를 모두 풀어(``resolve``) 최종 경로를 판정.
    - 최종 경로가 ``root`` 자신이거나 그 하위면 통과, 아니면 거부.

    Args:
        root: 허용 경계 디렉토리.
        raw: 도구가 받은 경로 인자.

    Returns:
        ``root`` 안에 있는 절대 ``Path``.

    Raises:
        PathOutsideRoot: 최종 경로가 ``root`` 밖일 때.
    """
    root_resolved = Path(root).resolve()
    raw_path = Path((raw or ".").strip())
    candidate = raw_path if raw_path.is_absolute() else root_resolved / raw_path
    resolved = candidate.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PathOutsideRoot(str(raw))
    return resolved
