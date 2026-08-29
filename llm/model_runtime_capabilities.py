"""Content-addressed model runtime capabilities used by analysis preflight and cache."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml

from shared.files.run_cache_context import read_run_bytes


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelRuntimeCapabilitiesError(ValueError):
    """The supplied runtime capability artifact is absent, mutable, or incomplete."""


@dataclass(frozen=True)
class ModelRuntimeCapabilities:
    artifact_path: Path
    artifact_sha256: str
    runtime_kind: str
    provider_id: str
    model_id: str
    model_revision: str
    serving_runtime_id: str
    serving_runtime_version: str
    runtime_config_sha256: str
    context_tokens: int
    max_output_tokens: int
    profile_version: str
    tokenizer_id: str | None = None
    tokenizer_revision: str | None = None
    quantization: str | None = None
    gpu_model: str | None = None
    gpu_count: int | None = None
    memory_bytes_per_gpu: int | None = None
    driver_runtime_version: str | None = None
    tokenizer_identity_disclosure: str | None = None
    quantization_disclosure: str | None = None
    infrastructure_disclosure: str | None = None

    def sealed_values(self) -> dict[str, object]:
        """Return the deployment-kind-specific values recorded in the run seal."""
        common: dict[str, object] = {
            "runtime_kind": self.runtime_kind,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "context_tokens": self.context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "profile_version": self.profile_version,
            "serving_runtime_id": self.serving_runtime_id,
            "serving_runtime_version": self.serving_runtime_version,
            "runtime_config_sha256": self.runtime_config_sha256,
        }
        if self.runtime_kind == "self_hosted":
            common.update({
                "tokenizer_id": self.tokenizer_id,
                "tokenizer_revision": self.tokenizer_revision,
                "quantization": self.quantization,
                "gpu_model": self.gpu_model,
                "gpu_count": self.gpu_count,
                "memory_bytes_per_gpu": self.memory_bytes_per_gpu,
                "driver_runtime_version": self.driver_runtime_version,
            })
        else:
            common["provider_managed_disclosure"] = {
                "tokenizer_identity": self.tokenizer_identity_disclosure,
                "quantization": self.quantization_disclosure,
                "infrastructure": self.infrastructure_disclosure,
            }
        return common


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelRuntimeCapabilitiesError(f"{field_name} must be a mapping")
    return value


def _text(values: dict[str, Any], field_name: str) -> str:
    value = values.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ModelRuntimeCapabilitiesError(f"{field_name} must be a non-blank string")
    return value.strip()


def _sha(values: dict[str, Any], field_name: str) -> str:
    value = _text(values, field_name).lower()
    if _SHA256.fullmatch(value) is None:
        raise ModelRuntimeCapabilitiesError(f"{field_name} must be a SHA-256 hex digest")
    return value


def _positive_int(values: dict[str, Any], field_name: str) -> int:
    value = values.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModelRuntimeCapabilitiesError(f"{field_name} must be a positive integer")
    return value


def _disclosure(values: dict[str, Any], field_name: str) -> str:
    value = _text(values, field_name)
    if value != "provider_managed_undisclosed":
        raise ModelRuntimeCapabilitiesError(
            f"{field_name} must be provider_managed_undisclosed"
        )
    return value


@lru_cache(maxsize=8)
def load_model_runtime_capabilities(
    artifact_ref: str,
    expected_sha256: str,
) -> ModelRuntimeCapabilities:
    """Read one immutable runtime artifact once and verify its hash and required fields."""
    expected = expected_sha256.strip().lower()
    if _SHA256.fullmatch(expected) is None:
        raise ModelRuntimeCapabilitiesError(
            "model runtime capability expected_sha256 must be a SHA-256 hex digest"
        )
    path = Path(artifact_ref).expanduser().resolve()
    try:
        payload = read_run_bytes(path)
    except OSError as error:
        raise ModelRuntimeCapabilitiesError(
            f"cannot read model runtime capability artifact: {path}: {error}"
        ) from error
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ModelRuntimeCapabilitiesError(
            "model runtime capability SHA-256 mismatch: "
            f"expected={expected} actual={actual} ref={path}"
        )
    try:
        loaded = yaml.safe_load(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ModelRuntimeCapabilitiesError(
            f"invalid model runtime capability YAML: {path}: {error}"
        ) from error
    root = _mapping(loaded, "runtime capability root")
    capabilities = _mapping(
        root.get("sealed_model_runtime_capabilities"),
        "sealed_model_runtime_capabilities",
    )
    runtime_kind = _text(capabilities, "runtime_kind")
    if runtime_kind not in {"self_hosted", "provider_managed"}:
        raise ModelRuntimeCapabilitiesError(
            "runtime_kind must be self_hosted or provider_managed"
        )
    serving = _mapping(capabilities.get("serving_runtime"), "serving_runtime")
    hard_limits = _mapping(
        capabilities.get("provider_hard_limits"), "provider_hard_limits"
    )
    common = {
        "artifact_path": path,
        "artifact_sha256": actual,
        "runtime_kind": runtime_kind,
        "provider_id": _text(capabilities, "provider_id"),
        "model_id": _text(capabilities, "model_id"),
        "model_revision": _text(capabilities, "model_revision"),
        "serving_runtime_id": _text(serving, "runtime_id"),
        "serving_runtime_version": _text(serving, "runtime_version"),
        "runtime_config_sha256": _sha(serving, "runtime_config_sha256"),
        "context_tokens": _positive_int(hard_limits, "context_tokens"),
        "max_output_tokens": _positive_int(hard_limits, "max_output_tokens"),
    }
    if runtime_kind == "self_hosted":
        gpu = _mapping(capabilities.get("gpu_runtime_profile"), "gpu_runtime_profile")
        return ModelRuntimeCapabilities(
            **common,
            tokenizer_id=_text(capabilities, "tokenizer_id"),
            tokenizer_revision=_text(capabilities, "tokenizer_revision"),
            quantization=_text(capabilities, "quantization"),
            gpu_model=_text(gpu, "gpu_model"),
            gpu_count=_positive_int(gpu, "gpu_count"),
            memory_bytes_per_gpu=_positive_int(gpu, "memory_bytes_per_gpu"),
            driver_runtime_version=_text(gpu, "driver_runtime_version"),
            profile_version=_text(gpu, "profile_version"),
        )
    disclosure = _mapping(
        capabilities.get("provider_managed_disclosure"),
        "provider_managed_disclosure",
    )
    return ModelRuntimeCapabilities(
        **common,
        profile_version=_text(capabilities, "profile_version"),
        tokenizer_identity_disclosure=_disclosure(
            disclosure, "tokenizer_identity",
        ),
        quantization_disclosure=_disclosure(disclosure, "quantization"),
        infrastructure_disclosure=_disclosure(disclosure, "infrastructure"),
    )
