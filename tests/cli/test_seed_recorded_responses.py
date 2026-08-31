import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli.seed_recorded_responses import (
    prompt_evidence_sha256,
    seed_recorded_responses,
)
from llm.configs.loader import LLMConfig, SamplingConfig


def test_seeded_record_is_content_addressed_and_uses_exact_schema(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "llm-prompts"
    prompt_dir.mkdir()
    record = {
        "system_prompt": "system",
        "user_content": "user",
        "response": {"parsed": {"summary": "meaning"}},
        "response_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        "metadata": {
            "model": "sealed-model",
            "structured_output_method": "json_schema",
        },
    }
    (prompt_dir / "code_summary.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8",
    )
    expected = prompt_evidence_sha256(prompt_dir)
    config = LLMConfig(
        provider="openai",
        model="sealed-model",
        api_base="https://example.invalid/v1",
        max_tokens=100,
        sampling=SamplingConfig(temperature=0.0, top_p=1.0),
        structured_output_method="json_schema",
    )
    settings = SimpleNamespace(analysis_llm=SimpleNamespace(
        config="sealed",
        model_runtime_capabilities_sha256="a" * 64,
        repetition_guard=True,
    ))

    with (
        patch("cli.seed_recorded_responses.response_cache.is_enabled", return_value=True),
        patch("cli.seed_recorded_responses.load_llm_config", return_value=config),
        patch("cli.seed_recorded_responses.response_cache.store") as store,
        patch("cli.seed_recorded_responses.SETTINGS", settings),
    ):
        result = seed_recorded_responses(tmp_path, expected_sha256=expected)

    assert result["imported"] == 1
    assert result["by_rule"] == {"code_summary": 1}
    store.assert_called_once()
    assert store.call_args.args[1] == {"summary": "meaning"}


def test_prompt_evidence_hash_detects_record_change(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "llm-prompts"
    prompt_dir.mkdir()
    path = prompt_dir / "rule.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    before = prompt_evidence_sha256(prompt_dir)
    path.write_text('{"changed":true}\n', encoding="utf-8")
    assert prompt_evidence_sha256(prompt_dir) != before
