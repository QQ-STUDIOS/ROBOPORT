"""A provider must never be handed another provider's model name."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from roboport_runtime.config import DEFAULT_MODELS, resolved_model

OLLAMA_CONFIG = {"models": {
    "reasoning-strong": {"provider": "ollama", "model": "qwen3:14b", "base_url": "http://localhost:11434"},
    "any": {"provider": "ollama", "model": "qwen3:14b", "base_url": "http://localhost:11434"},
}}
ANTHROPIC_CONFIG = {"models": {
    "reasoning-strong": {"provider": "anthropic", "model": "claude-custom-r"},
    "any": {"provider": "anthropic", "model": "claude-custom-d"},
}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ANTHROPIC_MODEL_REASONING", "ANTHROPIC_MODEL_DEFAULT", "OLLAMA_MODEL_REASONING",
                 "OLLAMA_MODEL_DEFAULT", "OLLAMA_HOST", "ROBOPORT_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def test_anthropic_over_ollama_routes_uses_anthropic_defaults():
    # The bug: AnthropicProvider sent model "qwen3:14b" to /v1/messages.
    assert resolved_model("reasoning-strong", OLLAMA_CONFIG, "anthropic")["model"] == DEFAULT_MODELS["anthropic"]["REASONING"]
    assert resolved_model("tool-use-capable", OLLAMA_CONFIG, "anthropic")["model"] == DEFAULT_MODELS["anthropic"]["DEFAULT"]


def test_anthropic_env_override_wins_over_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL_REASONING", "claude-from-env")
    assert resolved_model("reasoning-strong", OLLAMA_CONFIG, "anthropic") == {"provider": "anthropic", "model": "claude-from-env"}


def test_matching_route_is_used_as_configured():
    assert resolved_model("reasoning-strong", ANTHROPIC_CONFIG, "anthropic")["model"] == "claude-custom-r"
    assert resolved_model("reasoning-strong", OLLAMA_CONFIG, "ollama")["model"] == "qwen3:14b"
    assert resolved_model("any", OLLAMA_CONFIG) == OLLAMA_CONFIG["models"]["any"]


def test_ollama_over_anthropic_routes_gets_an_ollama_route(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
    route = resolved_model("any", ANTHROPIC_CONFIG, "ollama")
    assert route == {"provider": "ollama", "model": DEFAULT_MODELS["ollama"]["DEFAULT"], "base_url": "http://gpu-box:11434"}


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unsupported provider"):
        resolved_model("any", OLLAMA_CONFIG, "openai")


def test_anthropic_provider_uses_its_own_models(monkeypatch):
    pytest.importorskip("anthropic")
    from roboport_runtime import providers

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.setattr(providers, "load_runtime_config", lambda: OLLAMA_CONFIG)
    p = providers.AnthropicProvider()
    assert p.model_for("reasoning-strong") == DEFAULT_MODELS["anthropic"]["REASONING"]
    assert p.model_for("any") == DEFAULT_MODELS["anthropic"]["DEFAULT"]
