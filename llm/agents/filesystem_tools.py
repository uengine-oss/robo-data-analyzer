"""filesystem 도구 — Scan Agent 가 분석 대상 코드를 탐색.

``make_filesystem_tools(root)`` 팩토리가 5 도구(ls / glob_search / grep /
read_file / read_file_range)를 ``root`` 에 묶어서 만든다. 모든 경로 인자는
``path_boundary.resolve_within`` 으로 검사 — ``root`` 밖이면 ``OUTSIDE_ROOT`` 거부.
``root`` 는 분석 대상 ``data/source/<project>`` 디렉토리.

도구별 출력 한도 (LLM 컨텍스트 폭증 방지):
- glob_search: 200 개 / grep: 80 줄
- read_file: 50 줄 (앞쪽 훑기) / read_file_range: 11 줄 (anchor ± 5)
"""
from __future__ import annotations

import re
from pathlib import Path

from langchain.tools import tool

from llm.agents.path_boundary import (
    PathOutsideRoot,
    SKIP_DIRS,
    is_skipped,
    normalize_glob_pattern,
    resolve_within,
)

_READ_FILE_MAX_LINES = 50          # read_file 한 호출당 절대 한도
_READ_FILE_RANGE_RADIUS = 5        # read_file_range anchor 위/아래 라인 수


