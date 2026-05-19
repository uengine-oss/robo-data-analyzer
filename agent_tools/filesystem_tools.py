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
    """디렉토리 안 항목 목록 — 폴더 의미로 가설 형성에 사용.

    **언제 사용**:
    - Profile Agent: 첫 호출 권장. `mapper/` `dao/` `repository/` `entity/` `sql/`
      `include/` 같은 폴더명이 프레임워크 / DB 접근 패턴 가설로 직결.
    - 가설을 좁히려는 하위 디렉토리 탐색 (`<project_root>/src/main/java` 등).

    **인자**:
    - `path`: user message 의 `project_root` 절대경로 그대로. 또는 그 하위 경로.
      Windows / POSIX 절대경로 모두 OK. 시스템 표준 경로 (`/`, `/home`, `/app`) 추측 금지.

    **결과 형식**:
    - 한 줄당 `DIR  <name>` 또는 `FILE <name>`.
    - 최대 500개. 빌드/캐시 디렉토리 (`node_modules` / `.git` / `__pycache__` /
      `.venv` / `target` / `dist` 등) 는 자동 skip.

    **실패 응답**:
    - `NOT_FOUND: <path>` — 경로 존재 X (대개 user message 의 project_root 무시하고 추측).
    - `NOT_A_DIR: <path>` — 파일 경로 줬을 때.
    """
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
    """glob 패턴으로 파일 경로만 검색 (본문 X).

    **언제 사용**:
    - 확장자별 파일군 파악 (`**/*.java` / `**/*.xml` / `**/*.sql` / `src/**/*.py`).
    - 특정 파일명 패턴 찾기 (`**/*Repository*.java` / `**/Dao*.kt`).
    - mapper / config / yaml 파일 위치 확인 (`**/mapper/**/*.xml` / `**/*application*.yml`).

    **인자**:
    - `pattern`: glob 패턴 (`**/*.ext` 형태). 절대경로 X — 패턴은 상대 형식. Drive letter
      (`C:`) 자동 제거. 빈 패턴이면 `*` 로 fallback.
    - `base`: 검색 시작 디렉토리. user message 의 `project_root` 절대경로 그대로.

    **결과 형식**:
    - 한 줄당 절대경로 1개. 최대 200개.
    - 빌드/캐시 디렉토리 자동 skip.

    **실패 응답**:
    - `NOT_FOUND: <base>` — base 경로 존재 X.
    - `INVALID_GLOB: '<pattern>' (...)` — 패턴 문법 오류.
    - `NO_MATCHES` — 매칭 파일 0개. 다른 확장자 / 변형 패턴 시도.
    """
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
    """정규식으로 파일 내용 검색 — 매칭 라인 출력.

    **언제 사용**:
    - 의심 매크로 / 함수명 hit 수 확인 (≥ 3 hits 면 진짜 / 0 이면 이름 틀림).
    - DB 접근 wrapper 확인 (`mpfm`, `delegator\\.`, `@Query`, `findBy`, `EntityManager` 등).
    - 어노테이션 / 매핑 단서 (`@Entity`, `@Table\\(`, `<resultMap`, `@Mapper`).
    - 식별자 변형 탐색 — 대소문자 / 부분 / 약어 (`Repository` → `Repo`).

    **인자**:
    - `pattern`: 정규식 (Python `re`). 단독 식별자 검색은 청크 폭발 가능 → 패턴 + 식별자 결합 권장.
    - `path_glob`: 검색 대상 파일 패턴 (`**/*.java`). default `**/*` — 전체 파일.
    - `base`: 검색 시작 디렉토리. user message 의 `project_root` 절대경로 그대로.

    **결과 형식**:
    - 한 줄당 `path:line:content`. 최대 80줄.
    - 빌드/캐시 디렉토리 자동 skip.

    **실패 응답**:
    - `NOT_FOUND: <base>` — base 경로 존재 X.
    - `INVALID_REGEX: ...` — 정규식 문법 오류.
    - `INVALID_GLOB: '<pattern>' (...)` — path_glob 문법 오류.
    - `NO_MATCHES` — 매칭 0건. 변형 검색 ≥ 3 회 후에만 0 결론.

    **결과 활용**:
    - hits 1~2 → 정확한 위치 확인 후 `read_file_range` 로 본문 확인.
    - hits 80+ → 패턴이 너무 넓음. 더 좁은 정규식 / path_glob 으로 재검색.
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


_READ_FILE_MAX_LINES = 50              # 한 호출당 절대 한도 (LLM 입력 토큰 폭증 방지)


@tool
def read_file(path: str, max_lines: int = 50) -> str:
    """파일 **앞쪽 N 줄** 읽기 (default 50, 절대 한도 50).

    **언제 사용**:
    - 비함수 파일 (imports / `#include` / 매니페스트 / 설정) 앞부분 훑기.
    - `pom.xml` / `package.json` / `build.gradle` / `Cargo.toml` 같은 manifest.
    - 헤더 파일 (`*.h`) 의 함수 선언 / 매크로 정의.
    - 설정 파일 (`application.yml` / `*.properties`) 의 키워드 단서.

    **함수 본문은 X** — 큰 함수는 잘림. 함수 본문 정독은 `list_functions` 로 위치 확인 후
    `read_file_range(path, anchor)` 사용 (anchor ± 5줄).

    **인자**:
    - `path`: 파일 경로 (절대 / 상대 모두 가능).
    - `max_lines`: 읽을 줄 수 (default 50, 절대 한도 50 — 큰 값 줘도 50 으로 강제 trim).

    **결과 형식**: 줄바꿈으로 구분된 본문 (라인 번호 X).

    **실패 응답**:
    - `NOT_A_FILE: <path>` — 경로가 파일 아님 (디렉토리 / 존재 X).
    - `READ_ERROR: ...` — I/O 오류.
    """
    p = _resolve_root(path)
    if not p.is_file():
        return f"NOT_A_FILE: {path}"
    # LLM 이 max_lines=500 처럼 큰 값 주는 거 차단 (입력 토큰 폭증 방지).
    effective_max = min(max_lines, _READ_FILE_MAX_LINES)
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


_READ_FILE_RANGE_RADIUS = 5            # anchor 위/아래 라인 수


@tool
def read_file_range(path: str, anchor: int) -> str:
    """파일의 `anchor` 라인 **위 5줄 + anchor + 아래 5줄 = 총 11줄** 읽기.

    `anchor` 결정:
    - `list_functions(file)` 결과의 함수 `start_line` 들을 anchor 로 사용 (각 함수 정독).
    - `grep` / `bundle_search_lines` 결과의 라인 번호도 anchor.
    - 파일 시작 (anchor=1) 은 manifest / 헤더 / import 확인 시 1 번만.

    **★ 같은 anchor 연속 호출 X** — 같은 라인 두 번 봐도 정보 안 변함. 큰 함수는 시작 / 중간 / 끝 등 **여러 anchor** 로.

    Args:
        path: 파일 경로 (절대 / 상대 모두 가능).
        anchor: 정독할 중심 라인 번호 (1-indexed).

    Returns:
        ``<line_no>: <content>`` 형식. evidence `file:line` 인용 그대로 가능.
        실패 시 ``NOT_A_FILE: <path>`` / ``READ_ERROR: ...``.
    """
    p = _resolve_root(path)
    if not p.is_file():
        return f"NOT_A_FILE: {path}"

    start = max(anchor - _READ_FILE_RANGE_RADIUS, 1)
    end = anchor + _READ_FILE_RANGE_RADIUS

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
