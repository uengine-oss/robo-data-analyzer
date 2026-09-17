"""작업 폴더가 프로젝트마다 갈리는가.

**서버에 하나였다.** 그래서 이런 일이 일어났다 — 실측이다.

::

    alice   hr-sample 업로드     →  data/analysis = 자바 AST
    test    c-src 올림           →  AST 는 여전히 자바
    분석                         →  test 의 프로젝트 graph 에 자바가 들어간다

세 프로젝트의 분석 짝을 열어 보니 MODULE 이름이 전부 같았다. **오류가 없고 노드
수도 그럴듯해서**(FUNCTION 174 · RULE 127) 그래프 안을 보기 전에는 몰랐다.

그래서 여기서 재는 것은 "함수가 뭘 돌려주나" 가 아니라 **두 프로젝트가 서로의
입력을 보는가** 다.
"""
from __future__ import annotations

import contextvars
from pathlib import Path
import unittest

from tests.unittest_function_cases import load_function_cases

from shared.config.app_settings import SETTINGS
from shared.config.workspace import (
    current_workspace,
    data_dir,
    sanitize,
    set_workspace,
)


def _in_context(fn):
    """요청 하나를 흉내 낸다 — contextvar 를 격리해서 돌린다."""
    return contextvars.copy_context().run(fn)


def _scoped(workspace: str) -> Path:
    return _in_context(lambda: (set_workspace(workspace), data_dir())[1])


def test_two_projects_never_see_each_other_input() -> None:
    a = _scoped("prj_aaa")
    b = _scoped("prj_bbb")
    assert a != b
    # 한쪽이 다른 쪽 밑에 있으면 **지우기가 서로를 건드린다.** 파싱은 폴더를
    # 통째로 비우고 다시 쓴다.
    assert not str(a).startswith(str(b))
    assert not str(b).startswith(str(a))


def test_no_scope_keeps_the_old_location() -> None:
    """CLI·단독 실행·옛 호출자의 동작이 바뀌면 안 된다."""
    assert _in_context(data_dir) == SETTINGS.analysis_data.dir


def test_scope_does_not_leak_into_the_next_request() -> None:
    """contextvar 를 안 쓰고 전역에 두면 이 검사가 깨진다 — 파서 쪽은 스레드 풀
    재사용 때문에 같은 위험이 있었다."""
    _in_context(lambda: set_workspace("prj_aaa"))
    assert current_workspace() is None


def test_names_that_cannot_be_folders_are_not_used_as_scope() -> None:
    """graph 이름이 그대로 경로가 된다. **이 폴더는 지워지는 쪽**이라 `../` 가
    통과하면 작업 폴더 밖을 비운다."""
    for raw in ["../../etc", "prj/../..", "a b", "", "   ", None, "x" * 200, "prj;rm -rf"]:
        assert sanitize(raw) is None, raw


def test_ordinary_graph_names_pass() -> None:
    """전부 막으면 격리는 되지만 **아무것도 안 갈린다** — 반대쪽도 재야 한다."""
    for raw in ["prj_aaa", "prj-1", "analyzer_run", "A1", "robo"]:
        assert sanitize(raw) == raw, raw


def test_source_ddl_analysis_share_one_root() -> None:
    """파서와 분석기가 어긋나면 분석기가 빈 AST 를 읽고 **조용히 0건**이 된다.
    파서의 `WorkspaceScope.rootOf` 가 만드는 모양과 같아야 한다:
    ``<data>/projects/<graph>/{source,ddl,analysis}``."""
    root = _scoped("prj_aaa")
    for child in ("source", "ddl", "analysis"):
        assert (root / child).parent == root
    assert root.name == "prj_aaa"
    assert root.parent.name == "projects"
    assert root.parent.parent == SETTINGS.analysis_data.dir


def test_router_scopes_its_input_by_the_target_graph() -> None:
    """**분리는 라우터에서 성립한다.** 위 검사들이 전부 통과해도 라우터가
    `set_workspace` 를 안 부르면 입력은 여전히 공용 폴더다 — 그게 원래 증상이었다.

    소스를 읽어서 확인한다. 라우터를 import 하면 FastAPI·파이프라인이 전부 딸려
    오고, 여기서 재려는 것은 **그 한 줄이 있는가** 다.
    """
    source = Path(__file__).resolve().parents[2] / "api" / "analyze_router.py"
    text = source.read_text(encoding="utf-8")
    assert "from shared.config.workspace import" in text, "라우터가 workspace 를 안 쓴다"
    assert "set_workspace(" in text, "라우터가 요청 경계에서 범위를 안 세운다"
    assert "data_dir()" in text, "라우터가 공용 폴더를 그대로 쓴다"
    assert "SETTINGS.analysis_data.dir / \"source\"" not in text, (
        "라우터가 아직 공용 source 폴더를 직접 가리킨다"
    )


class WorkspaceIsolationTest(unittest.TestCase):
    def test_isolation(self) -> None:
        test_two_projects_never_see_each_other_input()
        test_scope_does_not_leak_into_the_next_request()

    def test_compatibility(self) -> None:
        test_no_scope_keeps_the_old_location()

    def test_name_rules(self) -> None:
        test_names_that_cannot_be_folders_are_not_used_as_scope()
        test_ordinary_graph_names_pass()

    def test_layout(self) -> None:
        test_source_ddl_analysis_share_one_root()

    def test_router_wiring(self) -> None:
        test_router_scopes_its_input_by_the_target_graph()


if __name__ == "__main__":
    unittest.main()


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
