"""Model runtime capability artifact is complete, content-addressed, and read once."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from llm.model_runtime_capabilities import (
    ModelRuntimeCapabilitiesError,
    load_model_runtime_capabilities,
)


def _document() -> dict:
    return {
        "sealed_model_runtime_capabilities": {
            "runtime_kind": "self_hosted",
            "provider_id": "internal",
            "model_id": "frentis-ai-model",
            "model_revision": "weights-sha256:abc",
            "tokenizer_id": "tokenizer-family",
            "tokenizer_revision": "tokenizer-sha256:def",
            "quantization": "bf16",
            "serving_runtime": {
                "runtime_id": "sglang",
                "runtime_version": "0.5.0",
                "runtime_config_sha256": "1" * 64,
            },
            "provider_hard_limits": {
                "context_tokens": 131072,
                "max_output_tokens": 30000,
            },
            "gpu_runtime_profile": {
                "gpu_model": "NVIDIA-test",
                "gpu_count": 2,
                "memory_bytes_per_gpu": 80 * 1024**3,
                "driver_runtime_version": "driver-runtime-1",
                "profile_version": "admission-profile-1",
            },
        }
    }


class ModelRuntimeCapabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        load_model_runtime_capabilities.cache_clear()

    def _write(self, root: Path, document: dict | None = None) -> tuple[Path, str]:
        path = root / "model-runtime-capabilities.json"
        path.write_text(
            json.dumps(document or _document(), ensure_ascii=False), encoding="utf-8",
        )
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_complete_content_addressed_artifact_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, sha256 = self._write(Path(temporary))
            loaded = load_model_runtime_capabilities(str(path), sha256)
        self.assertEqual("frentis-ai-model", loaded.model_id)
        self.assertEqual("self_hosted", loaded.runtime_kind)
        self.assertEqual(131072, loaded.context_tokens)
        self.assertEqual(30000, loaded.max_output_tokens)
        self.assertEqual(sha256, loaded.artifact_sha256)

    def test_missing_required_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = _document()
            del document["sealed_model_runtime_capabilities"]["tokenizer_revision"]
            path, sha256 = self._write(Path(temporary), document)
            with self.assertRaisesRegex(
                ModelRuntimeCapabilitiesError, "tokenizer_revision",
            ):
                load_model_runtime_capabilities(str(path), sha256)

    def test_provider_managed_runtime_does_not_invent_gpu_or_tokenizer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = {
                "sealed_model_runtime_capabilities": {
                    "runtime_kind": "provider_managed",
                    "provider_id": "openai",
                    "model_id": "gpt-5.4-mini-2026-03-17",
                    "model_revision": "gpt-5.4-mini-2026-03-17",
                    "serving_runtime": {
                        "runtime_id": "openai-api",
                        "runtime_version": "v1",
                        "runtime_config_sha256": "2" * 64,
                    },
                    "provider_hard_limits": {
                        "context_tokens": 400000,
                        "max_output_tokens": 128000,
                    },
                    "profile_version": "openai-model-page-2026-08-29",
                    "provider_managed_disclosure": {
                        "tokenizer_identity": "provider_managed_undisclosed",
                        "quantization": "provider_managed_undisclosed",
                        "infrastructure": "provider_managed_undisclosed",
                    },
                }
            }
            path, sha256 = self._write(Path(temporary), document)
            loaded = load_model_runtime_capabilities(str(path), sha256)
        self.assertEqual("provider_managed", loaded.runtime_kind)
        self.assertIsNone(loaded.gpu_model)
        self.assertIsNone(loaded.tokenizer_id)
        self.assertEqual(
            "provider_managed_undisclosed",
            loaded.infrastructure_disclosure,
        )

    def test_provider_managed_runtime_rejects_fake_disclosure_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = {
                "sealed_model_runtime_capabilities": {
                    "runtime_kind": "provider_managed",
                    "provider_id": "openai",
                    "model_id": "snapshot",
                    "model_revision": "snapshot",
                    "serving_runtime": {
                        "runtime_id": "openai-api",
                        "runtime_version": "v1",
                        "runtime_config_sha256": "2" * 64,
                    },
                    "provider_hard_limits": {
                        "context_tokens": 1,
                        "max_output_tokens": 1,
                    },
                    "profile_version": "test",
                    "provider_managed_disclosure": {
                        "tokenizer_identity": "guessed",
                        "quantization": "provider_managed_undisclosed",
                        "infrastructure": "provider_managed_undisclosed",
                    },
                }
            }
            path, sha256 = self._write(Path(temporary), document)
            with self.assertRaisesRegex(
                ModelRuntimeCapabilitiesError,
                "tokenizer_identity must be provider_managed_undisclosed",
            ):
                load_model_runtime_capabilities(str(path), sha256)

    def test_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, _sha256 = self._write(Path(temporary))
            with self.assertRaisesRegex(ModelRuntimeCapabilitiesError, "mismatch"):
                load_model_runtime_capabilities(str(path), "f" * 64)

    def test_same_ref_and_hash_are_read_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, sha256 = self._write(Path(temporary))
            first = load_model_runtime_capabilities(str(path), sha256)
            path.unlink()
            second = load_model_runtime_capabilities(str(path), sha256)
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
