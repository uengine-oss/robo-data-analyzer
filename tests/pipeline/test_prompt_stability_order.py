"""Every production prompt keeps lower-volatility content before target evidence."""
from __future__ import annotations

import re
from pathlib import Path
import unittest

import yaml

from llm.runners.prompt_rules import _load_rule


ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILES = tuple(sorted(
    path
    for base in (
        ROOT / "llm" / "prompts" / "framework",
        ROOT / "llm" / "prompts" / "dbms",
        ROOT / "agents" / "prompts",
    )
    for path in base.glob("*.yaml")
    if not path.name.startswith("_")
))


class PromptStabilityOrderTest(unittest.TestCase):
    def test_semantic_prompts_do_not_override_runtime_output_limit(self) -> None:
        for path in PROMPT_FILES:
            with self.subTest(path=path.relative_to(ROOT)):
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertNotIn("max_tokens", raw)

    def test_all_yaml_prompts_use_explicit_stability_sections(self) -> None:
        for path in PROMPT_FILES:
            with self.subTest(path=path.relative_to(ROOT)):
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertNotIn("user_template", raw)
                self.assertIn("user_template_specific", raw)
                if "extends" not in raw:
                    self.assertIn("user_template_inputs", raw)

    def test_fixed_prefix_contains_only_static_placeholders(self) -> None:
        automatic_static = {
            "stereotype_options",
            "stereotype_priority",
        }
        for path in PROMPT_FILES:
            with self.subTest(path=path.relative_to(ROOT)):
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                placeholders = set(re.findall(
                    r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
                    str(raw.get("user_template_specific", "")),
                ))
                static = set((raw.get("shared_values") or {})) | automatic_static
                self.assertEqual(set(), placeholders - static)

    def test_loader_composes_fixed_then_input_then_dynamic_input(self) -> None:
        for path in PROMPT_FILES:
            with self.subTest(path=path.relative_to(ROOT)):
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if "extends" in raw:
                    common = yaml.safe_load(
                        (path.parent / f"{raw['extends']}.yaml").read_text(encoding="utf-8")
                    )
                    inputs = (
                        common.get("user_template_inputs", "")
                        if raw.get("inherit_inputs", True)
                        else ""
                    )
                else:
                    inputs = raw.get("user_template_inputs", "")
                expected = (
                    raw.get("user_template_specific", "")
                    + inputs
                    + raw.get("user_template_dynamic_inputs", "")
                )
                loaded = _load_rule(path.parent, path.stem)
                self.assertEqual(expected, loaded["user_template"])

    def test_agent_system_prompts_are_separate_from_volatile_user_messages(self) -> None:
        for module in ("external.py", "scan.py"):
            with self.subTest(module=module):
                source = (ROOT / "agents" / module).read_text(encoding="utf-8")
                self.assertIn(
                    'system_prompt=read_run_text(_SYSTEM_PROMPT_PATH, encoding="utf-8")',
                    source,
                )
                self.assertNotIn(
                    'system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")',
                    source,
                )
                self.assertIn("user_message=", source)


if __name__ == "__main__":
    unittest.main()
