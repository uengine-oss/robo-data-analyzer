"""Exact source ↔ parser AST ↔ parser diagnostic run boundary.

The parser owns syntax.  Analyzer must therefore prove that every parser-supported
source file has exactly one current AST and one exact diagnostic before it may
materialize any semantic graph.  A warning or a zero-node ParseResult is not a
substitute for that closed population.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pipeline.languages import DBMS_SUFFIXES, EXT_TO_LANG, is_supported
from shared.files.cache import SourceFileCache


class SourceAstClosureError(ValueError):
    """Source, AST, and parser diagnostic do not form an exact closed population."""


@dataclass(frozen=True)
class SourceAstArtifact:
    source_id: str
    source_path: Path
    ast_path: Path
    diagnostic_path: Path
    source_sha256: str
    ast_sha256: str
    diagnostic_sha256: str
    language: str
    # Parsed once at the closure boundary. Step 2 consumes this exact object instead of
    # reopening the same AST path after its hash has already been sealed.
    ast_document: dict


@dataclass(frozen=True)
class SourceAstClosure:
    source_root: Path
    ast_root: Path
    artifacts: tuple[SourceAstArtifact, ...]

    def as_dict(self) -> dict:
        return {
            "schema_version": "1.0",
            "status": "exact",
            "expected_sources": len(self.artifacts),
            "ast_artifacts": len(self.artifacts),
            "diagnostics": len(self.artifacts),
            "artifacts": [
                {
                    "source_id": item.source_id,
                    "source_sha256": item.source_sha256,
                    "ast_sha256": item.ast_sha256,
                    "diagnostic_sha256": item.diagnostic_sha256,
                    "parser_status": "EXACT",
                    "language": item.language,
                }
                for item in self.artifacts
            ],
        }


def ast_json_paths(ast_root: Path) -> list[Path]:
    """Return every parser artifact in deterministic path order."""
    return sorted(path for path in ast_root.rglob("*.json") if path.is_file())


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _source_id(raw: object, *, owner: Path) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceAstClosureError(
            f"invalid parser sourcePath/filePath in {owner}: {value!r}"
        )
    return path.as_posix()


def _load_json_object(path: Path, *, kind: str) -> tuple[dict, str]:
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(decoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceAstClosureError(f"invalid {kind} artifact {path}: {error}") from error
    if not isinstance(value, dict):
        raise SourceAstClosureError(f"invalid {kind} artifact {path}: object required")
    return value, _sha256(payload)


def _diagnostic_language(source_id: str, diagnostic: dict) -> str:
    language = str(diagnostic.get("language") or "").strip().casefold()
    suffix = Path(source_id).suffix.lower()
    if suffix in DBMS_SUFFIXES:
        allowed = {"oracle", "postgresql"}
    else:
        expected = EXT_TO_LANG.get(suffix.lstrip("."), "")
        allowed = {expected} if expected else set()
    if language not in allowed:
        raise SourceAstClosureError(
            f"parser diagnostic language mismatch for {source_id}: "
            f"got {language!r}, expected {sorted(allowed)!r}"
        )
    return language


def _validate_exact_diagnostic(
    source_id: str,
    source_sha256: str,
    diagnostic_path: Path,
) -> tuple[str, str]:
    diagnostic, diagnostic_sha256 = _load_json_object(
        diagnostic_path, kind="parser diagnostic"
    )
    diagnostic_source = _source_id(diagnostic.get("sourcePath"), owner=diagnostic_path)
    if diagnostic_source != source_id:
        raise SourceAstClosureError(
            f"parser diagnostic sourcePath mismatch: AST={source_id!r}, "
            f"diagnostic={diagnostic_source!r}"
        )
    recorded_sha = str(diagnostic.get("sourceSha256") or "").strip().lower()
    if recorded_sha != source_sha256:
        raise SourceAstClosureError(
            f"parser diagnostic sourceSha256 mismatch for {source_id}: "
            f"diagnostic={recorded_sha!r}, source={source_sha256!r}"
        )
    status = str(diagnostic.get("status") or "").strip().upper()
    first_pass = diagnostic.get("firstPass") or {}
    coverage = first_pass.get("coverage") or {} if isinstance(first_pass, dict) else {}
    summary = diagnostic.get("summary") or {}
    exact = (
        status == "EXACT"
        and isinstance(coverage, dict)
        and coverage.get("knownAndComplete") is True
        and not list(coverage.get("missingDeclarations") or ())
        and int(first_pass.get("antlrRecoveries") or 0) == 0
        and isinstance(summary, dict)
        and int(summary.get("lexerErrors") or 0) == 0
        and int(summary.get("parserErrors") or 0) == 0
    )
    if not exact:
        raise SourceAstClosureError(
            f"parser diagnostic is not EXACT/complete for {source_id}: status={status!r}"
        )
    return _diagnostic_language(source_id, diagnostic), diagnostic_sha256


def verify_source_ast_closure(
    *,
    project_root: Path,
    ast_root: Path,
    source_root: Path,
    source_cache: SourceFileCache,
    selected_source_ids: tuple[str, ...] = (),
) -> SourceAstClosure:
    """Fail closed unless supported source, AST, and diagnostics are exact one-to-one."""
    project_root = project_root.resolve()
    source_root = source_root.resolve()
    ast_root = ast_root.resolve()
    if not project_root.is_dir():
        raise SourceAstClosureError(f"source root is not a directory: {project_root}")
    if not ast_root.is_dir():
        raise SourceAstClosureError(f"AST root is not a directory: {ast_root}")
    try:
        project_root.relative_to(source_root)
    except ValueError as error:
        raise SourceAstClosureError(
            f"project source root is outside parser source root: {project_root}"
        ) from error

    expected: dict[str, tuple[Path, str]] = {}
    selected = tuple(selected_source_ids)
    if len(selected) != len(set(selected)):
        raise SourceAstClosureError("selected source IDs contain duplicates")
    if selected:
        source_paths: list[Path] = []
        for source_id in sorted(selected):
            relative = PurePosixPath(source_id)
            if (
                relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise SourceAstClosureError(
                    f"invalid selected source ID: {source_id!r}"
                )
            path = source_root.joinpath(*relative.parts).resolve()
            try:
                path.relative_to(project_root)
            except ValueError as error:
                raise SourceAstClosureError(
                    f"selected source is outside project root: {source_id!r}"
                ) from error
            if not path.is_file():
                raise SourceAstClosureError(
                    f"selected source file not found: {source_id!r}"
                )
            if not is_supported(path.suffix):
                raise SourceAstClosureError(
                    f"selected source type is unsupported: {source_id!r}"
                )
            source_paths.append(path)
    else:
        source_paths = sorted(
            item for item in project_root.rglob("*") if item.is_file()
        )

    for path in source_paths:
        if not is_supported(path.suffix):
            continue
        source_id = path.relative_to(source_root).as_posix()
        payload = source_cache.read_bytes(path)
        # Decoding belongs to the Parser evidence envelope.  The later AST read binds
        # these exact bytes to sourceEncoding/decodedText before any graph mutation.
        expected[source_id] = (path.resolve(), _sha256(payload))

    if not expected:
        raise SourceAstClosureError(
            f"no parser-supported source files under {project_root}"
        )

    ast_by_source: dict[str, tuple[Path, str, dict]] = {}
    for ast_path in ast_json_paths(ast_root):
        raw, ast_sha256 = _load_json_object(ast_path, kind="AST")
        if str(raw.get("type") or "").strip() != "FILE":
            raise SourceAstClosureError(f"AST root must be FILE: {ast_path}")
        source_id = _source_id(raw.get("filePath"), owner=ast_path)
        if selected and source_id not in expected:
            continue
        file_name = str(raw.get("fileName") or "").strip()
        if file_name != PurePosixPath(source_id).name:
            raise SourceAstClosureError(
                f"AST fileName/filePath mismatch at {ast_path}: "
                f"fileName={file_name!r}, filePath={source_id!r}"
            )
        if source_id in ast_by_source:
            raise SourceAstClosureError(
                f"duplicate AST artifacts for {source_id}: "
                f"{ast_by_source[source_id][0]} and {ast_path}"
            )
        ast_by_source[source_id] = (ast_path.resolve(), ast_sha256, raw)

    expected_ids = set(expected)
    ast_ids = set(ast_by_source)
    missing_ast = sorted(expected_ids - ast_ids)
    orphan_ast = sorted(ast_ids - expected_ids)
    if missing_ast or orphan_ast:
        raise SourceAstClosureError(
            f"source/AST population mismatch: missing_ast={missing_ast}, orphan_ast={orphan_ast}"
        )

    diagnostics_root = (source_root.parent / "diagnostics").resolve()
    artifacts: list[SourceAstArtifact] = []
    for source_id in sorted(expected):
        source_path, source_sha256 = expected[source_id]
        ast_path, ast_sha256, raw_ast = ast_by_source[source_id]
        diagnostic_path = (diagnostics_root / f"{source_id}.parse.json").resolve()
        try:
            diagnostic_path.relative_to(diagnostics_root)
        except ValueError as error:
            raise SourceAstClosureError(
                f"parser diagnostic path outside root for {source_id}"
            ) from error
        if not diagnostic_path.is_file():
            raise SourceAstClosureError(
                f"missing parser diagnostic for {source_id}: {diagnostic_path}"
            )
        language, diagnostic_sha256 = _validate_exact_diagnostic(
            source_id, source_sha256, diagnostic_path
        )
        artifacts.append(SourceAstArtifact(
            source_id=source_id,
            source_path=source_path,
            ast_path=ast_path,
            diagnostic_path=diagnostic_path,
            source_sha256=source_sha256,
            ast_sha256=ast_sha256,
            diagnostic_sha256=diagnostic_sha256,
            language=language,
            ast_document=raw_ast,
        ))

    return SourceAstClosure(
        source_root=source_root,
        ast_root=ast_root,
        artifacts=tuple(artifacts),
    )
