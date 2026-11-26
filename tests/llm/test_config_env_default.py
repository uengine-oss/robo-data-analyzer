"""api_base 의 ${VAR:-기본값} (2026-11-24 · 포스코 현장 2026-11-20 의 P-GPT 404)."""

from llm.configs.config_loader import _expand_env, load_llm_config


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("ROBO_AI_BASE_URL", raising=False)
    assert _expand_env("${ROBO_AI_BASE_URL:-http://a/v1}") == "http://a/v1"


def test_default_when_blank(monkeypatch):
    monkeypatch.setenv("ROBO_AI_BASE_URL", "  ")
    assert _expand_env("${ROBO_AI_BASE_URL:-http://a/v1}") == "http://a/v1"


def test_env_wins(monkeypatch):
    monkeypatch.setenv("ROBO_AI_BASE_URL", "http://pgpt.dx/v1")
    assert _expand_env("${ROBO_AI_BASE_URL:-http://a/v1}") == "http://pgpt.dx/v1"


def test_plain_var_still_works(monkeypatch):
    monkeypatch.setenv("BRIDGE_PORT", "4567")
    assert _expand_env("http://127.0.0.1:${BRIDGE_PORT}/v1") == "http://127.0.0.1:4567/v1"


def test_pgpt_config_follows_env(monkeypatch):
    monkeypatch.setenv("ROBO_AI_BASE_URL", "http://pgpt.posco/v1")
    assert load_llm_config("pgpt").api_base == "http://pgpt.posco/v1"
    monkeypatch.delenv("ROBO_AI_BASE_URL")
    assert load_llm_config("pgpt").api_base == "http://aigpt.posco.net/gpgpta01-gpt/v1"


def test_pgpt_model_follows_env(monkeypatch):
    """레거시 분석 모델도 .env 로(2026-11-26) — 비면 gpt-4.1."""
    monkeypatch.setenv("ROBO_ANALYZER_MODEL", "gpt-4o")
    assert load_llm_config("pgpt").model == "gpt-4o"
    monkeypatch.setenv("ROBO_ANALYZER_MODEL", " ")
    assert load_llm_config("pgpt").model == "gpt-4.1"
    monkeypatch.delenv("ROBO_ANALYZER_MODEL")
    assert load_llm_config("pgpt").model == "gpt-4.1"
