from pathlib import Path

import yaml


CONFIGS = Path(__file__).resolve().parents[2] / "llm" / "configs"


def test_openrouter_configs_disable_cross_provider_fallbacks() -> None:
    openrouter_configs = []
    for path in sorted(CONFIGS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if document.get("provider") != "openrouter":
            continue
        openrouter_configs.append(path.name)
        provider_options = (document.get("extra_body") or {}).get("provider") or {}
        assert provider_options.get("allow_fallbacks") is False, path.name

    assert openrouter_configs
