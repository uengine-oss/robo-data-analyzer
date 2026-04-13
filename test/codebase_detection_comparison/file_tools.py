"""파일시스템 탐색 유틸리티.

Python 표준 라이브러리만 사용. 5가지 연산:
- count_extensions: 확장자별 파일 수
- find_marker_files: 빌드 마커 파일 찾기
- read_file: UTF-8/CP949 fallback 으로 파일 읽기
- extract_imports: 언어별 정규식으로 import/include 추출 + 빈도 집계
- grep_pattern: 정규식으로 파일들에서 매치 행 찾기
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterable


DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "venv",
        ".venv",
        "env",
        ".env.dir",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        ".turbo",
        "coverage",
        ".tox",
        ".gradle",
        ".idea",
        ".vscode",
        "logs",
    }
)


def _iter_files(root: Path, skip_dirs: frozenset) -> Iterable[Path]:
    """os.walk 래퍼. skip_dirs 에 포함된 디렉토리는 진입하지 않음."""

    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            yield Path(dirpath) / fn


def count_extensions(
    root: str | Path,
    skip_dirs: frozenset = DEFAULT_SKIP_DIRS,
) -> dict[str, int]:
    """루트 아래 모든 파일의 확장자별 개수."""

    counter: Counter[str] = Counter()
    for path in _iter_files(Path(root), skip_dirs):
        ext = path.suffix.lower()
        if ext:
            counter[ext] += 1
    return dict(counter.most_common())


def find_marker_files(
    root: str | Path,
    marker_names: Iterable[str],
    max_depth: int = 3,
    skip_dirs: frozenset = DEFAULT_SKIP_DIRS,
) -> list[str]:
    """빌드 마커 파일 상대경로 리스트.

    루트 기준 max_depth 이내만 탐색 (모노레포 대응). 대소문자 구분 없음.
    """

    root_path = Path(root).resolve()
    marker_set = {m.lower() for m in marker_names}
    results: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        rel = Path(dirpath).relative_to(root_path)
        depth = 0 if rel == Path(".") else len(rel.parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.lower() in marker_set:
                results.append(str((Path(dirpath) / fn).relative_to(root_path)).replace("\\", "/"))
    return results


def read_file(path: str | Path, limit: int | None = None) -> str:
    """UTF-8 우선, 실패 시 CP949 (한국 레거시) → Latin-1 순으로 fallback.

    limit: 앞 N줄만 반환 (None 이면 전체).
    """

    path = Path(path)
    for encoding in ("utf-8", "cp949", "latin-1"):
        try:
            with path.open("r", encoding=encoding) as f:
                if limit is None:
                    return f.read()
                lines = []
                for i, line in enumerate(f):
                    if i >= limit:
                        break
                    lines.append(line)
                return "".join(lines)
        except UnicodeDecodeError:
            continue
    raise IOError(f"Failed to decode {path} with any supported encoding")


def extract_imports(
    root: str | Path,
    extensions: Iterable[str],
    import_regex: str | None = None,
    max_files_per_ext: int = 500,
    skip_dirs: frozenset = DEFAULT_SKIP_DIRS,
) -> dict[str, int]:
    """확장자별로 import/include 행 추출 → 빈도 집계.

    import_regex 를 주면 언어별 단일 정규식 사용.
    None 이면 확장자별 기본 정규식 자동 선택 (C/Python/Java/Go/TS/JS 지원).

    반환 키: import/include 의 raw 문자열 (prefix 추출은 호출측에서 수행).
    """

    root_path = Path(root).resolve()
    ext_set = {e.lower() for e in extensions}
    counter: Counter[str] = Counter()

    # 확장자별 기본 정규식 (import_regex 미지정 시).
    default_regexes = {
        ".c": re.compile(r'^\s*#include\s*[<"]([^>"]+)[>"]'),
        ".h": re.compile(r'^\s*#include\s*[<"]([^>"]+)[>"]'),
        ".ec": re.compile(r'^\s*#include\s*[<"]([^>"]+)[>"]'),
        ".pc": re.compile(r'^\s*#include\s*[<"]([^>"]+)[>"]'),
        ".py": re.compile(r'^\s*(?:from\s+([\w.]+)|import\s+([\w.]+))'),
        ".java": re.compile(r'^\s*import\s+([\w.]+);?'),
        ".kt": re.compile(r'^\s*import\s+([\w.]+)'),
        ".go": re.compile(r'^\s*"([^"]+)"'),
        ".ts": re.compile(r'(?:from\s+[\'"]([^\'"]+)[\'"]|require\s*\(\s*[\'"]([^\'"]+)[\'"])'),
        ".tsx": re.compile(r'(?:from\s+[\'"]([^\'"]+)[\'"]|require\s*\(\s*[\'"]([^\'"]+)[\'"])'),
        ".js": re.compile(r'(?:from\s+[\'"]([^\'"]+)[\'"]|require\s*\(\s*[\'"]([^\'"]+)[\'"])'),
        ".jsx": re.compile(r'(?:from\s+[\'"]([^\'"]+)[\'"]|require\s*\(\s*[\'"]([^\'"]+)[\'"])'),
        ".rs": re.compile(r'^\s*use\s+([\w:]+)'),
    }

    user_regex = re.compile(import_regex) if import_regex else None
    files_seen_per_ext: Counter[str] = Counter()

    for path in _iter_files(root_path, skip_dirs):
        ext = path.suffix.lower()
        if ext not in ext_set:
            continue
        if files_seen_per_ext[ext] >= max_files_per_ext:
            continue
        files_seen_per_ext[ext] += 1

        regex = user_regex or default_regexes.get(ext)
        if regex is None:
            continue

        try:
            content = read_file(path)
        except (IOError, OSError):
            continue

        for line in content.splitlines():
            m = regex.search(line)
            if not m:
                continue
            # 여러 그룹 중 매치된 첫 번째 것을 키로 사용.
            captured = next((g for g in m.groups() if g), None)
            if captured:
                counter[captured] += 1

    return dict(counter.most_common())


def grep_pattern(
    root: str | Path,
    pattern: str,
    extensions: Iterable[str],
    max_hits: int = 20,
    skip_dirs: frozenset = DEFAULT_SKIP_DIRS,
) -> list[dict]:
    """정규식 패턴을 소스에서 검색. 각 히트는 {file, line, text} 딕셔너리.

    max_hits 도달 시 조기 종료.
    """

    root_path = Path(root).resolve()
    ext_set = {e.lower() for e in extensions}
    try:
        regex = re.compile(pattern)
    except re.error as err:
        return [{"error": f"invalid regex: {err}"}]

    hits: list[dict] = []
    for path in _iter_files(root_path, skip_dirs):
        if len(hits) >= max_hits:
            break
        if path.suffix.lower() not in ext_set:
            continue
        try:
            content = read_file(path)
        except (IOError, OSError):
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                rel = str(path.relative_to(root_path)).replace("\\", "/")
                hits.append({"file": rel, "line": lineno, "text": line.strip()[:200]})
                if len(hits) >= max_hits:
                    break
    return hits
