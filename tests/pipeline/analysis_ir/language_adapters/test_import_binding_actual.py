from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from pipeline.models.import_resolution import summarize_import_resolution_ledger
from pipeline.steps.step2_load_ast.dispatch import read_antlr_json
from pipeline.analysis_ir.language_adapters.import_binding import (
    build_import_binding_environment,
    build_import_resolution_ledger,
)
from pipeline.analysis_ir.parser_facts.source_ast_closure import verify_source_ast_closure
from shared.files.cache import SourceFileCache


_REPO_ROOT = Path(__file__).resolve().parents[4]
_RUN_ROOT = (
    _REPO_ROOT.parents[1] / "_playground" / "analyzer" / "runs"
    / "framework" / "skt-1503-gpt54-mini" / "output"
    / "20260831_063541_470a779494a7"
)


@pytest.mark.skipif(
    not (_RUN_ROOT / "manifest.json").is_file(),
    reason="sealed SKT two-file run is not present",
)
def test_sealed_two_file_imports_close_as_external_exactly_once() -> None:
    manifest = json.loads((_RUN_ROOT / "manifest.json").read_text(encoding="utf-8"))
    source_root = Path(manifest["source_root"])
    corpus_root = Path(manifest["corpus_root"])
    if not source_root.is_dir() or not (corpus_root / "analysis").is_dir():
        pytest.skip("sealed source/AST corpus is not present")

    cache = SourceFileCache()
    closure = verify_source_ast_closure(
        project_root=source_root,
        ast_root=corpus_root / "analysis",
        source_root=source_root,
        source_cache=cache,
        selected_source_ids=tuple(manifest["selected_source_ids"]),
    )
    imports: list[dict] = []
    sources: list[dict] = []
    modules = []
    for artifact in closure.artifacts:
        result = read_antlr_json(
            artifact.ast_path,
            source_root,
            expected_sha256=artifact.ast_sha256,
            source_language=artifact.language,
            source_cache=cache,
            sealed_ast_document=artifact.ast_document,
        )
        imports.extend(result.import_refs)
        sources.extend(result.evidence_sources)
        modules.extend(result.modules)

    environment = build_import_binding_environment(sources, modules)
    first = build_import_resolution_ledger(imports, environment=environment)
    second = build_import_resolution_ledger(imports, environment=environment)

    assert first == second
    assert len(first) == len({row["occurrence_id"] for row in first}) == 25
    assert Counter(row["outcome"] for row in first) == Counter(external=25)
    assert Counter(row["reason"] for row in first) == Counter({
        "target_outside_selected_source_closure": 25,
    })
    assert all(not row["internal_module_ids"] for row in first)
    assert summarize_import_resolution_ledger(first) == {
        "total": 25,
        "unresolved": 0,
        "by_outcome": {"external": 25},
        "by_reason": {"target_outside_selected_source_closure": 25},
        "sha256": "7880cbf52b3c1f7a540a69ea46f1d217a1236757c1d43bc6ea01068ec846618e",
    }
