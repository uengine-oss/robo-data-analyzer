"""Bind canonical Parser import entries against an explicit selected-source environment."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from graph.schema.graph_nodes import Module
from graph.schema.node_ids import code_file_id
from pipeline.languages import lang_of
from pipeline.models.import_resolution import (
    ImportResolutionOccurrence,
    import_occurrence_id,
    normalize_import_resolution_ledger,
)


_STRUCTURED_VERSIONS = frozenset({"1.1.0", "1.2.0", "2.0.0", "2.1.0"})


@dataclass(frozen=True)
class ImportBindingSource:
    source_id: str
    language: str


@dataclass(frozen=True)
class ImportBindingEnvironment:
    """Closed run-local inputs; absence of this object means no binding authority."""

    selected_sources: tuple[ImportBindingSource, ...]
    module_ids_by_source: tuple[tuple[str, tuple[str, ...]], ...]
    c_include_roots: tuple[str, ...] = ()

    def language_by_source(self) -> dict[str, str]:
        return {item.source_id: item.language for item in self.selected_sources}

    def modules_by_source(self) -> dict[str, tuple[str, ...]]:
        return dict(self.module_ids_by_source)


def _relative_path(value: object, *, field: str, allow_root: bool = False) -> str:
    raw = str(value or "")
    if raw in {"", "."} and allow_root:
        return ""
    if (
        not raw or "\x00" in raw or "\\" in raw or raw.startswith("/")
        or (len(raw) >= 2 and raw[1] == ":")
    ):
        raise ValueError(f"{field} must be a canonical relative POSIX path")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or path.as_posix() != raw
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field} must be a canonical relative POSIX path")
    return path.as_posix()


def build_import_binding_environment(
    evidence_sources: Sequence[Mapping[str, Any]],
    modules: Sequence[Module],
    *,
    c_include_roots: Sequence[str] = (),
) -> ImportBindingEnvironment:
    """Seal exact selected source/language/module identities for import binding."""
    selected: list[ImportBindingSource] = []
    seen_sources: set[str] = set()
    for source in evidence_sources:
        source_id = _relative_path(source.get("source_id"), field="source_id")
        language = str(source.get("language") or "").strip().lower()
        if not language:
            language = lang_of(PurePosixPath(source_id).suffix)
        if source_id in seen_sources:
            raise ValueError(f"duplicate selected import source: {source_id}")
        seen_sources.add(source_id)
        selected.append(ImportBindingSource(source_id=source_id, language=language))

    module_ids = [str(module.id or "") for module in modules]
    if any(not module_id for module_id in module_ids):
        raise ValueError("blank module ID in import binding environment")
    if len(module_ids) != len(set(module_ids)):
        raise ValueError("duplicate module ID in import binding environment")
    available_module_ids = set(module_ids)
    sealed_modules: list[tuple[str, tuple[str, ...]]] = []
    for source in selected:
        expected = code_file_id(source.source_id)
        projected = (
            (expected,)
            if source.language == "c" and expected in available_module_ids
            else ()
        )
        sealed_modules.append((source.source_id, projected))

    roots = tuple(
        _relative_path(root, field="c_include_root", allow_root=True)
        for root in c_include_roots
    )
    if len(roots) != len(set(roots)):
        raise ValueError("C include roots must be unique and ordered")
    return ImportBindingEnvironment(
        selected_sources=tuple(selected),
        module_ids_by_source=tuple(sealed_modules),
        c_include_roots=roots,
    )


def _base_outcome(
    ref: Mapping[str, Any], entry: Mapping[str, Any],
) -> tuple[str, str] | None:
    if str(ref.get("evidence_version") or "") not in _STRUCTURED_VERSIONS:
        return "unresolved", "insufficient_structured_import_evidence"
    if str(ref.get("evidence_status") or "") != "complete":
        return "unresolved", "insufficient_import_evidence_completeness"
    presence = str(ref.get("presence_status") or "")
    if presence == "inactive":
        return "excluded", "inactive_import"
    if presence != "active":
        return "unresolved", "insufficient_import_presence"
    if entry.get("import_kind") == "computed":
        return "unresolved", "insufficient_computed_import_target"
    return None


def _join(root: str, target: str) -> str:
    return (PurePosixPath(root) / target).as_posix() if root else target


def _c_target(entry: Mapping[str, Any]) -> str:
    if entry.get("import_kind") != "source_file":
        raise ValueError("C import entry is not a source-file target")
    target_kind = str(entry.get("target_kind") or "")
    components = entry.get("path_components")
    if not isinstance(components, (list, tuple)) or len(components) != 1:
        raise ValueError("C source-file import requires one path component")
    target = _relative_path(components[0], field="C import target")
    raw_target = str(entry.get("target") or "")
    expected = f'"{target}"' if target_kind == "quoted" else f"<{target}>"
    if target_kind not in {"quoted", "angle"} or raw_target != expected:
        raise ValueError("C import target syntax differs from Parser path component")
    return target


def _bind_c(
    ref: Mapping[str, Any],
    entry: Mapping[str, Any],
    environment: ImportBindingEnvironment,
) -> tuple[str, str, tuple[str, ...]]:
    try:
        target = _c_target(entry)
    except ValueError:
        return "unresolved", "invalid_c_import_target", ()

    source_id = str(ref.get("source_id") or "")
    selected_ids = {item.source_id for item in environment.selected_sources}
    exact_candidates: list[str] = []
    if entry.get("target_kind") == "quoted":
        parent = PurePosixPath(source_id).parent.as_posix()
        exact_candidates.append(_join("" if parent == "." else parent, target))
    exact_candidates.extend(_join(root, target) for root in environment.c_include_roots)

    selected_target = next(
        (candidate for candidate in exact_candidates if candidate in selected_ids),
        None,
    )
    if selected_target is not None:
        module_ids = environment.modules_by_source().get(selected_target, ())
        expected_module_id = code_file_id(selected_target)
        if module_ids != (expected_module_id,):
            return "unresolved", "invalid_c_file_module_projection", ()
        return "internal", "", module_ids

    target_parts = PurePosixPath(target).parts
    suffix_candidates = [
        candidate for candidate in selected_ids
        if PurePosixPath(candidate).parts[-len(target_parts):] == target_parts
    ]
    if suffix_candidates:
        return "unresolved", "insufficient_c_include_search_environment", ()
    return "external", "target_outside_selected_source_closure", ()


def _environment_outcome(
    ref: Mapping[str, Any],
    entry: Mapping[str, Any],
    environment: ImportBindingEnvironment | None,
) -> tuple[str, str, tuple[str, ...]]:
    base = _base_outcome(ref, entry)
    if base is not None:
        return base[0], base[1], ()
    if environment is None:
        return "unresolved", "insufficient_import_binding_environment", ()
    source_id = str(ref.get("source_id") or "")
    language = environment.language_by_source().get(source_id)
    if language is None:
        return "unresolved", "source_outside_import_binding_environment", ()
    if language == "c":
        return _bind_c(ref, entry, environment)
    return "unresolved", "unsupported_import_binding_language", ()


def build_import_resolution_ledger(
    import_refs: Sequence[Mapping[str, Any]],
    *,
    environment: ImportBindingEnvironment | None = None,
) -> list[dict[str, Any]]:
    """Produce one source-ordered terminal outcome per canonical binding entry."""
    rows: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()
    for ref in import_refs:
        import_id = str(ref.get("import_id") or "")
        if not import_id:
            continue
        if import_id in seen_fact_ids:
            raise ValueError(f"duplicate canonical import fact: {import_id}")
        seen_fact_ids.add(import_id)
        entries = ref.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"canonical import fact has no binding entries: {import_id}")
        for expected_entry_ordinal, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ValueError(f"import binding entry must be an object: {import_id}")
            if entry.get("entry_ordinal") != expected_entry_ordinal:
                raise ValueError(
                    f"import binding entry order mismatch: {import_id}:"
                    f"{entry.get('entry_ordinal')}!={expected_entry_ordinal}"
                )
            outcome, reason, module_ids = _environment_outcome(
                ref, entry, environment,
            )
            row = {
                "occurrence_id": import_occurrence_id(
                    import_id, expected_entry_ordinal,
                ),
                "import_id": import_id,
                "ordinal": len(rows),
                "entry_ordinal": expected_entry_ordinal,
                "evidence_version": ref.get("evidence_version"),
                "source_id": ref.get("source_id"),
                "line": ref.get("line"),
                "end_line": ref.get("end_line"),
                "range": ref.get("range"),
                "directive_kind": ref.get("directive_kind"),
                "grammar_rule": ref.get("grammar_rule"),
                "presence_status": ref.get("presence_status"),
                "evidence_status": ref.get("evidence_status"),
                "import_kind": entry.get("import_kind"),
                "target_kind": entry.get("target_kind"),
                "target": entry.get("target"),
                "target_range": entry.get("target_range"),
                "path_components": list(entry.get("path_components") or []),
                "path_component_ranges": list(
                    entry.get("path_component_ranges") or []
                ),
                "member": entry.get("member"),
                "member_range": entry.get("member_range"),
                "alias": entry.get("alias"),
                "alias_range": entry.get("alias_range"),
                "relative_level": entry.get("relative_level"),
                "wildcard": entry.get("wildcard"),
                "locality": entry.get("locality"),
                "outcome": outcome,
                "reason": reason,
                "internal_module_ids": list(module_ids),
            }
            rows.append(ImportResolutionOccurrence.model_validate(row).as_dict())
    return normalize_import_resolution_ledger(rows)
