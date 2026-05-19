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
    """코드 패턴 검색 — **매칭 라인 1개씩만** (본문 X / 주변 줄 X / 최대 30개).

    **언제 사용** (Linking Agent 전용):
    - unresolved case 의 단서 추적 — 변수 alias / wrapper / 어노테이션 / SQL 리터럴.
    - 한 case = 한 검색 호출 / 변형 ≤ 3 회. 같은 쿼리 반복 X.

    **융합 쿼리 규칙** (단독 식별자 검색 금지 — 청크 폭발):

    | 패턴 출처 | 융합 예시 |
    |---|---|
    | wrapper / 매크로 | `<wrapper> <id>` / `<id>_<suffix>` |
    | raw SQL | `FROM <table>` / `INTO <table>` / `UPDATE <table>` |
    | ORM | `<repoVar>.<method>` / `em.persist <Entity>` |
    | mapper / annotation | `@Query ... <table>` / `<namespace>.<id>` |

    금지:
    - 식별자 단독 (`"USERS"`) — 청크 폭발 → 노이즈.
    - 패턴 단독 (`"@Query"`) — 식별자 없으면 의미 X.

    **인자**:
    - `query`: 검색어. substring (대소문자 무시) + 정규식 둘 다 시도 — 정규식 문법 오류면 substring 만.
    - `base`: 검색 시작 디렉토리. user message 의 `project_root` 절대경로 그대로.

    **결과 형식**:
    - 한 줄당 `path:line:content`. 최대 30개. 본문 / 주변 줄 X.
    - 빌드/캐시 디렉토리 자동 skip.

    **결과 활용**:
    - 라인의 `file:line` 정보만으로 후보 판단 가능 → 다음 라인 처리.
    - 본문 필요 → `read_file_range(file, line-10, line+10)` 1번 호출 후 단서 등록.
    - 묶음 적재 X — 본문 1 개씩만.

    **실패 응답**:
    - `NO_MATCHES` — 매칭 0건. 변형 검색 (대소문자 / suffix / 별칭 / 부분 / 매크로 결합) ≤ 3 회 후에만 결론.

    **운영 주의**:
    - `bundle_get_chunk` (본문 통째 반환) 는 폐기 — $400 사고 원인. 이 도구는 라인 1 개만.
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
