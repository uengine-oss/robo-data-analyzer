"""Artifact classification safety-net contracts."""
from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from pipeline.execution.artifact_writer import dump_artifact, dump_state_artifacts
from pipeline.execution import run_executor


class ArtifactWriterContractTest(unittest.TestCase):
    def test_unknown_and_nonfinite_values_do_not_replace_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = SimpleNamespace(run_dir=Path(directory))
            artifact_path = Path(directory) / "artifacts" / "strict.json"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text('{"preserved": true}', encoding="utf-8")

            with self.assertRaisesRegex(TypeError, "Unsupported artifact value"):
                dump_artifact(run, "strict", object())  # type: ignore[arg-type]
            self.assertEqual('{"preserved": true}', artifact_path.read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "finite"):
                dump_artifact(run, "strict", {"score": float("nan")})  # type: ignore[arg-type]
            self.assertEqual('{"preserved": true}', artifact_path.read_text(encoding="utf-8"))

    def test_set_serialization_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = SimpleNamespace(run_dir=Path(directory))
            dump_artifact(run, "set-values", {"values": {"z", "a", "m"}})  # type: ignore[arg-type]
            payload = json.loads(
                (Path(directory) / "artifacts" / "set-values.json").read_text(encoding="utf-8")
            )
            self.assertEqual(["a", "m", "z"], payload["values"])

    def test_unmapped_state_is_preserved_in_graph_extras(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = SimpleNamespace(run_dir=Path(directory))

            dump_state_artifacts(run, {"new_unmapped_metric": {"count": 3}})  # type: ignore[arg-type]

            extras_path = Path(directory) / "artifacts" / "graph_extras.json"
            extras = json.loads(extras_path.read_text(encoding="utf-8"))
            self.assertEqual({"count": 3}, extras["new_unmapped_metric"])
            self.assertTrue((Path(directory) / "artifacts" / "analysis.json").is_file())


class FailedRunArtifactContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_latest_checkpoint_state_can_be_preserved_after_pipeline_failure(self) -> None:
        class App:
            async def aget_state(self, _config):
                return SimpleNamespace(values={"new_unmapped_metric": {"count": 7}})

        with tempfile.TemporaryDirectory() as directory:
            run = SimpleNamespace(run_dir=Path(directory))
            await run_executor._dump_checkpoint_artifacts(App(), {}, run)  # type: ignore[attr-defined]

            extras = json.loads(
                (Path(directory) / "artifacts" / "graph_extras.json").read_text(encoding="utf-8")
            )
            self.assertEqual({"count": 7}, extras["new_unmapped_metric"])


if __name__ == "__main__":
    unittest.main()
