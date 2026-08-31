from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]

CURRENT_RUNTIME_DIRECTORIES = {
    "agents",
    "api",
    "cli",
    "graph",
    "integrations",
    "llm",
    "pipeline",
    "search",
    "shared",
    "tools",
}

CURRENT_SPEC_FILES = {
    "README.md",
    "CURRENT_STATUS.md",
    "plan.md",
    "spec.md",
    "tasks.md",
    "contracts/analyzer-meaning-and-publication-v2.md",
    "contracts/catalog-table-discovery-pagination-v1.md",
    "contracts/import-binding-environment-v1.md",
    "contracts/parser-structural-facts-v2.md",
}

REMOVED_IMPLEMENTATIONS = {
    "pipeline/steps/step5_linking/link_calls/call_linker.py",
    "pipeline/steps/step5_linking/link_calls/link_calls_by_ast_step.py",
    "pipeline/steps/step5_linking/link_calls/link_calls_by_name_step.py",
    "pipeline/semantic_tasks/batch_prompt_values.py",
    "pipeline/semantic_tasks/range_body.py",
    "pipeline/steps/step8_post_processing/build_glossary_step.py",
    "pipeline/steps/step8_post_processing/embed_step.py",
    "pipeline/steps/step8_post_processing/health_metrics_step.py",
    "scripts/reembed_graph.py",
    "scripts/run_strategy_a_progressive.py",
    "scripts/analyze_data_dir.py",
    "scripts/audit_coverage.py",
    "scripts/check_byte_identical.py",
    "scripts/export_semantic_review_packets.py",
    "scripts/inventory_semantic_outputs.py",
    "scripts/judge_run.py",
    "scripts/refresh_pricing.py",
    "scripts/reset_neo4j_schema.py",
}


class RepositoryLayoutTest(unittest.TestCase):
    def test_runtime_responsibilities_have_one_current_directory(self) -> None:
        missing = sorted(
            name for name in CURRENT_RUNTIME_DIRECTORIES
            if not (REPO / name).is_dir()
        )
        self.assertEqual([], missing)

    def test_only_current_sdd_files_remain_under_specs(self) -> None:
        root = REPO / "specs" / "131-cross-node-semantic-grounding"
        actual = {
            path.relative_to(root).as_posix()
            for path in (REPO / "specs").rglob("*")
            if path.is_file()
        }
        self.assertEqual(CURRENT_SPEC_FILES, actual)

    def test_removed_implementations_do_not_reappear(self) -> None:
        remaining = sorted(path for path in REMOVED_IMPLEMENTATIONS if (REPO / path).exists())
        self.assertEqual([], remaining)

    def test_legacy_one_shot_catalog_discovery_does_not_reappear(self) -> None:
        transport = (REPO / "integrations" / "catalog.py").read_text(encoding="utf-8")
        self.assertNotIn("get_table_discovery", transport)
        self.assertNotIn("max_tables", transport)

    def test_pipeline_uses_the_approved_responsibility_tree(self) -> None:
        required = {
            "pipeline/analysis_ir/parser_facts",
            "pipeline/analysis_ir/language_adapters",
            "pipeline/analysis_ir/resolution",
            "pipeline/semantic_tasks/summary",
            "pipeline/semantic_tasks/table",
            "pipeline/semantic_tasks/rule",
        }
        self.assertEqual([], sorted(
            path for path in required if not (REPO / path).is_dir()
        ))
        retired_step = REPO / "pipeline/steps/step7_analyze_code"
        self.assertFalse(any(retired_step.glob("*.py")))
        self.assertFalse((REPO / "llm/prompts").exists())

    def test_llm_package_contains_transport_not_semantic_prompt_assets(self) -> None:
        semantic_assets = [
            path for path in (REPO / "llm").rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".yaml", ".yml"}
            and "configs" not in path.parts
            and "runtime_capabilities" not in path.parts
            and "usage" not in path.parts
        ]
        self.assertEqual([], semantic_assets)

    def test_unconsumed_historical_experiment_directory_does_not_reappear(self) -> None:
        self.assertFalse((REPO / "_artifacts").exists())

    def test_default_data_dir_and_prompt_assets_resolve(self) -> None:
        from agents.residual import _RESIDUAL_PROMPTS_DIR, _RESIDUAL_RULE
        from agents.scan import _SYSTEM_PROMPT_PATH
        from shared.config.settings import _DEFAULT_DATA_DIR

        self.assertEqual((REPO.parent / "data").resolve(), _DEFAULT_DATA_DIR.resolve())
        self.assertTrue(_SYSTEM_PROMPT_PATH.is_file())
        self.assertTrue((_RESIDUAL_PROMPTS_DIR / f"{_RESIDUAL_RULE}.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
