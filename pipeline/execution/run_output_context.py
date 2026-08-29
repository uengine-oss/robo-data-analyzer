"""현재 분석 run이 소유하는 산출물 경로와 LLM 응답 언어 컨텍스트.

산출물을 만들거나 프롬프트를 렌더링하는 경로는 반드시 분석 run 안에서 실행한다.
소유자가 없으면 전역 경로나 임의 언어를 고르지 않고 계약 오류로 중단한다.
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


def get_run_dir() -> Path:
    run_dir = _run_dir.get()
    if run_dir is not None:
        return run_dir
    raise RuntimeError("analysis run output context is not set")


def active_run_dir() -> Path | None:
    """현재 활성 분석 run의 출력 루트. 읽기 전용 점검이면 ``None``."""
    return _run_dir.get()


def set_response_locale(locale: str) -> contextvars.Token:
    if not isinstance(locale, str) or not locale.strip():
        raise ValueError("response locale must be a non-blank string")
    return _response_locale.set(locale.strip())


def reset_response_locale(token: contextvars.Token) -> None:
    _response_locale.reset(token)


def get_response_locale() -> str:
    locale = _response_locale.get()
    if locale is None:
        raise RuntimeError("analysis response locale context is not set")
    return locale
