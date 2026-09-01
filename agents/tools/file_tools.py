"""filesystem 도구 — Scan Agent 가 분석 대상 코드를 탐색.

``make_filesystem_tools(root)`` 팩토리가 5 도구(ls / glob_search / grep /
read_file / read_file_range)를 ``root`` 에 묶어서 만든다. 모든 경로 인자는
``path_boundary.resolve_within`` 으로 검사 — ``root`` 밖이면 ``OUTSIDE_ROOT`` 거부.
``root`` 는 분석 대상 ``data/source/<project>`` 디렉토리.

큰 결과는 숨겨서 자르지 않고 결정론적 페이지로 반환한다. 각 결과의
``next_offset``/``next_start_line``이 null이 될 때까지 이어 읽을 수 있다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from langchain.tools import tool

from shared.files.safe_paths import (
    PathOutsideRoot,
    SKIP_DIRS,
    is_skipped,
    normalize_glob_pattern,
    resolve_within,
)
from shared.files.run_cache_context import read_run_text

_LIST_PAGE_SIZE = 500
_GLOB_PAGE_SIZE = 200
_GREP_PAGE_SIZE = 80
_READ_PAGE_SIZE = 100


def _page_header(*, offset: int, returned: int, total: int) -> str:
    next_offset = offset + returned if offset + returned < total else None
    return (
        f"PAGE total={total} "
        f"next_offset={next_offset if next_offset is not None else 'null'}"
    )


def make_filesystem_tools(
    root: Path,
    *,
    allowed_files: Iterable[Path] | None = None,
) -> dict:
    """Scan Agent 코드 탐색 도구 5종을 ``root`` 에 묶어서 생성.

    반환은 ``{도구이름: 도구}`` dict — 호출자가 전부(``.values()``) 또는
    일부(``["read_file_range"]``)만 골라 쓸 수 있다.

    Args:
        root: 탐색 허용 경계 — 분석 대상 ``data/source/<project>`` 디렉토리.

    Returns:
        ``{"ls", "glob_search", "grep", "read_file", "read_file_range"}`` dict.
    """
    root_resolved = Path(root).resolve()
    allowed_resolved = (
        None
        if allowed_files is None
        else frozenset(Path(path).resolve() for path in allowed_files)
    )
    if allowed_resolved is not None:
        for path in allowed_resolved:
            try:
                path.relative_to(root_resolved)
            except ValueError as error:
                raise ValueError(
                    f"allowed scan source is outside project root: {path}"
                ) from error
            if not path.is_file():
                raise ValueError(f"allowed scan source is not a file: {path}")

    def is_allowed_file(path: Path) -> bool:
        return allowed_resolved is None or path.resolve() in allowed_resolved

    def is_visible(path: Path) -> bool:
        resolved = path.resolve()
        if allowed_resolved is None or resolved == root_resolved:
            return True
        if resolved in allowed_resolved:
            return True
        return any(resolved in allowed.parents for allowed in allowed_resolved)

    @tool
    def ls(path: str, offset: int = 0) -> str:
        """프로젝트 상대 디렉터리의 visible 항목을 `DIR|FILE` 페이지로 나열한다.

        `path="."`가 root다. root 밖, 선택 source 밖, 빌드·캐시 디렉터리는
        노출하지 않는다. `next_offset`이 null이 될 때까지 이어 읽는다.
        """
        try:
            p = resolve_within(root_resolved, path)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {path}"
        if not p.exists():
            return f"NOT_FOUND: {path}"
        if not p.is_dir():
            return f"NOT_A_DIR: {path}"
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        entries = []
        for item in sorted(p.iterdir()):
            if item.is_dir() and item.name in SKIP_DIRS:
                continue
            if not is_visible(item):
                continue
            kind = "DIR" if item.is_dir() else "FILE"
            entries.append(f"{kind:<5} {item.name}")
        page = entries[offset:offset + _LIST_PAGE_SIZE]
        return "\n".join([
            _page_header(offset=offset, returned=len(page), total=len(entries)),
            *page,
        ])

    @tool
    def glob_search(pattern: str, base: str = ".", offset: int = 0) -> str:
        """프로젝트 상대 `base` 아래 visible 파일 경로를 glob으로 찾는다.

        본문은 반환하지 않는다. root 밖·선택 source 밖·빌드/캐시는 제외하며,
        `next_offset`이 null이 될 때까지 이어 읽는다.
        """
        try:
            base_path = resolve_within(root_resolved, base)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {base}"
        if not base_path.exists():
            return f"NOT_FOUND: {base}"
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        glob_pat = normalize_glob_pattern(pattern)
        try:
            candidates = base_path.glob(glob_pat)
        except (NotImplementedError, ValueError) as e:
            return f"INVALID_GLOB: '{pattern}' ({e})"
        results = []
        for p in sorted(candidates):
            if is_skipped(p) or not p.is_file() or not is_allowed_file(p):
                continue
            results.append(p.relative_to(root_resolved).as_posix())
        if not results:
            return "NO_MATCHES"
        page = results[offset:offset + _GLOB_PAGE_SIZE]
        return "\n".join([
            _page_header(offset=offset, returned=len(page), total=len(results)),
            *page,
        ])

    @tool
    def grep(
        pattern: str,
        path_glob: str = "**/*",
        base: str = ".",
        offset: int = 0,
    ) -> str:
        """visible 파일을 Python 정규식으로 검색해 `path:line:content`를 반환한다.

        경로는 프로젝트 상대값이며 매칭 줄 원문을 보존한다. root/선택-source
        경계를 강제하고 읽기 실패를 숨기지 않는다. `next_offset`이 null까지 이어 읽는다.
        """
        try:
            base_path = resolve_within(root_resolved, base)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {base}"
        if not base_path.exists():
            return f"NOT_FOUND: {base}"
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"INVALID_REGEX: {e}"
        glob_pat = normalize_glob_pattern(path_glob or "**/*")
        try:
            candidates = list(base_path.glob(glob_pat))
        except (NotImplementedError, ValueError) as e:
            return f"INVALID_GLOB: '{path_glob}' ({e})"

        results: list[str] = []
        read_failures: list[str] = []
        for p in sorted(candidates):
            if not p.is_file() or is_skipped(p) or not is_allowed_file(p):
                continue
            try:
                text = read_run_text(p, encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                relative_path = p.relative_to(root_resolved).as_posix()
                read_failures.append(f"{relative_path}:{type(error).__name__}")
                continue
            relative_path = p.relative_to(root_resolved).as_posix()
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{relative_path}:{line_no}:{line.rstrip()}")
        if not results:
            output_lines = ["NO_MATCHES"]
        else:
            page = results[offset:offset + _GREP_PAGE_SIZE]
            output_lines = [
                _page_header(offset=offset, returned=len(page), total=len(results)),
                *page,
            ]
        if read_failures:
            output_lines.append(
                f"READ_SKIPPED: count={len(read_failures)} first={read_failures[0]}"
            )
        return "\n".join(output_lines)

    @tool
    def read_file(path: str, start_line: int = 1) -> str:
        """visible 파일을 `start_line`부터 최대 100줄의 번호 붙은 원문으로 읽는다.

        root와 선택-source 경계를 강제한다. `total_lines`와 `next_start_line`으로
        전문 접근 종료를 확인한다. 정확한 routine 범위는 `read_file_range`를 사용한다.
        """
        try:
            p = resolve_within(root_resolved, path)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {path}"
        if not p.is_file():
            return f"NOT_A_FILE: {path}"
        if not is_allowed_file(p):
            return f"NOT_SELECTED: {path}"
        if start_line < 1:
            return "INVALID_START_LINE: start_line must be positive"
        lines: list[str] = []
        total_lines = 0
        try:
            for line_no, line in enumerate(
                read_run_text(p, encoding="utf-8").splitlines(), start=1,
            ):
                total_lines = line_no
                if line_no < start_line:
                    continue
                if len(lines) < _READ_PAGE_SIZE:
                    lines.append(f"{line_no}: {line}")
        except (OSError, UnicodeDecodeError) as e:
            return f"READ_ERROR: {e}"
        next_start = start_line + len(lines) if start_line + len(lines) <= total_lines else None
        return "\n".join([
            f"PAGE total_lines={total_lines} "
            f"next_start_line={next_start if next_start is not None else 'null'}",
            *lines,
        ])

    @tool
    def read_file_range(path: str, start_line: int, end_line: int) -> str:
        """visible 파일의 inclusive 줄 범위를 번호 붙은 원문으로 읽는다.

        root와 선택-source 경계를 강제한다. 한 페이지는 최대 100줄이며
        `next_start_line`이 null이 될 때까지 요청 범위를 이어 읽는다.
        """
        try:
            p = resolve_within(root_resolved, path)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {path}"
        if not p.is_file():
            return f"NOT_A_FILE: {path}"
        if not is_allowed_file(p):
            return f"NOT_SELECTED: {path}"
        if start_line < 1 or end_line < start_line:
            return "INVALID_RANGE: require 1 <= start_line <= end_line"
        page_end = min(end_line, start_line + _READ_PAGE_SIZE - 1)
        lines: list[str] = []
        try:
            for i, line in enumerate(
                read_run_text(p, encoding="utf-8").splitlines(), start=1,
            ):
                if i < start_line:
                    continue
                if i > page_end:
                    break
                lines.append(f"{i}: {line}")
        except (OSError, UnicodeDecodeError) as e:
            return f"READ_ERROR: {e}"
        last_line = start_line + len(lines) - 1
        next_start = last_line + 1 if lines and last_line < end_line else None
        return "\n".join([
            f"PAGE next_start_line={next_start if next_start is not None else 'null'}",
            *lines,
        ])

    return {
        "ls": ls,
        "glob_search": glob_search,
        "grep": grep,
        "read_file": read_file,
        "read_file_range": read_file_range,
    }