def make_filesystem_tools(root: Path) -> dict:
    """Scan Agent 코드 탐색 도구 5종을 ``root`` 에 묶어서 생성.

    반환은 ``{도구이름: 도구}`` dict — 호출자가 전부(``.values()``) 또는
    일부(``["read_file_range"]``)만 골라 쓸 수 있다.

    Args:
        root: 탐색 허용 경계 — 분석 대상 ``data/source/<project>`` 디렉토리.

    Returns:
        ``{"ls", "glob_search", "grep", "read_file", "read_file_range"}`` dict.
    """
    root_resolved = Path(root).resolve()

    @tool
    def ls(path: str) -> str:
        """디렉토리 안 항목 목록 — 폴더 의미로 가설 형성에 사용.

        **언제 사용**:
        - Scan Agent 첫 호출 권장. `mapper/` `dao/` `repository/` `entity/`
          `sql/` `include/` 같은 폴더명이 프레임워크 / DB 접근 패턴 가설로 직결.
        - 가설을 좁히려는 하위 디렉토리 탐색.

        **인자**:
        - `path`: user message 의 `project_root` 절대경로 또는 그 하위. 분석
          대상 폴더 밖 경로는 거부된다.

        **결과 형식**:
        - 한 줄당 `DIR  <name>` 또는 `FILE <name>`. 최대 500개.
          빌드/캐시 디렉토리 (`node_modules` / `.git` / `target` 등) 자동 skip.

        **실패 응답**:
        - `OUTSIDE_ROOT: <path>` — 분석 대상 폴더 밖. project_root 기준 경로로.
        - `NOT_FOUND: <path>` — 경로 존재 X.
        - `NOT_A_DIR: <path>` — 파일 경로를 줬을 때.
        """
        try:
            p = resolve_within(root_resolved, path)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {path}"
        if not p.exists():
            return f"NOT_FOUND: {path}"
        if not p.is_dir():
            return f"NOT_A_DIR: {path}"
        entries = []
        for item in sorted(p.iterdir()):
            if item.is_dir() and item.name in SKIP_DIRS:
                continue
            kind = "DIR" if item.is_dir() else "FILE"
            entries.append(f"{kind:<5} {item.name}")
        return "\n".join(entries[:500])

    @tool
    def glob_search(pattern: str, base: str = ".") -> str:
        """glob 패턴으로 파일 경로만 검색 (본문 X).

        **언제 사용**:
        - 확장자별 파일군 파악 (`**/*.java` / `**/*.xml` / `src/**/*.py`).
        - 특정 파일명 패턴 (`**/*Repository*.java` / `**/mapper/**/*.xml`).

        **인자**:
        - `pattern`: glob 패턴 (`**/*.ext` 형태). 절대경로 X — 패턴은 상대 형식.
        - `base`: 검색 시작 디렉토리. user message 의 `project_root` 절대경로 또는
          그 하위. default 는 project_root. 분석 대상 폴더 밖은 거부.

        **결과 형식**:
        - 한 줄당 절대경로 1개. 최대 200개. 빌드/캐시 디렉토리 자동 skip.

        **실패 응답**:
        - `OUTSIDE_ROOT: <base>` — 분석 대상 폴더 밖.
        - `NOT_FOUND: <base>` — base 경로 존재 X.
        - `INVALID_GLOB: '<pattern>' (...)` — 패턴 문법 오류.
        - `NO_MATCHES` — 매칭 파일 0개.
        """
        try:
            base_path = resolve_within(root_resolved, base)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {base}"
        if not base_path.exists():
            return f"NOT_FOUND: {base}"
        glob_pat = normalize_glob_pattern(pattern)
        try:
            candidates = base_path.glob(glob_pat)
        except (NotImplementedError, ValueError) as e:
            return f"INVALID_GLOB: '{pattern}' ({e})"
        results = []
        for p in sorted(candidates):
            if is_skipped(p):
                continue
            results.append(str(p))
            if len(results) >= 200:
                break
        return "\n".join(results) if results else "NO_MATCHES"

    @tool
    def grep(pattern: str, path_glob: str = "**/*", base: str = ".") -> str:
        """정규식으로 파일 내용 검색 — 매칭 라인 출력.

        **언제 사용**:
        - 의심 매크로 / 이름 hit 수 확인 (≥ 3 hits 면 진짜 / 0 이면 이름 틀림).
        - DB 접근 wrapper / 어노테이션 단서 (`@Entity`, `findBy`, `mpfm` 등).

        **인자**:
        - `pattern`: 정규식 (Python `re`). 단독 식별자는 청크 폭발 → 패턴 + 식별자 결합 권장.
        - `path_glob`: 검색 대상 파일 패턴 (`**/*.java`). default `**/*`.
        - `base`: 검색 시작 디렉토리. user message 의 `project_root` 또는 그 하위.

        **결과 형식**:
        - 한 줄당 `path:line:content`. 최대 80줄. 빌드/캐시 디렉토리 자동 skip.

        **실패 응답**:
        - `OUTSIDE_ROOT: <base>` — 분석 대상 폴더 밖.
        - `NOT_FOUND: <base>` — base 경로 존재 X.
        - `INVALID_REGEX: ...` / `INVALID_GLOB: ...` — 문법 오류.
        - `NO_MATCHES` — 매칭 0건.
        """
        try:
            base_path = resolve_within(root_resolved, base)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {base}"
        if not base_path.exists():
            return f"NOT_FOUND: {base}"
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
            if not p.is_file() or is_skipped(p):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                read_failures.append(f"{p}:{type(error).__name__}")
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{p}:{line_no}:{line.rstrip()}")
                    if len(results) >= 80:
                        return "\n".join(results)
        output_lines = results or ["NO_MATCHES"]
        if read_failures:
            output_lines.append(
                f"READ_SKIPPED: count={len(read_failures)} first={read_failures[0]}"
            )
        return "\n".join(output_lines)

    @tool
    def read_file(path: str, max_lines: int = 50) -> str:
        """파일 **앞쪽 N 줄** 읽기 (default 50, 절대 한도 50).

        **언제 사용**:
        - 비함수 파일 (imports / `#include` / 매니페스트 / 설정) 앞부분 훑기.
        - `pom.xml` / `package.json` / `*.h` / `application.yml` 등.

        **본문은 X** — 큰 코드노드는 잘림. 본문 정독은 `list_code_nodes` 로
        위치 확인 후 `read_file_range(path, anchor)`.

        **인자**:
        - `path`: 파일 경로. 분석 대상 폴더 안이어야 한다.
        - `max_lines`: 읽을 줄 수 (default 50, 절대 한도 50 — 큰 값도 50 으로 강제).

        **결과 형식**: 줄바꿈으로 구분된 본문 (라인 번호 X).

        **실패 응답**:
        - `OUTSIDE_ROOT: <path>` — 분석 대상 폴더 밖.
        - `NOT_A_FILE: <path>` — 파일 아님 (디렉토리 / 존재 X).
        - `READ_ERROR: ...` — I/O 오류.
        """
        try:
            p = resolve_within(root_resolved, path)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {path}"
        if not p.is_file():
            return f"NOT_A_FILE: {path}"
        effective_max = min(max_lines, _READ_FILE_MAX_LINES)
        lines: list[str] = []
        try:
            with p.open("r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= effective_max:
                        break
                    lines.append(line.rstrip())
        except (OSError, UnicodeDecodeError) as e:
            return f"READ_ERROR: {e}"
        return "\n".join(lines)

    @tool
    def read_file_range(path: str, anchor: int) -> str:
        """파일의 `anchor` 라인 **위 5줄 + anchor + 아래 5줄 = 총 11줄** 읽기.

        `anchor` 결정:
        - `list_code_nodes(file)` 결과 코드노드의 `start_line` 들.
        - `grep` 결과의 라인 번호.

        **★ 같은 anchor 연속 호출 X** — 큰 코드노드는 시작 / 중간 / 끝 여러 anchor 로.

        Args:
            path: 파일 경로. 분석 대상 폴더 안이어야 한다.
            anchor: 정독할 중심 라인 번호 (1-indexed).

        Returns:
            ``<line_no>: <content>`` 형식. evidence `file:line` 인용 그대로 가능.
            실패 시 ``OUTSIDE_ROOT`` / ``NOT_A_FILE`` / ``READ_ERROR``.
        """
        try:
            p = resolve_within(root_resolved, path)
        except PathOutsideRoot:
            return f"OUTSIDE_ROOT: {path}"
        if not p.is_file():
            return f"NOT_A_FILE: {path}"
        start = max(anchor - _READ_FILE_RANGE_RADIUS, 1)
        end = anchor + _READ_FILE_RANGE_RADIUS
        lines: list[str] = []
        try:
            with p.open("r", encoding="utf-8") as f:
                for i, line in enumerate(f, start=1):
                    if i < start:
                        continue
                    if i > end:
                        break
                    lines.append(f"{i}: {line.rstrip()}")
        except (OSError, UnicodeDecodeError) as e:
            return f"READ_ERROR: {e}"
        return "\n".join(lines)

    return {
        "ls": ls,
        "glob_search": glob_search,
        "grep": grep,
        "read_file": read_file,
        "read_file_range": read_file_range,
    }
