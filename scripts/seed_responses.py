"""검증된 prompt evidence를 응답 캐시에 넣어 같은 요청을 재호출하지 않게 한다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from langchain_core.messages import HumanMessage, SystemMessage

from llm import response_cache
from llm.configs.config_loader import load_llm_config
from llm.prompt_filters import apply_cache_friendly_filters
from shared.config.app_settings import SETTINGS


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def prompt_evidence_sha256(prompt_dir: Path) -> str:
    files = sorted(prompt_dir.glob("*.jsonl"), key=lambda item: item.name)
    if not files:
        raise ValueError(f"no prompt evidence JSONL files: {prompt_dir}")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def exact_response_schema(evidence: object) -> dict[str, object]:
    """Unwrap tracing's content-addressed schema envelope without trusting it."""
    if not isinstance(evidence, dict):
        raise ValueError("recorded response schema is missing")
    if set(evidence) == {"sha256", "bytes", "schema"}:
        schema = evidence.get("schema")
        if not isinstance(schema, dict):
            raise ValueError("recorded response schema envelope is invalid")
        canonical = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if evidence.get("bytes") != len(canonical) or evidence.get(
            "sha256",
        ) != hashlib.sha256(canonical).hexdigest():
            raise ValueError("recorded response schema envelope hash mismatch")
        return schema
    # Historical evidence before the tracing envelope stored the exact schema.
    return evidence


def seed_responses(
    run_dir: Path,
    *,
    expected_sha256: str,
) -> dict[str, object]:
    run_dir = run_dir.expanduser().resolve()
    prompt_dir = run_dir / "llm-prompts"
    expected = expected_sha256.strip().lower()
    if _SHA256.fullmatch(expected) is None:
        raise ValueError("expected prompt evidence SHA-256 is invalid")
    actual = prompt_evidence_sha256(prompt_dir)
    if actual != expected:
        raise ValueError(
            f"prompt evidence SHA-256 mismatch: expected={expected} actual={actual}"
        )
    if not response_cache.is_enabled():
        raise ValueError("sealed response cache is not enabled")

    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    imported = 0
    by_rule: dict[str, int] = {}
    for path in sorted(prompt_dir.glob("*.jsonl"), key=lambda item: item.name):
        rule_name = path.stem
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            record = json.loads(line)
            response = record.get("response")
            parsed = response.get("parsed") if isinstance(response, dict) else None
            response_schema = exact_response_schema(record.get("response_schema"))
            metadata = record.get("metadata")
            if parsed is None:
                raise ValueError(
                    f"incomplete recorded response: {path.name}:{line_number}"
                )
            if not isinstance(metadata, dict):
                raise ValueError(
                    f"missing recorded metadata: {path.name}:{line_number}"
                )
            recorded_model = metadata.get("model")
            if recorded_model != cfg.model:
                raise ValueError(
                    f"recorded model/config mismatch: {path.name}:{line_number}"
                )
            structured_output_method = metadata.get(
                "structured_output_method",
            )
            if not isinstance(structured_output_method, str):
                raise ValueError(
                    f"missing structured output method: {path.name}:{line_number}"
                )
            system_prompt = record.get("system_prompt")
            user_content = record.get("user_content")
            if not isinstance(system_prompt, str) or not isinstance(user_content, str):
                raise ValueError(
                    f"invalid recorded messages: {path.name}:{line_number}"
                )
            messages = apply_cache_friendly_filters(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_content),
                ],
                provider=cfg.provider,
            )
            key = response_cache.make_key_from_schema(
                rule_name=rule_name,
                system_prompt=str(messages[0].content),
                user_content=str(messages[1].content),
                runtime_sha256=(
                    SETTINGS.analysis_llm.model_runtime_capabilities_sha256
                ),
                provider=cfg.provider,
                api_base=cfg.api_base,
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.sampling.temperature,
                top_p=cfg.sampling.top_p,
                reasoning_effort=cfg.reasoning_effort,
                extra_body=cfg.extra_body,
                repetition_guard=SETTINGS.analysis_llm.repetition_guard,
                structured_output_method=structured_output_method,
                response_schema=response_schema,
            )
            response_cache.store(key, parsed)
            imported += 1
            by_rule[rule_name] = by_rule.get(rule_name, 0) + 1
    return {
        "prompt_evidence_sha256": actual,
        "imported": imported,
        "by_rule": dict(sorted(by_rule.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed exact recorded responses for fail-closed replay",
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(
        seed_responses(
            args.run_dir,
            expected_sha256=args.expected_sha256,
        ),
        ensure_ascii=False,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
