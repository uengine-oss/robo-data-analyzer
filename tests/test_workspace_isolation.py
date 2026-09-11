"""작업 폴더가 프로젝트마다 갈리는가.

**서버에 하나였다.** 그래서 이런 일이 일어났다 — 실측이다.

::

    alice   hr-sample 업로드     →  data/analysis = 자바 AST
    test    c-src 폴더 지정      →  AST 는 여전히 자바
    분석                         →  test 의 프로젝트 graph 에 자바가 들어간다

세 프로젝트의 분석 짝을 열어 보니 MODULE 이름이 전부 같았다. **오류가 없고 노드
수도 그럴듯해서**(FUNCTION 174 · RULE 127) 그래프 안을 보기 전에는 몰랐다.

그래서 여기서 재는 것은 "함수가 뭘 돌려주나"가 아니라 **두 프로젝트가 서로의
입력을 보는가** 다.
"""
from __future__ import annotations

import contextvars
from pathlib import Path

import pytest

from shared.config.workspace import (
    current_workspace,
    data_dir,
    sanitize,
    set_workspace,
)


def _in_context(fn):
    """요청 하나를 흉내 낸다 — contextvar 를 격리해서 돌린다."""
    return contextvars.copy_context().run(fn)


def test_두_프로젝트는_서로의_입력을_안_본다():
    a = _in_context(lambda: (set_workspace("prj_aaa"), data_dir())[1])
    b = _in_context(lambda: (set_workspace("prj_bbb"), data_dir())[1])
    assert a != b
    # 한쪽이 다른 쪽 밑에 있으면 지우기가 서로를 건드린다.
    assert not str(a).startswith(str(b))
    assert not str(b).startswith(str(a))


def test_범위가_없으면_예전_자리를_쓴다():
    """CLI·단독 실행·옛 호출자의 동작이 바뀌면 안 된다."""
    from shared.config.env_catalog import ENV

    assert _in_context(data_dir) == ENV.data.dir


def test_요청이_끝나도_다음_요청에_안_샌다():
    """contextvar 를 안 쓰고 전역에 두면 이 검사가 깨진다 — 실제로 파서 쪽은
    스레드 풀 재사용 때문에 같은 위험이 있었다."""
    _in_context(lambda: set_workspace("prj_aaa"))
    assert current_workspace() is None


@pytest.mark.parametrize(
    "raw",
    ["../../etc", "prj/../..", "a b", "", "   ", None, "x" * 200, "prj;rm -rf"],
)
def test_폴더_이름이_못_되는_값은_범위로_안_쓴다(raw):
    """graph 이름이 그대로 경로가 된다. **이 폴더는 지워지는 쪽**이라, `../` 가
    통과하면 작업 폴더 밖을 비운다."""
    assert sanitize(raw) is None


@pytest.mark.parametrize("raw", ["prj_aaa", "prj-1", "analyzer_run", "A1"])
def test_멀쩡한_graph_이름은_통과한다(raw):
    """전부 막으면 격리는 되지만 아무것도 안 갈린다 — 반대쪽도 재야 한다."""
    assert sanitize(raw) == raw


def test_ddl_과_analysis_가_같은_뿌리_아래에_있다():
    """파서와 분석기가 어긋나면 분석기가 빈 AST 를 읽고 **조용히 0건**이 된다."""
    root = _in_context(lambda: (set_workspace("prj_aaa"), data_dir())[1])
    assert (root / "source").parent == root
    assert (root / "ddl").parent == root
    assert (root / "analysis").parent == root
    assert Path("projects") in root.parents or "projects" in str(root)
