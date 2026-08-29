"""Build the single immutable input identity before any graph or LLM mutation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from llm.configs.loader import load_llm_config
from llm.model_runtime_capabilities import ModelRuntimeCapabilities
from pipeline.models.analysis_meta import Codebase
from shared.files.cache import SourceFileCache


SCHEMA_VERSION = "sealed-analysis-inputs/v1"
_IMPLEMENTATION_DIRECTORIES = (
    "agents", "api", "graph", "integrations", "llm", "pipeline", "search", "shared",
)
_IMPLEMENTATION_FILES = ("main.py", "requirements.txt", "Dockerfile", "__init__.py")
_CONTRACT_FILES = (
    ".specify/memory/constitution.md",
    "specs/131-cross-node-semantic-grounding/README.md",
    "specs/131-cross-node-semantic-grounding/spec.md",
    "specs/131-cross-node-semantic-grounding/plan.md",
    "specs/131-cross-node-semantic-grounding/tasks.md",
    "specs/131-cross-node-semantic-grounding/contracts/parser-structural-facts-v2.md",
    "specs/131-cross-node-semantic-grounding/contracts/analyzer-meaning-and-publication-v2.md",
)
_SECRET_FIELDS = {
    ("analysis_llm", "api_key"),
    ("embedding", "api_key"),
    ("graph_database", "password"),
}


class SealedAnalysisInputsError(ValueError):
    """The run inputs cannot be represented as one immutable content identity."""


@dataclass(frozen=True)
class SealedAnalysisInputs:
    payload: dict[str, Any]
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {**self.payload, "sealed_analysis_inputs_sha256": self.sha256}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.expanduser().resolve())
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SealedAnalysisInputsError(
        f"analysis setting is not canonically serializable: {type(value).__name__}"
    )


def _settings_snapshot(settings: Any) -> dict[str, Any]:
    if not is_dataclass(settings):
        raise SealedAnalysisInputsError("Analyzer settings must be a dataclass instance")
    snapshot = asdict(settings)
    for section, field_name in _SECRET_FIELDS:
        section_values = snapshot.get(section)
        if isinstance(section_values, dict):
            section_values.pop(field_name, None)
    return _normalize(snapshot)


def _tree_seal(
    *,
    root: Path,
    paths: Iterable[Path],
    content_cache: SourceFileCache | None = None,
) -> dict[str, Any]:
    resolved_root = root.expanduser().resolve()
    entries: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    total_bytes = 0
    unique_paths = sorted({path.expanduser().resolve() for path in paths})
    for path in unique_paths:
        if not path.is_file():
            raise SealedAnalysisInputsError(f"sealed input file not found: {path}")
        try:
            relative = path.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise SealedAnalysisInputsError(
                f"sealed input file escapes its root: root={resolved_root} file={path}"
            ) from error
        payload = (
            content_cache.read_bytes(path)
            if content_cache is not None
            else path.read_bytes()
        )
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        total_bytes += len(payload)
        entries.append({
            "relative_path": relative,
            "sha256": _sha256(payload),
            "size_bytes": len(payload),
        })
    return {
        "root": str(resolved_root),
        "tree_sha256": digest.hexdigest(),
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
    }


def _content_reference(
    *,
    ref: Path | None,
    expected_sha256: str | None,
    field_name: str,
    content_cache: SourceFileCache,
) -> dict[str, Any]:
    if ref is None and expected_sha256 is None:
        return {"supplied": False}
    if ref is None or expected_sha256 is None:
        raise SealedAnalysisInputsError(
            f"{field_name}_ref and {field_name}_sha256 must be supplied together"
        )
    path = ref.expanduser().resolve()
    try:
        payload = content_cache.read_bytes(path)
    except OSError as error:
        raise SealedAnalysisInputsError(f"cannot read {field_name}: {path}: {error}") from error
    actual = _sha256(payload)
    expected = expected_sha256.strip().lower()
    if actual != expected:
        raise SealedAnalysisInputsError(
            f"{field_name} SHA-256 mismatch: expected={expected} actual={actual} ref={path}"
        )
    return {
        "supplied": True,
        "ref": str(path),
        "sha256": actual,
        "size_bytes": len(payload),
    }


def _implementation_paths(repository_root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in _IMPLEMENTATION_DIRECTORIES:
        root = repository_root / relative
        if not root.is_dir():
            raise SealedAnalysisInputsError(f"implementation directory not found: {root}")
        paths.extend(
            path for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
            and not (
                relative == "llm"
                and path.relative_to(root).parts
                and path.relative_to(root).parts[0]
                in {"prompts", "configs", "runtime_capabilities"}
            )
        )
    for relative in _IMPLEMENTATION_FILES:
        paths.append(repository_root / relative)
    return paths


def build_sealed_analysis_inputs(
    *,
    codebase: Codebase,
    neo4j_database: str,
    project_input_paths: Iterable[Path],
    source_ast_closure_ref: Path,
    source_ast_closure_bytes: bytes,
    source_ast_closure_sha256: str,
    strategy_prompts_dir: Path,
    human_glossary_manifest: Mapping[str, Any],
    source_cache: SourceFileCache,
    model_runtime: ModelRuntimeCapabilities,
    settings: Any,
    repository_root: Path,
) -> SealedAnalysisInputs:
    """Hash every product input family once and return one canonical run identity."""
    repository_root = repository_root.expanduser().resolve()
    project_root = codebase.root.expanduser().resolve()
    project_paths = list(project_input_paths)
    ddl_root = codebase.ddl_dir.expanduser().resolve() if codebase.ddl_dir else None
    ddl_paths = (
        [path for path in ddl_root.rglob("*.sql") if path.is_file()]
        if ddl_root is not None else []
    )
    prompts_root = strategy_prompts_dir.expanduser().resolve().parent
    if prompts_root.name != "prompts":
        prompts_root = (repository_root / "llm" / "prompts").resolve()
    prompt_paths = [path for path in prompts_root.rglob("*") if path.is_file()]
    contract_paths = [(repository_root / relative).resolve() for relative in _CONTRACT_FILES]

    llm_config_name = settings.analysis_llm.config
    llm_config_path = (repository_root / "llm" / "configs" / f"{llm_config_name}.yaml").resolve()
    if llm_config_path.parent != (repository_root / "llm" / "configs").resolve():
        raise SealedAnalysisInputsError("LLM config path escapes llm/configs")
    llm_config = load_llm_config(llm_config_name)
    llm_config_bytes = source_cache.read_bytes(llm_config_path)
    effective_settings = _settings_snapshot(settings)
    effective_settings_bytes = _canonical_bytes(effective_settings)

    source_ast_path = source_ast_closure_ref.expanduser().resolve()
    if not source_ast_path.is_file():
        raise SealedAnalysisInputsError(
            f"source_ast_closure artifact not found: {source_ast_path}"
        )
    actual_source_ast_sha256 = _sha256(source_ast_closure_bytes)
    if actual_source_ast_sha256 != source_ast_closure_sha256:
        raise SealedAnalysisInputsError(
            "source_ast_closure SHA-256 mismatch: "
            f"expected={source_ast_closure_sha256} actual={actual_source_ast_sha256}"
        )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_request": {
            "source_root": str(project_root),
            "corpus_root": str(codebase.corpus_root.resolve()),
            "ddl_root": str(ddl_root) if ddl_root else None,
            "strategy": codebase.strategy,
            "locale": codebase.locale,
            "datasource": codebase.datasource,
            "neo4j_database": neo4j_database,
            "selected_source_ids": list(codebase.selected_source_ids),
        },
        "analysis_scope_files": _tree_seal(
            root=project_root, paths=project_paths, content_cache=source_cache,
        ),
        "ddl_files": (
            _tree_seal(root=ddl_root, paths=ddl_paths, content_cache=source_cache)
            if ddl_root is not None else {"supplied": False}
        ),
        "source_ast_closure": {
            "ref": str(source_ast_path),
            "sha256": actual_source_ast_sha256,
            "size_bytes": len(source_ast_closure_bytes),
        },
        "llm_configuration": {
            "ref": str(llm_config_path),
            "sha256": _sha256(llm_config_bytes),
            "effective_values_sha256": _sha256(_canonical_bytes(asdict(llm_config))),
        },
        "effective_analyzer_settings": {
            "sha256": _sha256(effective_settings_bytes),
            "values": effective_settings,
        },
        "prompt_contracts": _tree_seal(
            root=prompts_root, paths=prompt_paths, content_cache=source_cache,
        ),
        "analyzer_contracts": _tree_seal(
            root=repository_root, paths=contract_paths, content_cache=source_cache,
        ),
        "analyzer_implementation": _tree_seal(
            root=repository_root,
            paths=_implementation_paths(repository_root),
            content_cache=source_cache,
        ),
        "human_glossary": _normalize(dict(human_glossary_manifest)),
        "model_runtime_capabilities": {
            "ref": str(model_runtime.artifact_path),
            "sha256": model_runtime.artifact_sha256,
            **model_runtime.sealed_values(),
        },
        "validation_baseline": _content_reference(
            ref=codebase.validation_baseline_ref,
            expected_sha256=codebase.validation_baseline_sha256,
            field_name="validation_baseline",
            content_cache=source_cache,
        ),
        "validation_answer_key": _content_reference(
            ref=codebase.validation_answer_key_ref,
            expected_sha256=codebase.validation_answer_key_sha256,
            field_name="validation_answer_key",
            content_cache=source_cache,
        ),
    }
    return SealedAnalysisInputs(payload=payload, sha256=_sha256(_canonical_bytes(payload)))


def write_sealed_analysis_inputs(path: Path, sealed: SealedAnalysisInputs) -> None:
    """Write the immutable seal exactly once; never overwrite a prior run identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(sealed.as_dict(), file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
