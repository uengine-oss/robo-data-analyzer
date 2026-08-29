from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]

CURRENT_RUNTIME_DIRECTORIES = {
    "agents",
    "api",
    "graph",
    "integrations",
    "llm",
    "pipeline",
    "search",
    "shared",
    "scripts",
}

CURRENT_SPEC_FILES = {
    "README.md",
    "CURRENT_STATUS.md",
    "plan.md",
    "spec.md",
    "tasks.md",
    "contracts/analyzer-meaning-and-publication-v2.md",
    "contracts/parser-structural-facts-v2.md",
}

REMOVED_IMPLEMENTATIONS = {
    "pipeline/steps/step5_linking/link_calls/call_linker.py",
    "pipeline/steps/step5_linking/link_calls/link_calls_by_ast_step.py",
    "pipeline/steps/step5_linking/link_calls/link_calls_by_name_step.py",
    "pipeline/steps/step7_analyze_code/batch_prompt_values.py",
    "pipeline/steps/step7_analyze_code/range_body.py",
    "pipeline/steps/step8_post_processing/build_glossary_step.py",
    "pipeline/steps/step8_post_processing/embed_step.py",
    "pipeline/steps/step8_post_processing/health_metrics_step.py",
    "scripts/reembed_graph.py",
    "scripts/run_strategy_a_progressive.py",
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
