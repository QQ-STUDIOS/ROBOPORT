"""Phase 4 — the cost half of routing telemetry: prove cost_for is honest."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from roboport_runtime.pricing import cost_for  # noqa: E402


def test_ollama_is_free_for_any_model():
    assert cost_for("ollama", "qwen3:14b", 1000, 500) == 0.0
    assert cost_for("ollama", "anything", 10, 10) == 0.0


def test_anthropic_known_model_priced():
    # claude-opus-4-8: $5/Mtok in, $25/Mtok out.
    # 1000 in -> 0.005, 500 out -> 0.0125, total 0.0175.
    assert cost_for("anthropic", "claude-opus-4-8", 1000, 500) == 0.0175


def test_anthropic_zero_tokens_is_zero_not_none():
    assert cost_for("anthropic", "claude-sonnet-4-6", 0, 0) == 0.0


def test_unknown_model_is_none_not_fabricated():
    assert cost_for("anthropic", "no-such-model", 1000, 500) is None


def test_unknown_provider_is_none():
    assert cost_for("openai", "gpt-4", 1000, 500) is None


def test_pricing_override_via_env(monkeypatch):
    import roboport_runtime.pricing as pricing  # noqa: PLC0415
    pricing._prices.cache_clear()
    monkeypatch.setenv("ROBOPORT_PRICING", '{"anthropic": {"my-model": [2.0, 8.0]}}')
    try:
        # 1,000,000 in -> $2, 1,000,000 out -> $8.
        assert cost_for("anthropic", "my-model", 1_000_000, 1_000_000) == 10.0
    finally:
        pricing._prices.cache_clear()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
