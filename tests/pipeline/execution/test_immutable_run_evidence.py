from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm import tracing
from pipeline.execution.run_executor import (
    _reset_run_output_root,
    _write_initial_manifest,
)
from pipeline.execution.sealed_analysis_inputs import SealedAnalysisInputs
from pipeline.execution.run_output_context import (
    get_response_locale,
    get_run_dir,
    reset_response_locale,
    reset_run_dir,
    set_response_locale,
    set_run_dir,
)
from pipeline.models.analysis_meta import AnalysisRun, Codebase


class ImmutableRunEvidenceTest(unittest.TestCase):
    def test_run_owned_output_and_locale_have_no_process_fallback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "run output context"):
            get_run_dir()
        with self.assertRaisesRegex(RuntimeError, "response locale context"):
            get_response_locale()

        run_token = set_run_dir(Path("run-owned"))
        locale_token = set_response_locale("fr")
        try:
            self.assertEqual(Path("run-owned"), get_run_dir())
            self.assertEqual("fr", get_response_locale())
        finally:
            reset_response_locale(locale_token)
            reset_run_dir(run_token)

    def test_prompt_record_retries_transient_windows_replace_denial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "record.json"
            real_replace = tracing.os.replace
            attempts = 0

            def transient_denial(source: Path, destination: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError(5, "transient sharing violation")
                real_replace(source, destination)

            with (
                patch.object(tracing.os, "replace", side_effect=transient_denial),
                patch.object(tracing.time, "sleep"),
            ):
                tracing._write_json(target, {"records": [{"target_id": "code:sample"}]})

            self.assertEqual(2, attempts)
            self.assertEqual(
                "code:sample",
                json.loads(target.read_text(encoding="utf-8"))["records"][0]["target_id"],
            )
            self.assertEqual([], list(target.parent.glob(f".{target.name}.*.tmp")))

    def test_concurrent_prompt_records_are_complete_and_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch.object(tracing, "_prompts_dir", return_value=root / "llm-prompts"):
                def write(index: int) -> None:
                    tracing.write_rule_record(
                        rule_name="parallel_contract",
                        target_id=f"code:{index}",
                        system_prompt="system",
                        user_content=f"user {index}",
                        response={"summary": f"result {index}"},
                        response_schema={
                            "type": "object",
                            "properties": {"summary": {"type": "string"}},
                            "required": ["summary"],
                        },
                        sort_key=index,
                    )

                with ThreadPoolExecutor(max_workers=16) as executor:
                    list(executor.map(write, range(64)))

            prompt_log = root / "llm-prompts" / "parallel_contract.jsonl"
            records = tracing.load_rule_records(prompt_log)
            self.assertEqual(64, len(records))
            self.assertEqual({f"code:{index}" for index in range(64)}, {
                record["target_id"] for record in records
            })
            self.assertEqual(list(range(64)), [record["sort_key"] for record in records])
            self.assertEqual(
                {"summary"},
                set(records[0]["response_schema"]["schema"]["properties"]),
            )
            self.assertRegex(records[0]["response_schema"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(records[0]["response_schema"]["bytes"], 0)

    def test_prompt_append_does_not_read_or_rewrite_prior_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "llm-prompts"
            old_loader = getattr(tracing, "_load_existing", lambda _path: None)
            with (
                patch.object(tracing, "_prompts_dir", return_value=root),
                patch.object(
                    tracing, "_load_existing", wraps=old_loader, create=True,
                ) as old_load,
            ):
                for index in range(3):
                    tracing.write_rule_record(
                        "append_contract", f"code:{index}", "system", "user",
                        {"summary": "result"}, sort_key=index,
                    )
            old_load.assert_not_called()
            self.assertEqual(3, len(tracing.load_rule_records(
                root / "append_contract.jsonl"
            )))

    def test_corrupt_prompt_line_fails_with_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "broken.jsonl"
            path.write_text('{"target_id":"ok"}\n{"target_id":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"broken\.jsonl:2"):
                tracing.load_rule_records(path)

    def test_structured_raw_response_keeps_tool_call_arguments(self) -> None:
        response = SimpleNamespace(
            type="ai",
            content="",
            tool_calls=[
                {
                    "name": "SummaryOutput",
                    "args": {
                        "summary": "Declares an interface.",
                    },
                }
            ],
        )
        serialized = tracing.serialize_response(response)
        self.assertEqual("", serialized["content"])
        self.assertEqual(
            "Declares an interface.",
            serialized["tool_calls"][0]["args"]["summary"],
        )

    def test_manifest_and_prompt_record_share_selected_flat_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "runs"
            corpus = Path(temporary_directory) / "corpus"
            source = corpus / "source"
            source.mkdir(parents=True)
            codebase = Codebase(
                root=source,
                corpus_root=corpus,
                strategy="framework",
                locale="en-US",
            )
            run = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, run.run_dir)
            token = set_run_dir(run.run_dir)
            try:
                _write_initial_manifest(
                    run,
                    SealedAnalysisInputs(
                        payload={"schema_version": "sealed-analysis-inputs/v1"},
                        sha256="a" * 64,
                    ),
                )
                tracing.write_rule_record(
                    rule_name="generic_contract",
                    target_id="code:sample",
                    system_prompt="system",
                    user_content="user",
                    response={"summary": "result"},
                )
            finally:
                reset_run_dir(token)

            manifest = json.loads((run.run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(run.run_id, manifest["run_id"])
            self.assertEqual(run.analysis_scope_id, manifest["analysis_scope_id"])
            self.assertEqual(str(corpus.resolve()), manifest["corpus_root"])
            self.assertEqual("en-US", manifest["locale"])
            self.assertEqual("a" * 64, manifest["sealed_analysis_inputs"]["sha256"])
            self.assertEqual(
                str((run.run_dir / "sealed-analysis-inputs.json").resolve()),
                manifest["sealed_analysis_inputs"]["ref"],
            )
            self.assertNotIn("prompt_tree_sha256", manifest)
            self.assertNotIn("human_glossary", manifest)
            # run 산출물은 루트가 아니라 root/<run_id>/ 에 격리된다 (2026-08-07 개편).
            self.assertEqual(root / run.run_id, run.run_dir)
            prompt_log = run.run_dir / "llm-prompts" / "generic_contract.jsonl"
            self.assertEqual(
                "code:sample", tracing.load_rule_records(prompt_log)[0]["target_id"]
            )

    def test_previous_run_survives_and_retention_prunes_oldest(self) -> None:
        """새 run 은 직전 run 의 증거를 지우지 않는다 — 보존 개수 초과분만 정리한다."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "runs"
            corpus = Path(temporary_directory) / "corpus"
            (corpus / "source").mkdir(parents=True)
            codebase = Codebase(
                root=corpus / "source", corpus_root=corpus,
                strategy="framework", locale="ko",
            )

            first = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, first.run_dir)
            evidence = first.run_dir / "llm-prompts" / "rules.jsonl"
            evidence.write_text('{"target_id": "code:first"}\n', encoding="utf-8")

            second = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, second.run_dir)
            self.assertTrue(
                evidence.exists(),
                "직전 run 의 record 가 새 run 시작으로 지워지면 전후 비교가 불가능하다",
            )

            third = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, third.run_dir)  # 기본 보존 2 → 첫 run 정리
            self.assertFalse(first.run_dir.exists(), "보존 개수 초과분은 정리된다")
            self.assertTrue(second.run_dir.exists())
            self.assertTrue(third.run_dir.exists())

    def test_retention_counts_per_strategy(self) -> None:
        """다른 전략의 기준 run 은 이 전략의 재실행에 밀려나지 않는다.

        보존 정책의 선언된 목적이 "직전 run 이 남아야 백업 없이 전후 비교가 성립한다"
        인 이상, 비교 상대는 **같은 전략의 직전 run** 이다. 종전에는 전략을 구분하지 않고
        세어 2026-08-08 에 framework 판정 기준 run 이 DBMS run 두 번에 밀려 삭제됐고
        저장 프롬프트(제한 replay 재료, 04 §5-1)를 잃었다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "runs"
            corpus = Path(temporary_directory) / "corpus"
            (corpus / "source").mkdir(parents=True)
            codebase = Codebase(
                root=corpus / "source", corpus_root=corpus,
                strategy="framework", locale="ko",
            )
            framework = SimpleNamespace(name="framework")
            dbms = SimpleNamespace(name="dbms")

            baseline = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, baseline.run_dir, framework)
            baseline.run_dir.mkdir(parents=True, exist_ok=True)
            (baseline.run_dir / "manifest.json").write_text(
                '{"strategy": "framework"}', encoding="utf-8",
            )

            kept = []
            for _ in range(3):
                run = AnalysisRun.now(codebase, root)
                _reset_run_output_root(root, run.run_dir, dbms)
                run.run_dir.mkdir(parents=True, exist_ok=True)
                (run.run_dir / "manifest.json").write_text(
                    '{"strategy": "dbms"}', encoding="utf-8",
                )
                kept.append(run.run_dir)

            self.assertTrue(
                baseline.run_dir.exists(),
                "다른 전략의 기준 run 이 삭제되면 그 전략의 전후 비교 재료를 잃는다",
            )
            self.assertFalse(kept[0].exists(), "같은 전략 안에서는 보존 개수를 지킨다")
            self.assertTrue(kept[1].exists())
            self.assertTrue(kept[2].exists())

    def test_failed_run_with_provider_response_survives_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "runs"
            corpus = Path(temporary_directory) / "corpus"
            (corpus / "source").mkdir(parents=True)
            codebase = Codebase(
                root=corpus / "source", corpus_root=corpus,
                strategy="framework", locale="ko",
            )
            framework = SimpleNamespace(name="framework")

            paid_failure = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, paid_failure.run_dir, framework)
            (paid_failure.run_dir / "manifest.json").write_text(
                '{"strategy":"framework","status":"failed"}', encoding="utf-8",
            )
            (paid_failure.run_dir / "usage_summary.json").write_text(
                json.dumps({"total": {
                    "calls": 1,
                    "response_cache_hits": 0,
                    "input_tokens": 10,
                    "output_tokens": 2,
                }}),
                encoding="utf-8",
            )

            ordinary = []
            for _ in range(3):
                run = AnalysisRun.now(codebase, root)
                _reset_run_output_root(root, run.run_dir, framework)
                (run.run_dir / "manifest.json").write_text(
                    '{"strategy":"framework","status":"completed"}',
                    encoding="utf-8",
                )
                ordinary.append(run.run_dir)

            self.assertTrue(paid_failure.run_dir.exists())
            self.assertFalse(ordinary[0].exists())
            self.assertTrue(ordinary[1].exists())
            self.assertTrue(ordinary[2].exists())

    def test_legacy_flat_artifacts_are_cleared_but_run_dirs_kept(self) -> None:
        """개편 전 평면 산출물은 소유 표식 아래에서만 제거되고 run 폴더는 보존된다."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "runs"
            corpus = Path(temporary_directory) / "corpus"
            (corpus / "source").mkdir(parents=True)
            codebase = Codebase(
                root=corpus / "source", corpus_root=corpus,
                strategy="framework", locale="ko",
            )
            root.mkdir(parents=True)
            (root / ".robo-data-analyzer-output").write_text(
                "robo-data-analyzer\n", encoding="utf-8"
            )
            (root / "analyzer.log").write_text("legacy", encoding="utf-8")
            (root / "llm-prompts").mkdir()

            run = AnalysisRun.now(codebase, root)
            _reset_run_output_root(root, run.run_dir)
            self.assertFalse((root / "analyzer.log").exists())
            self.assertFalse((root / "llm-prompts").exists())
            self.assertTrue(run.run_dir.is_dir())

    def test_unowned_nonempty_root_is_refused(self) -> None:
        """소유 표식 없는 비어 있지 않은 루트는 어떤 정리도 하지 않고 실패한다."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "runs"
            corpus = Path(temporary_directory) / "corpus"
            (corpus / "source").mkdir(parents=True)
            codebase = Codebase(
                root=corpus / "source", corpus_root=corpus,
                strategy="framework", locale="ko",
            )
            root.mkdir(parents=True)
            (root / "user-file.txt").write_text("keep me", encoding="utf-8")

            run = AnalysisRun.now(codebase, root)
            with self.assertRaises(ValueError):
                _reset_run_output_root(root, run.run_dir)
            self.assertTrue((root / "user-file.txt").exists())


if __name__ == "__main__":
    unittest.main()
