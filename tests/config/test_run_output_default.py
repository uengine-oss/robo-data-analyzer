"""분석 산출물 기본 자리가 **얕은 트리에서도** 정해지는가.

`SETTINGS` 는 `shared/config/app_settings.py` **모듈 최상위**에서 만들어진다. 그래서
여기서 예외가 나면 설정 오류가 아니라 **import 실패**가 되고, 앱이 아예 안 뜬다.

실측(2026-09-17): 저장소의 Dockerfile 로 구운 analyzer 이미지가 그 자리에서 죽었다.

::

    File "/app/shared/config/app_settings.py", line 329, in _resolve_run_output_dir
        Path(__file__).resolve().parents[4]
    IndexError: 4

기본값이 **개발 체크아웃의 모양**(위로 네 칸)을 가정했기 때문이다. 컨테이너에서는
이 파일이 `/app/shared/config/` 에 있어 위로 세 칸뿐이다. 개발 환경에서는 안 드러난다.
"""
from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
import unittest

from tests.unittest_function_cases import load_function_cases

from shared.config.app_settings import _run_output_default

_TAIL = ("_playground", "analyzer", "runs", "live")


def test_container_layout_resolves() -> None:
    """`/app/shared/config/app_settings.py` — 위로 세 칸뿐이다."""
    got = _run_output_default(PurePosixPath("/app/shared/config/app_settings.py"))
    assert got == PurePosixPath("/app").joinpath(*_TAIL), got


def test_development_checkout_is_unchanged() -> None:
    """깊은 트리에서는 예전 값 그대로여야 한다 — 개발자 동작이 바뀌면 안 된다."""
    config = PurePosixPath(
        "/home/u/robo/robo-architect/robo-analyzer/robo-data-analyzer"
        "/shared/config/app_settings.py"
    )
    assert _run_output_default(config) == PurePosixPath(
        "/home/u/robo/robo-architect"
    ).joinpath(*_TAIL)


def test_windows_layout_resolves() -> None:
    """납품은 Windows 다. 경로 종류가 달라도 같은 규칙이어야 한다."""
    config = PureWindowsPath(r"C:\app\shared\config\app_settings.py")
    assert _run_output_default(config) == PureWindowsPath(r"C:\app").joinpath(*_TAIL)


class RunOutputDefaultTest(unittest.TestCase):
    def test_shallow_tree(self) -> None:
        test_container_layout_resolves()
        test_windows_layout_resolves()

    def test_deep_tree_unchanged(self) -> None:
        test_development_checkout_is_unchanged()


if __name__ == "__main__":
    unittest.main()


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
