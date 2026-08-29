from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llm.configs import loader as config_loader
from llm.runners.prompt_rules import _load_rule
from pipeline.models.human_glossary import load_human_glossary
from shared.files.cache import SourceFileCache
from shared.files.run_cache_context import (
    read_run_text,
    reset_run_file_cache,
    set_run_file_cache,
)


class RunFileCacheContextTest(unittest.TestCase):
    def test_text_read_preserves_standard_universal_newline_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "windows.txt"
            path.write_bytes(b"first\r\nsecond\rthird\n")

            cache = SourceFileCache()
            token = set_run_file_cache(cache)
            try:
                self.assertEqual("first\nsecond\nthird\n", read_run_text(path))
            finally:
                reset_run_file_cache(token)

    def test_active_run_returns_the_first_exact_bytes_after_disk_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.txt"
            path.write_text("sealed", encoding="utf-8")
            cache = SourceFileCache()
            token = set_run_file_cache(cache)
            try:
                self.assertEqual("sealed", read_run_text(path))
                path.write_text("mutated", encoding="utf-8")
                self.assertEqual("sealed", read_run_text(path))
            finally:
                reset_run_file_cache(token)
            self.assertEqual("mutated", read_run_text(path))

    def test_prompt_rule_uses_bytes_already_read_by_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            prompts = Path(temp) / "prompts" / "framework"
            prompts.mkdir(parents=True)
            rule_path = prompts / "summary.yaml"
            rule_path.write_text(
                "system: sealed-system\nuser_template: 'sealed {value}'\n",
                encoding="utf-8",
            )
            cache = SourceFileCache()
            cache.read_bytes(rule_path.resolve())
            token = set_run_file_cache(cache)
            try:
                rule_path.write_text(
                    "system: mutated-system\nuser_template: mutated\n",
                    encoding="utf-8",
                )
                rule = _load_rule(prompts, "summary")
            finally:
                reset_run_file_cache(token)
            self.assertEqual("sealed-system", rule["system"])
            self.assertEqual("sealed {value}", rule["user_template"])

    def test_llm_configuration_uses_bytes_already_read_by_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            configs = Path(temp)
            config_path = configs / "sealed.yaml"
            config_path.write_text(
                "provider: openai\n"
                "model: sealed-model\n"
                "api_base: https://example.invalid/v1\n",
                encoding="utf-8",
            )
            cache = SourceFileCache()
            cache.read_bytes(config_path.resolve())
            token = set_run_file_cache(cache)
            try:
                config_path.write_text("provider: broken\n", encoding="utf-8")
                with patch.object(config_loader, "_CONFIGS_DIR", configs):
                    loaded = config_loader.load_llm_config("sealed")
            finally:
                reset_run_file_cache(token)
            self.assertEqual("sealed-model", loaded.model)

    def test_human_glossary_and_project_seal_share_the_first_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "analysis" / "glossary.yaml"
            path.parent.mkdir()
            path.write_text(
                "schema_version: '1.0'\n"
                "terms:\n"
                "  - canonical: Sealed\n"
                "    aliases: []\n"
                "    meaning: original meaning\n"
                "    category: ''\n",
                encoding="utf-8",
            )
            sealed_bytes = path.read_bytes()
            cache = SourceFileCache()
            token = set_run_file_cache(cache)
            try:
                glossary = load_human_glossary(root)
                path.write_text("broken: true\n", encoding="utf-8")
                self.assertEqual(sealed_bytes, cache.read_bytes(path.resolve()))
                second = load_human_glossary(root)
            finally:
                reset_run_file_cache(token)

            self.assertEqual("original meaning", glossary.terms[0].meaning)
            self.assertEqual(glossary, second)


if __name__ == "__main__":
    unittest.main()
