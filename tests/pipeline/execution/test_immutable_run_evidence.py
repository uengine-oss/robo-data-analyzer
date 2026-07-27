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
from pipeline.execution.run_output_context import reset_run_dir, set_run_dir
from pipeline.models.analysis_meta import AnalysisRun, Codebase


class ImmutableRunEvidenceTest(unittest.TestCase):
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
                        sort_key=index,
                    )

                with ThreadPoolExecutor(max_workers=16) as executor:
                    list(executor.map(write, range(64)))

            prompt_log = root / "llm-prompts" / "parallel_contract.json"
            records = json.loads(prompt_log.read_text(encoding="utf-8"))["records"]
            self.assertEqual(64, len(records))
            self.assertEqual({f"code:{index}" for index in range(64)}, {
                record["target_id"] for record in records
            })
            self.assertEqual([], list(prompt_log.parent.glob("*.tmp")))

    def test_structured_raw_response_keeps_tool_call_arguments(self) -> None:
        response = SimpleNamespace(
            type="ai",
            content="",
            tool_calls=[
                {
                    "name": "SummaryOutput",
                    "args": {
                        "summary": "Declares an interface.",
                        "search_text": "Declares a bodyless interface signature.",
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
            _reset_run_output_root(root)
            token = set_run_dir(run.run_dir)
            try:
                _write_initial_manifest(
                    run,
                    SimpleNamespace(prompts_dir="llm/prompts/framework"),
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
            self.assertEqual(str(corpus.resolve()), manifest["corpus_root"])
            self.assertEqual("en-US", manifest["locale"])
            self.assertRegex(manifest["prompt_tree_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(root, run.run_dir)
            prompt_log = root / "llm-prompts" / "generic_contract.json"
            self.assertEqual("code:sample", json.loads(
                prompt_log.read_text(encoding="utf-8")
            )["records"][0]["target_id"])


if __name__ == "__main__":
    unittest.main()
