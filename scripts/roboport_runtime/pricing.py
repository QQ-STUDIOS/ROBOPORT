"""Per-model token pricing — the cost half of the Phase-4 routing telemetry.

`cost_for(provider, model, prompt_tokens, completion_tokens)` returns the USD
cost of a call, or **None when the price is unknown** — we never fabricate a
number. Local Ollama models have no API cost, so they return 0.0.

The Anthropic table is a snapshot (published prices, USD per 1M tokens; cached
2026-06-04). Override it without editing this file via `config/pricing.yaml`
or the `ROBOPORT_PRICING` env var (JSON: `{"anthropic": {"<model>": [in, out]}}`);
unknown models still resolve to None so cost is reported only when it is real.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# USD per 1,000,000 tokens, as (input, output).
_DEFAULT_PRICES: dict[str, dict[str, tuple[float, float]]] = {
    "anthropic": {
        "claude-fable-5": (10.0, 50.0),
        "claude-opus-4-8": (5.0, 25.0),
        "claude-opus-4-7": (5.0, 25.0),
        "claude-opus-4-6": (5.0, 25.0),
        "claude-opus-4-5": (5.0, 25.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-sonnet-4-5": (3.0, 15.0),
        "claude-haiku-4-5": (1.0, 5.0),
    },
}


@functools.lru_cache(maxsize=1)
def _prices() -> dict[str, dict[str, tuple[float, float]]]:
    """Default table merged with optional overrides from config/env."""
    table = {prov: dict(models) for prov, models in _DEFAULT_PRICES.items()}
    raw = None
    env = os.environ.get("ROBOPORT_PRICING")
    if env:
        try:
            raw = json.loads(env)
        except json.JSONDecodeError:
            raw = None
    if raw is None:
        path = REPO / "config" / "pricing.yaml"
        if path.exists():
            try:
                import yaml  # noqa: PLC0415
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — pricing degrades to defaults
                raw = None
    if isinstance(raw, dict):
        for prov, models in raw.items():
            if not isinstance(models, dict):
                continue
            dest = table.setdefault(str(prov).lower(), {})
            for model, pair in models.items():
                try:
                    dest[str(model)] = (float(pair[0]), float(pair[1]))
                except (TypeError, ValueError, IndexError):
                    continue
    return table


def cost_for(provider: str | None, model: str | None,
             prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    """USD cost of one call, or None if the price is unknown.

    Local Ollama models have no API cost (0.0). Anthropic models price off the
    table; an unknown model returns None so callers report cost only when real.
    """
    prov = (provider or "").lower()
    if prov == "ollama":
        return 0.0
    pair = _prices().get(prov, {}).get(model or "")
    if pair is None:
        return None
    inp, out = pair
    cost = (int(prompt_tokens or 0) / 1_000_000) * inp \
        + (int(completion_tokens or 0) / 1_000_000) * out
    return round(cost, 6)
