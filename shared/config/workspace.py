"""요청이 쓸 작업 폴더 — 프로젝트마다 갈린다.

**작업 폴더가 서버에 하나였다.** ``ROBO_DATA_DIR/{source,ddl,analysis}`` 를 모든
사용자가 같이 썼고, 파싱은 그 폴더를 통째로 비우고 다시 쓴다. 그래서 이런 일이
일어났다 — 실측이다.

::

    A 사용자  hr-sample 업로드   →  analysis = 자바 AST
    B 사용자  c-src 올림         →  AST 는 여전히 자바
    B 의 분석 결과               →  자기 프로젝트에 **남의 코드**가 들어간다

**오류가 나지 않는다.** 노드 수도 그럴듯해서(FUNCTION 174 · RULE 127) 그래프 안의
MODULE 이름을 보기 전에는 알 수 없다. 실제로 세 프로젝트가 전부 같은 내용이었다.

대상 graph 는 이미 요청마다 갈라 놓았다(``X-Neo4j-Database`` ·
`graph/connection_context.py`). **같은 값으로 입력도 가른다.**

::

    <ROBO_DATA_DIR>/projects/<graph>/{source,ddl,analysis}

값이 없으면(CLI·단독 실행·옛 호출자) 예전처럼 루트를 쓴다 — 동작이 바뀌지 않는다.

파서도 같은 헤더로 **같은 규약**을 따른다 (``antlr-code-parser`` 의
``intake/WorkspaceScope.java`` — 경로 모양도 허용 문자도 같다). **둘이 어긋나면
분석기가 빈 AST 를 읽고 조용히 0건이 된다.**
"""
from __future__ import annotations

import contextvars
from pathlib import Path
import re
from typing import Optional

from shared.config.app_settings import SETTINGS

# 프로젝트별 폴더가 모이는 자리. 옛 배치(루트 직속)와 섞이지 않게 한 칸 둔다.
_PROJECTS = "projects"

# graph 이름이 그대로 경로가 된다. 이 코드가 여는 폴더는 **지워지는** 쪽이므로
# `../` 같은 것이 들어오면 작업 폴더 밖을 비우게 된다.
# 파서의 `WorkspaceScope.sanitize` 와 **같은 식**이어야 한다.
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_workspace: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "robo_workspace", default=None
)


def sanitize(raw: Optional[str]) -> Optional[str]:
    """폴더 이름으로 쓸 수 있는 값만 통과시킨다. 아니면 ``None``(= 범위 없음)."""
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed if _SAFE.match(trimmed) else None


def set_workspace(raw: Optional[str]) -> None:
    """요청 경계에서 1회.

    ``asyncio.create_task`` 가 컨텍스트를 복사하므로 파이프라인 전 단계가 같은 값을
    본다 — 단계마다 인자로 들고 다닐 필요가 없다. 전역 변수로 두면 다음 요청에
    샌다(파서 쪽은 스레드 풀 재사용 때문에 같은 위험이 있었다).
    """
    _workspace.set(sanitize(raw))


def current_workspace() -> Optional[str]:
    return _workspace.get()


def data_dir() -> Path:
    """이 요청이 쓸 뿌리. 범위가 없으면 예전 자리(``SETTINGS.analysis_data.dir``)."""
    workspace = _workspace.get()
    base = SETTINGS.analysis_data.dir
    return base / _PROJECTS / workspace if workspace else base
