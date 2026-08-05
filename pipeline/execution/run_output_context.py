"""현재 분석 run의 산출물 경로와 LLM 응답 언어 컨텍스트.

명시적인 ``--run-output``이 있으면 해당 경로를, 없으면 기존 설정 경로를 사용한다.
하위 run 디렉터리를 만들지 않으며 선택된 경로 자체가 현행 run 출력 루트다.
"""
from __future__ import annotations

import contextvars
from pathlib import Path


_run_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "analysis_run_output_dir", default=None
)
_response_locale: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "analysis_response_locale", default=None
)


def set_run_dir(run_dir: Path) -> contextvars.Token:
    if not isinstance(run_dir, Path):
        raise TypeError(f"run_dir must be a Path; got {run_dir!r}")
    return _run_dir.set(run_dir)


def reset_run_dir(token: contextvars.Token) -> None:
    _run_dir.reset(token)


def get_run_dir(*, fallback: Path | None = None) -> Path:
    run_dir = _run_dir.get()
    if run_dir is not None:
        return run_dir
    if fallback is not None:
        return fallback
    raise RuntimeError("analysis run output context is not set")


def active_run_dir() -> Path | None:
    """현재 활성 분석 run의 출력 루트. 독립 호출·단위 테스트면 ``None``."""
    return _run_dir.get()


def set_response_locale(locale: str) -> contextvars.Token:
    if not isinstance(locale, str) or not locale.strip():
        raise ValueError("response locale must be a non-blank string")
    return _response_locale.set(locale.strip())


def reset_response_locale(token: contextvars.Token) -> None:
    _response_locale.reset(token)


def get_response_locale(*, fallback: str = "ko") -> str:
    return _response_locale.get() or fallback
