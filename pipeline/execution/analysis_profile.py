"""분석 실행 프로필 로드·우선순위 해석·preflight 단일 진실.

프로필은 데이터 자체가 아니라 경로 참조다. 이 모듈은 환경변수를 쓰지 않고, 호출자가 넘긴
명시 인자 > 프로필 > 기본값 순서만 적용한다. CLI와 CI가 같은 함수를 직접 사용한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


_ALLOWED_KEYS = {
    "name", "data_dir", "strategy", "neo4j_database", "locale", "run_output",
}
_REQUIRED_KEYS = {
    "data_dir", "strategy", "neo4j_database", "locale",
}
_STRATEGIES = {"framework", "dbms"}
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True)
class AnalysisProfile:
    source_path: Path
    data_dir: Path
    strategy: str
    neo4j_database: str
    locale: str
    run_output: Path | None = None
    name: str | None = None


@dataclass(frozen=True)
class ResolvedAnalysisInputs:
    data_dir: Path
    source_dir: Path
    ddl_dir: Path | None
    strategy: str
    neo4j_database: str
    locale: str
    run_output: Path
    profile_path: Path | None = None


def _nonblank_string(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"analysis profile {key!r} must be a non-blank string")
    return value.strip()


def _profile_path(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _validate_strategy(value: str) -> str:
    if value not in _STRATEGIES:
        raise ValueError(f"strategy must be one of {sorted(_STRATEGIES)}; got {value!r}")
    return value


def _validate_database(value: str) -> str:
    value = value.strip()
    if not value or value.lower() == "system":
        raise ValueError("neo4j_database must be non-blank and must not be 'system'")
    return value


def _validate_locale(value: str) -> str:
    value = value.strip()
    if not _LOCALE.fullmatch(value):
        raise ValueError(f"locale must be a language tag such as 'ko' or 'en-US'; got {value!r}")
    return value


def _validate_output_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == Path.cwd().resolve():
        raise ValueError("run_output must not be the current working directory")
    if resolved == Path(resolved.anchor):
        raise ValueError("run_output must not be a filesystem root")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"run_output is not a directory: {resolved}")
    return resolved


def load_analysis_profile(path: Path) -> AnalysisProfile:
    source_path = path.expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"analysis profile file not found: {source_path}")
    try:
        loaded = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid analysis profile YAML: {source_path}") from error
    if not isinstance(loaded, dict):
        raise ValueError("analysis profile root must be a mapping")

    unknown = set(loaded) - _ALLOWED_KEYS
    missing = _REQUIRED_KEYS - set(loaded)
    if unknown:
        raise ValueError(f"unknown analysis profile keys: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing analysis profile keys: {sorted(missing)}")

    base = source_path.parent
    name_value = loaded.get("name")
    if name_value is not None and (not isinstance(name_value, str) or not name_value.strip()):
        raise ValueError("analysis profile 'name' must be a non-blank string when supplied")
    return AnalysisProfile(
        source_path=source_path,
        name=name_value.strip() if isinstance(name_value, str) else None,
        data_dir=_profile_path(base, _nonblank_string(loaded, "data_dir")),
        strategy=_validate_strategy(_nonblank_string(loaded, "strategy")),
        neo4j_database=_validate_database(_nonblank_string(loaded, "neo4j_database")),
        locale=_validate_locale(_nonblank_string(loaded, "locale")),
        run_output=(
            _validate_output_path(
                _profile_path(base, _nonblank_string(loaded, "run_output"))
            )
            if "run_output" in loaded
            else None
        ),
    )


def resolve_analysis_inputs(
    *,
    profile: AnalysisProfile | None,
    explicit_data_dir: Path | None,
    explicit_strategy: str | None,
    explicit_database: str | None,
    explicit_locale: str | None,
    explicit_run_output: Path | None,
    default_data_dir: Path,
    default_database: str,
    default_locale: str,
    default_run_output: Path,
    detected_strategy: str | None = None,
) -> ResolvedAnalysisInputs:
    """명시 CLI > 프로필 > 기본값 순으로 해석하고, 외부 변경 전에 전부 검증한다."""
    data_dir = (
        explicit_data_dir
        or (profile.data_dir if profile else None)
        or default_data_dir
    ).expanduser().resolve()
    strategy = (
        explicit_strategy
        or (profile.strategy if profile else None)
        or detected_strategy
    )
    if strategy is None:
        raise ValueError("strategy is unresolved; supply it or provide a detectable source tree")
    strategy = _validate_strategy(strategy)

    database = _validate_database(
        explicit_database
        or (profile.neo4j_database if profile else None)
        or default_database
    )
    locale = _validate_locale(
        explicit_locale
        or (profile.locale if profile else None)
        or default_locale
    )
    run_output = _validate_output_path(
        (
            explicit_run_output
            or (profile.run_output if profile else None)
            or default_run_output
        ).expanduser()
    )
    source_dir = data_dir / "source"
    if not source_dir.is_dir():
        raise ValueError(f"source directory not found: {source_dir}")
    ddl_candidate = data_dir / "ddl"
    ddl_dir = ddl_candidate if ddl_candidate.is_dir() else None

    return ResolvedAnalysisInputs(
        data_dir=data_dir,
        source_dir=source_dir,
        ddl_dir=ddl_dir,
        strategy=strategy,
        neo4j_database=database,
        locale=locale,
        run_output=run_output,
        profile_path=profile.source_path if profile else None,
    )


def preflight_lines(inputs: ResolvedAnalysisInputs) -> tuple[str, ...]:
    """CLI와 CI가 동일한 resolved 계약을 사람이 읽을 수 있게 표시한다."""
    return (
        f"profile: {inputs.profile_path or '(none)'}",
        f"data_dir: {inputs.data_dir}",
        f"source: {inputs.source_dir}",
        f"ddl: {inputs.ddl_dir or '(none)'}",
        f"strategy: {inputs.strategy}",
        f"neo4j_database: {inputs.neo4j_database}",
        f"locale: {inputs.locale}",
        f"run_output: {inputs.run_output}",
    )
