"""spec 061 — runtime event code and generated run output have distinct owners."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.config.settings import (
    AnalysisLlmSettings,
    AnalysisRunOutputSettings,
    EmbeddingSettings,
    RuntimeEventSettings,
    SemanticSearchSettings,
    _getenv_bool,
)
from pipeline.models.analysis_meta import AnalysisRun, Codebase
from pipeline.execution.run_executor import _reset_run_output_root
from shared.events.runtime_logger import _COMPONENT_LABELS, _level_from_name


REPO = Path(__file__).resolve().parents[2]


class RuntimeOutputContractTest(unittest.TestCase):
    def test_explicit_setting_and_analysis_run_use_per_run_directory(self) -> None:
        # 2026-08-07 개편: 루트는 logs 그대로, 산출물은 logs/<run_id>/ 에 격리된다.
        # run 끼리 서로의 증거를 지우지 않아야 전후 비교가 백업 없이 성립한다.
        with patch.dict(os.environ, {"ROBO_RUN_OUTPUT_DIR": "./logs"}, clear=False):
            settings = AnalysisRunOutputSettings()
        self.assertEqual(Path("./logs"), settings.dir)

        run = AnalysisRun.now(
            Codebase(root=REPO, corpus_root=REPO, strategy="framework"), settings.dir
        )
        self.assertEqual(Path("./logs") / run.run_id, run.run_dir)

    def test_default_run_root_is_outside_the_product_repository(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            previous = os.environ.pop("ROBO_RUN_OUTPUT_DIR", None)
            try:
                output = AnalysisRunOutputSettings().dir.resolve()
            finally:
                if previous is not None:
                    os.environ["ROBO_RUN_OUTPUT_DIR"] = previous
        self.assertNotEqual(REPO, output)
        self.assertNotIn(REPO, output.parents)
        self.assertEqual(
            (REPO.parents[1] / "_playground/analyzer/runs/live").resolve(),
            output,
        )

    def test_reset_prepares_run_directory_under_owned_root(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "logs"
            run = AnalysisRun.now(
                Codebase(root=REPO, corpus_root=REPO, strategy="framework"), root
            )
            _reset_run_output_root(root, run.run_dir)
            self.assertTrue((root / ".robo-data-analyzer-output").is_file())
            self.assertTrue((run.run_dir / "llm-prompts").is_dir())

            # 개편 전 평면 산출물은 소유 표식 아래에서 제거된다 — run 폴더는 보존.
            sentinel = root / "stale.json"
            sentinel.write_text("previous-run", encoding="utf-8")
            second = AnalysisRun.now(
                Codebase(root=REPO, corpus_root=REPO, strategy="framework"), root
            )
            _reset_run_output_root(root, second.run_dir)
            self.assertFalse(sentinel.exists())
            self.assertTrue(run.run_dir.is_dir())
            self.assertTrue((second.run_dir / "llm-prompts").is_dir())

    def test_reset_refuses_nonempty_unowned_directory_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "not-analyzer-output"
            root.mkdir()
            sentinel = root / "user-file.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "소유하지 않은"):
                _reset_run_output_root(root)

            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))

    def test_reset_rejects_invalid_marker_and_non_directory_path(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "output"
            root.mkdir()
            marker = root / ".robo-data-analyzer-output"
            marker.write_text("another-owner\n", encoding="utf-8")
            sentinel = root / "result.json"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "표식이 올바르지"):
                _reset_run_output_root(root)
            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))

            file_path = Path(parent) / "output-file"
            file_path.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "디렉터리가 아닙니다"):
                _reset_run_output_root(file_path)
            self.assertEqual("preserve", file_path.read_text(encoding="utf-8"))

    def test_reset_refuses_even_legacy_shaped_tree_without_explicit_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "output"
            (root / "analysis" / "latest").mkdir(parents=True)
            (root / "llm-prompts" / "latest").mkdir(parents=True)
            (root / "search").mkdir()
            sentinel = root / "analysis" / "latest" / "result.json"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "소유하지 않은"):
                _reset_run_output_root(root)

            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))

    def test_invalid_environment_values_fail_closed(self) -> None:
        with patch.dict(os.environ, {"ROBO_LLM_RESPONSE_CACHE": "treu"}, clear=False):
            with self.assertRaisesRegex(ValueError, "ROBO_LLM_RESPONSE_CACHE"):
                _getenv_bool("ROBO_LLM_RESPONSE_CACHE", False)

        with patch.dict(
            os.environ,
            {"ROBO_LLM_MODEL_RUNTIME_CAPABILITIES_SHA256": "not-a-sha256"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                ValueError, "MODEL_RUNTIME_CAPABILITIES_SHA256"
            ):
                AnalysisLlmSettings()

        with self.assertRaisesRegex(ValueError, "dense_impl"):
            SemanticSearchSettings(dense_impl="indx")
        with self.assertRaisesRegex(ValueError, "log_level"):
            RuntimeEventSettings(log_level="VERBOSE")
        with self.assertRaisesRegex(ValueError, "지원하지 않는 로그 레벨"):
            _level_from_name("VERBOSE")
        with self.assertRaisesRegex(ValueError, "concurrency"):
            EmbeddingSettings(concurrency=0)

    def test_run_output_rejects_empty_and_dangerous_paths(self) -> None:
        with patch.dict(os.environ, {"ROBO_RUN_OUTPUT_DIR": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "ROBO_RUN_OUTPUT_DIR"):
                AnalysisRunOutputSettings()

        with self.assertRaisesRegex(ValueError, "작업 디렉터리"):
            AnalysisRunOutputSettings(dir=Path("."))

        anchor = Path.cwd().anchor
        if anchor:
            with self.assertRaisesRegex(ValueError, "파일시스템 루트"):
                AnalysisRunOutputSettings(dir=Path(anchor))

    def test_old_runtime_roots_are_absent(self) -> None:
        # logs 는 현행 정본 루트로 복귀 (사용자 지시 2026-07-22) — 옛 루트는 output/run-output.
        for old_root in ("system", "log", "deploy", "output", "run-output"):
            self.assertFalse((REPO / old_root).exists(), old_root)

    def test_runtime_labels_use_current_pipeline_component_names(self) -> None:
        current = {
            "step1.parse_ddl",
            "step1.discover_datasource",
            "step1.resolve_data_objects",
            "step2.load_ast",
            "step3.load_external",
            "step4.scan",
            "step4.scan_external",
            "step5.link_deterministic",
            "step5.link_dbms_tables",
            "step5.link_parser_calls",
            "step5.link_residual_patterns",
            "step5.link_external",
            "step6.build_context",
            "step7.analyze_code",
            "step7.analyze_code.batch",
            "step7.analyze_code.three_split",
            "step8.build_module_summary_rollups",
            "step8.build_components",
            "step8.enrich_table_description",
            "step8.build_search_segments",
        }
        removed = {
            "step5.link_calls_by_name",
            "step5.link_calls_by_ast",
            "step8.summarize_module",
            "step8.enrich_lineage_from_catalog",
            "step8.embed",
        }

        self.assertTrue(current.issubset(_COMPONENT_LABELS))
        self.assertTrue(removed.isdisjoint(_COMPONENT_LABELS))


if __name__ == "__main__":
    unittest.main()
