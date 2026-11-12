"""One fail-closed configuration source for runtime, tools, and provenance."""
from __future__ import annotations

import math
import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = _merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


_scoped_config = ContextVar('roboport_scoped_config', default=None)


@contextmanager
def runtime_config_scope(config: dict):
    token = _scoped_config.set(deepcopy(config))
    try:
        yield
    finally:
        _scoped_config.reset(token)


def load_runtime_config(repo: Path = REPO) -> dict:
    if _scoped_config.get() is not None:
        return deepcopy(_scoped_config.get())
    config: dict = {}
    for name in ('agent_config.yaml', 'agent_config.local.yaml'):
        path = repo / 'config' / name
        if not path.exists():
            if name == 'agent_config.yaml':
                raise ValueError(f'missing runtime configuration: {path}')
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8'))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f'invalid runtime configuration: {path}: {exc}') from exc
        if not isinstance(data, dict):
            raise ValueError(f'runtime configuration must be a mapping: {path}')  # noqa: TRY004 — one public configuration exception
        config = _merge(config, data)
    for limits in (config.get('budgets') or {}).values():
        if not isinstance(limits, dict):
            raise ValueError('budget configuration must be a mapping')  # noqa: TRY004
        for key, value in limits.items():
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f'invalid budget {key}: {value!r}')
    for hint, route in (config.get('models') or {}).items():
        if hint == 'none':
            continue
        if not isinstance(route, dict):
            raise ValueError(f'invalid model configuration: {hint}')  # noqa: TRY004
        prov = os.environ.get('ROBOPORT_PROVIDER') or route.get('provider', 'ollama')
        if prov not in ('ollama', 'anthropic'):
            raise ValueError(f'unsupported provider: {prov}')
        switched = prov != route.get('provider')
        route['provider'] = prov
        fallback = _default_model(prov, hint)
        route['model'] = os.environ.get(_model_env(prov, hint)) or (fallback if switched else route.get('model', fallback))
        if prov == 'ollama':
            route['base_url'] = os.environ.get('OLLAMA_HOST') or route.get('base_url', DEFAULT_OLLAMA_URL)
    return config


DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434'
DEFAULT_MODELS = {
    'anthropic': {'REASONING': 'claude-opus-4-7', 'DEFAULT': 'claude-sonnet-4-6'},
    'ollama': {'REASONING': 'qwen3:14b', 'DEFAULT': 'qwen3:14b'},
}


def _suffix(hint: str) -> str:
    return 'REASONING' if hint == 'reasoning-strong' else 'DEFAULT'


def _model_env(provider: str, hint: str) -> str:
    return f'{provider.upper()}_MODEL_{_suffix(hint)}'


def _default_model(provider: str, hint: str) -> str:
    return DEFAULT_MODELS[provider][_suffix(hint)]


def resolved_model(hint: str, config: dict, provider: str | None = None) -> dict:
    """Route for ``hint``; with ``provider``, a route that provider can serve.

    A configured route for another provider is never handed over as-is: its
    model name belongs to that provider (an Anthropic call must not ask for
    an Ollama tag). The provider's own env override or default is used instead.
    """
    route = (config.get('models') or {}).get(hint) or (config.get('models') or {}).get('any')
    if not isinstance(route, dict) or not route.get('model') or not route.get('provider'):
        raise ValueError(f'model route is not configured: {hint}')
    if provider is None or route['provider'] == provider:
        return dict(route)
    if provider not in DEFAULT_MODELS:
        raise ValueError(f'unsupported provider: {provider}')
    own = {'provider': provider, 'model': os.environ.get(_model_env(provider, hint)) or _default_model(provider, hint)}
    if provider == 'ollama':
        own['base_url'] = os.environ.get('OLLAMA_HOST') or DEFAULT_OLLAMA_URL
    return own
