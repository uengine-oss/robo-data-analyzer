"""The run input seal binds every product input family before external mutation."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from llm.model_runtime_capabilities import load_model_runtime_capabilities
from pipeline.execution.sealed_analysis_inputs import (
    SealedAnalysisInputsError,
    build_sealed_analysis_inputs,
    write_sealed_analysis_inputs,
)
from pipeline.models.analysis_meta import Codebase
from shared.files.source_cache import SourceFileCache


REAL_REPOSITORY = Path(__file__).resolve().parents[3]
CONFIG_NAME = "qwen38_sglang_local"


@dataclass(frozen=True)
class _LlmSettings:
    config: str = CONFIG_NAME
    api_key: str = "must-not-enter-seal"


@dataclass(frozen=True)
class _EmbeddingSettings:
    api_key: str = "must-not-enter-seal"
    model: str = "embed-model"


@dataclass(frozen=True)
class _GraphSettings:
    password: str = "must-not-enter-seal"
    database: str = "framework"


@dataclass(frozen=True)
class _Settings:
    analysis_llm: _LlmSettings = _LlmSettings()
    embedding: _EmbeddingSettings = _EmbeddingSettings()
    graph_database: _GraphSettings = _GraphSettings()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative_bytes = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _runtime_document() -> dict:
    return {
        "sealed_model_runtime_capabilities": {
            "runtime_kind": "self_hosted",
            "provider_id": "internal",
            "model_id": "test-model",
            "model_revision": "weights-1",
            "tokenizer_id": "test-tokenizer",
            "tokenizer_revision": "vocab-1",
            "quantization": "bf16",
            "serving_runtime": {
                "runtime_id": "test-runtime",
                "runtime_version": "1.0",
                "runtime_config_sha256": "2" * 64,
            },
            "provider_hard_limits": {
                "context_tokens": 32768,
                "max_output_tokens": 8192,
            },
            "gpu_runtime_profile": {
                "gpu_model": "test-gpu",
                "gpu_count": 1,
                "memory_bytes_per_gpu": 1024,
                "driver_runtime_version": "driver-1",
                "profile_version": "profile-1",
            },
        }
    }


class SealedAnalysisInputsTest(unittest.TestCase):
    def setUp(self) -> None:
        load_model_runtime_capabilities.cache_clear()

    def test_docker_build_context_contains_every_sealed_contract(self) -> None:
        ignored = {
            line.strip().rstrip("/")
            for line in (REAL_REPOSITORY / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertNotIn(".specify", ignored)
        self.assertNotIn("specs", ignored)

    def _repository(
        self, root: Path,
    ) -> tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
        repository = root / "repo"
        for directory in (
            "agents", "api", "graph", "integrations", "llm", "pipeline", "search", "shared",
        ):
            (repository / directory).mkdir(parents=True, exist_ok=True)
            (repository / directory / "owned.py").write_text(
                f"OWNER = {directory!r}\n", encoding="utf-8",
            )
        for file_name in (
            "main.py", "requirements.txt", "requirements.lock", "Dockerfile", "__init__.py",
        ):
            (repository / file_name).write_text(
                f"fixture={file_name}\n", encoding="utf-8",
            )
        config_dir = repository / "llm" / "configs"
        config_dir.mkdir()
        config_dir.joinpath(f"{CONFIG_NAME}.yaml").write_bytes(
            (REAL_REPOSITORY / "llm" / "configs" / f"{CONFIG_NAME}.yaml").read_bytes()
        )
        prompts = repository / "llm" / "prompts" / "framework"
        prompts.mkdir(parents=True)
        (prompts / "summary.yaml").write_text("system: stable\n", encoding="utf-8")
        for relative in (
            ".specify/memory/constitution.md",
            "specs/001-analyzer-redesign/README.md",
            "specs/001-analyzer-redesign/spec.md",
            "specs/001-analyzer-redesign/plan.md",
            "specs/001-analyzer-redesign/tasks.md",
            "specs/001-analyzer-redesign/contracts/parser-contract.md",
            "specs/001-analyzer-redesign/contracts/graph-contract.md",
            "specs/001-analyzer-redesign/contracts/service-contract.md",
        ):
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"contract: {relative}\n", encoding="utf-8")
        project = root / "data" / "source" / "skt-slice"
        project.mkdir(parents=True)
        (project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        (project / "settings.yaml").write_text("feature: enabled\n", encoding="utf-8")
        ddl = root / "data" / "ddl"
        ddl.mkdir(parents=True)
        (ddl / "schema.sql").write_text("create table T(ID int);\n", encoding="utf-8")
        closure = root / "run" / "source-ast-closure.json"
        closure.parent.mkdir()
        closure.write_text('{"status":"exact"}\n', encoding="utf-8")
        runtime = root / "model-runtime.json"
        runtime.write_text(json.dumps(_runtime_document()), encoding="utf-8")
        baseline = root / "baseline.json"
        baseline.write_text('{"expected":2}\n', encoding="utf-8")
        answer_key = root / "answer-key.json"
        answer_key.write_text('{"meaning":"approved"}\n', encoding="utf-8")
        return repository, prompts, project, ddl, closure, runtime, baseline, answer_key

    def test_complete_seal_is_deterministic_and_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, prompts, project, ddl, closure, runtime, baseline, answer_key = (
                self._repository(root)
            )
            model_runtime = load_model_runtime_capabilities(str(runtime), _sha(runtime))
            codebase = Codebase(
                root=project,
                corpus_root=root / "data",
                ddl_dir=ddl,
                strategy="framework",
                validation_baseline_ref=baseline,
                validation_baseline_sha256=_sha(baseline),
                validation_answer_key_ref=answer_key,
                validation_answer_key_sha256=_sha(answer_key),
            )
            kwargs = dict(
                codebase=codebase,
                neo4j_database="framework",
                project_input_paths=tuple(
                    path for path in project.rglob("*") if path.is_file()
                ),
                source_ast_closure_ref=closure,
                source_ast_closure_bytes=closure.read_bytes(),
                source_ast_closure_sha256=_sha(closure),
                strategy_prompts_dir=prompts,
                human_glossary_manifest={"supplied": False},
                source_cache=SourceFileCache(),
                model_runtime=model_runtime,
                settings=_Settings(),
                repository_root=repository,
            )
            first = build_sealed_analysis_inputs(**kwargs)
            second = build_sealed_analysis_inputs(**kwargs)
            target = root / "run" / "sealed-analysis-inputs.json"
            write_sealed_analysis_inputs(target, first)
            with self.assertRaises(FileExistsError):
                write_sealed_analysis_inputs(target, first)

        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(2, first.payload["source_files"]["file_count"])
        self.assertEqual(1, first.payload["ddl_files"]["file_count"])
        self.assertTrue(first.payload["validation_baseline"]["supplied"])
        self.assertTrue(first.payload["validation_answer_key"]["supplied"])
        runtime_values = first.payload["model_runtime_capabilities"]
        self.assertEqual("test-runtime", runtime_values["serving_runtime_id"])
        self.assertEqual("test-gpu", runtime_values["gpu_model"])
        self.assertEqual("driver-1", runtime_values["driver_runtime_version"])
        self.assertEqual("CPython", first.payload["python_environment"]["implementation"])
        self.assertTrue(first.payload["python_environment"]["packages"])
        implementation_files = {
            item["relative_path"]
            for item in first.payload["analyzer_implementation"]["files"]
        }
        self.assertIn("requirements.lock", implementation_files)
        self.assertNotIn("must-not-enter-seal", json.dumps(first.as_dict()))

    def test_changed_answer_key_is_rejected_at_seal_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, prompts, project, ddl, closure, runtime, baseline, answer_key = (
                self._repository(root)
            )
            expected_answer_key = _sha(answer_key)
            answer_key.write_text('{"meaning":"changed"}\n', encoding="utf-8")
            with self.assertRaisesRegex(SealedAnalysisInputsError, "validation_answer_key"):
                build_sealed_analysis_inputs(
                    codebase=Codebase(
                        root=project,
                        corpus_root=root / "data",
                        ddl_dir=ddl,
                        strategy="framework",
                        validation_baseline_ref=baseline,
                        validation_baseline_sha256=_sha(baseline),
                        validation_answer_key_ref=answer_key,
                        validation_answer_key_sha256=expected_answer_key,
                    ),
                    neo4j_database="framework",
                    project_input_paths=tuple(
                        path for path in project.rglob("*") if path.is_file()
                    ),
                    source_ast_closure_ref=closure,
                    source_ast_closure_bytes=closure.read_bytes(),
                    source_ast_closure_sha256=_sha(closure),
                    strategy_prompts_dir=prompts,
                    human_glossary_manifest={"supplied": False},
                    source_cache=SourceFileCache(),
                    model_runtime=load_model_runtime_capabilities(str(runtime), _sha(runtime)),
                    settings=_Settings(),
                    repository_root=repository,
                )

    def test_directory_answer_key_seals_every_file_and_rejects_child_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, prompts, project, ddl, closure, runtime, baseline, _ = (
                self._repository(root)
            )
            answer_key = root / "gold"
            claims = answer_key / "claims"
            claims.mkdir(parents=True)
            (answer_key / "routines.json").write_text('{"routines":[]}', encoding="utf-8")
            claim = claims / "c010.json"
            claim.write_text('{"claim":"approved"}', encoding="utf-8")
            expected_answer_key = _tree_sha(answer_key)
            model_runtime = load_model_runtime_capabilities(str(runtime), _sha(runtime))

            def build():
                return build_sealed_analysis_inputs(
                    codebase=Codebase(
                        root=project,
                        corpus_root=root / "data",
                        ddl_dir=ddl,
                        strategy="framework",
                        validation_baseline_ref=baseline,
                        validation_baseline_sha256=_sha(baseline),
                        validation_answer_key_ref=answer_key,
                        validation_answer_key_sha256=expected_answer_key,
                    ),
                    neo4j_database="framework",
                    project_input_paths=tuple(
                        path for path in project.rglob("*") if path.is_file()
                    ),
                    source_ast_closure_ref=closure,
                    source_ast_closure_bytes=closure.read_bytes(),
                    source_ast_closure_sha256=_sha(closure),
                    strategy_prompts_dir=prompts,
                    human_glossary_manifest={"supplied": False},
                    source_cache=SourceFileCache(),
                    model_runtime=model_runtime,
                    settings=_Settings(),
                    repository_root=repository,
                )

            sealed = build()
            answer = sealed.payload["validation_answer_key"]
            self.assertEqual("directory", answer["kind"])
            self.assertEqual(expected_answer_key, answer["sha256"])
            self.assertEqual(2, answer["file_count"])
            self.assertEqual(
                ["claims/c010.json", "routines.json"],
                [item["relative_path"] for item in answer["files"]],
            )

            claim.write_text('{"claim":"changed"}', encoding="utf-8")
            with self.assertRaisesRegex(SealedAnalysisInputsError, "validation_answer_key"):
                build()


if __name__ == "__main__":
    unittest.main()
