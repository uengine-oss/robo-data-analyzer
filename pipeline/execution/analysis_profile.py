"""Load and validate one reusable Analyzer CLI input profile.

The profile contains paths and run inputs, never source contents. Explicit CLI
arguments take precedence over the profile; the profile takes precedence over
process defaults. Validation completes before a model call or graph write begins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from pipeline.models.analysis_meta import normalize_selected_source_ids


_ALLOWED_KEYS = {
    "name",
    "data_dir",
    "strategy",
    "neo4j_database",
    "locale",
    "run_output",
    "selected_source_ids",
    "validation_baseline_ref",
    "validation_baseline_sha256",
    "validation_answer_key_ref",
    "validation_answer_key_sha256",
}
_REQUIRED_KEYS = {"data_dir", "strategy", "neo4j_database", "locale"}
_STRATEGIES = {"framework", "dbms"}
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class AnalysisProfile:
    source_path: Path
    data_dir: Path
    strategy: str
    neo4j_database: str
    locale: str
    run_output: Path | None = None
    name: str | None = None
    selected_source_ids: tuple[str, ...] = ()
    validation_baseline_ref: Path | None = None
    validation_baseline_sha256: str | None = None
    validation_answer_key_ref: Path | None = None
    validation_answer_key_sha256: str | None = None


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
    selected_source_ids: tuple[str, ...] = ()
    validation_baseline_ref: Path | None = None
    validation_baseline_sha256: str | None = None
    validation_answer_key_ref: Path | None = None
    validation_answer_key_sha256: str | None = None


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
        raise ValueError(
            f"locale must be a language tag such as 'ko' or 'en-US'; got {value!r}"
        )
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


def _reject_validation_reference_inside_run_output(
    *, reference: Path | None, field_name: str, run_output: Path,
) -> None:
    """Keep immutable validation inputs outside the mutable run-output tree."""
    if reference is None:
        return
    resolved_reference = reference.resolve()
    if resolved_reference == run_output or run_output in resolved_reference.parents:
        raise ValueError(
            f"{field_name} must be outside run_output because run retention may "
            f"remove mutable run artifacts: {resolved_reference}"
        )


def _optional_validation_reference(
    values: Mapping[str, Any], base: Path, stem: str,
) -> tuple[Path | None, str | None]:
    ref_key = f"{stem}_ref"
    hash_key = f"{stem}_sha256"
    raw_ref = values.get(ref_key)
    raw_hash = values.get(hash_key)
    if (raw_ref is None) != (raw_hash is None):
        raise ValueError(f"{ref_key} and {hash_key} must be supplied together")
    if raw_ref is None:
        return None, None
    if not isinstance(raw_ref, str) or not raw_ref.strip():
        raise ValueError(f"analysis profile {ref_key!r} must be a non-blank string")
    if not isinstance(raw_hash, str) or not _SHA256.fullmatch(raw_hash.strip()):
        raise ValueError(f"analysis profile {hash_key!r} must be a SHA-256 hex digest")
    path = _profile_path(base, raw_ref.strip())
    if not path.exists() or not (path.is_file() or path.is_dir()):
        raise ValueError(f"validation reference not found: {path}")
    return path, raw_hash.strip().lower()


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
    if name_value is not None and (
        not isinstance(name_value, str) or not name_value.strip()
    ):
        raise ValueError("analysis profile 'name' must be a non-blank string when supplied")
    baseline_ref, baseline_hash = _optional_validation_reference(
        loaded, base, "validation_baseline",
    )
    answer_ref, answer_hash = _optional_validation_reference(
        loaded, base, "validation_answer_key",
    )
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
        selected_source_ids=normalize_selected_source_ids(
            loaded.get("selected_source_ids") or ()
        ),
        validation_baseline_ref=baseline_ref,
        validation_baseline_sha256=baseline_hash,
        validation_answer_key_ref=answer_ref,
        validation_answer_key_sha256=answer_hash,
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
    """Resolve explicit CLI > profile > defaults before any external mutation."""
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
        explicit_locale or (profile.locale if profile else None) or default_locale
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

    baseline_ref = profile.validation_baseline_ref if profile else None
    answer_key_ref = profile.validation_answer_key_ref if profile else None
    _reject_validation_reference_inside_run_output(
        reference=baseline_ref,
        field_name="validation_baseline_ref",
        run_output=run_output,
    )
    _reject_validation_reference_inside_run_output(
        reference=answer_key_ref,
        field_name="validation_answer_key_ref",
        run_output=run_output,
    )

    return ResolvedAnalysisInputs(
        data_dir=data_dir,
        source_dir=source_dir,
        ddl_dir=ddl_dir,
        strategy=strategy,
        neo4j_database=database,
        locale=locale,
        run_output=run_output,
        profile_path=profile.source_path if profile else None,
        selected_source_ids=profile.selected_source_ids if profile else (),
        validation_baseline_ref=baseline_ref,
        validation_baseline_sha256=(
            profile.validation_baseline_sha256 if profile else None
        ),
        validation_answer_key_ref=answer_key_ref,
        validation_answer_key_sha256=(
            profile.validation_answer_key_sha256 if profile else None
        ),
    )


def preflight_lines(inputs: ResolvedAnalysisInputs) -> tuple[str, ...]:
    """Render the resolved run contract used by both CLI output and tests."""
    return (
        f"profile: {inputs.profile_path or '(none)'}",
        f"data_dir: {inputs.data_dir}",
        f"source: {inputs.source_dir}",
        f"ddl: {inputs.ddl_dir or '(none)'}",
        f"strategy: {inputs.strategy}",
        f"neo4j_database: {inputs.neo4j_database}",
        f"locale: {inputs.locale}",
        f"selected_source_ids: {list(inputs.selected_source_ids)}",
        f"validation_baseline: {inputs.validation_baseline_ref or '(none)'}",
        f"validation_answer_key: {inputs.validation_answer_key_ref or '(none)'}",
        f"run_output: {inputs.run_output}",
    )
