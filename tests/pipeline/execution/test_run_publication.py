from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipeline.execution import run_executor, run_publication


class _Stream:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


class PublishedRunSealTest(unittest.TestCase):
    def _run_case(self, failed_stage: str) -> tuple[dict, _Stream, Exception]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        run_dir = Path(temporary.name)
        (run_dir / "manifest.json").write_text(
            json.dumps({"status": "building"}), encoding="utf-8",
        )
        run = SimpleNamespace(run_id="run-1", run_dir=run_dir)
        stream = _Stream()
        closure = {
            "graph_sha256": "a" * 64,
            "node_count": 3,
            "relationship_count": 2,
        }
        update_manifest = run_publication.update_run_manifest

        receipt_patch = patch.object(run_publication, "write_product_graph_receipt")
        performance_patch = patch.object(
            run_publication, "write_performance_artifact",
            return_value="b" * 64,
        )
        manifest_patch = patch.object(
            run_publication,
            "update_run_manifest",
            side_effect=update_manifest,
        )
        with receipt_patch as receipt, performance_patch as performance, manifest_patch as manifest:
            if failed_stage == "product_graph_receipt":
                receipt.side_effect = OSError(f"{failed_stage} failed")
            elif failed_stage == "postcommit_performance":
                performance.side_effect = OSError(f"{failed_stage} failed")
            else:
                def fail_terminal_manifest(*args, **kwargs):
                    if kwargs.get("status") != "published_unsealed":
                        raise OSError(f"{failed_stage} failed")
                    return update_manifest(*args, **kwargs)

                manifest.side_effect = fail_terminal_manifest
            with self.assertRaises(run_publication.PublishedRunUnsealed) as raised:
                run_publication.seal_published_run(
                    run=run,
                    graph_closure=closure,
                    prepublish_performance_artifact_hash="c" * 64,
                    publish_transaction_wall_time_ms=7,
                    run_started_at=0.0,
                    quality_status="completed",
                    stream=stream,
                )

        return (
            json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")),
            stream,
            raised.exception,
        )

    def test_each_postcommit_failure_is_published_unsealed_not_complete(self) -> None:
        for stage in (
            "product_graph_receipt",
            "postcommit_performance",
            "terminal_manifest",
        ):
            with self.subTest(stage=stage):
                manifest, stream, error = self._run_case(stage)
                self.assertEqual("published_unsealed", manifest["status"])
                self.assertEqual("a" * 64, manifest["product_graph"]["sha256"])
                self.assertEqual(stage, error.stage)
                self.assertEqual(
                    ["published_unsealed"],
                    [
                        event["event"] for event in stream.events
                        if event.get("type") == "phase_event"
                    ],
                )

    def test_stop_classification_does_not_consult_mutable_pause_state(self) -> None:
        source = Path(run_executor.__file__).read_text(encoding="utf-8")
        workflow_boundary = source[
            source.index("except RunStopped:"):source.index("if stopped:")
        ]
        self.assertIn("except Exception:", workflow_boundary)
        self.assertNotIn('pause.status()["isStopped"]', workflow_boundary)


if __name__ == "__main__":
    unittest.main()
