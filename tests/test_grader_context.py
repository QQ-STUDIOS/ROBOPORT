"""Exercise grading through real routing, context enforcement and JSON validation."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from roboport_runtime import budget, client, config, grader
from roboport_runtime.routing import RoutingProvider


class GradingBackend:
    name = 'ollama'

    def __init__(self):
        self.config = {'defaults': {'context_tokens': 100000, 'max_tokens': 16}}
        self.calls = []

    def model_for(self, hint):
        return 'qwen3:14b'

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return {'content': json.dumps({'results': [
            {'expectation': 'final evidence exists', 'verdict': 'PASS', 'evidence': 'final-proof'}
        ], 'pass_rate': 1, 'blocker_failed': False}),
                'tool_calls': [], 'usage': {'cost_usd': 0.0}}


def setup_grading(tmp_path, monkeypatch, routed=False):
    backend = GradingBackend()
    router = RoutingProvider({'objective': 'static', 'constraints': {}, 'candidates': {
        'reasoning-strong': [{'provider': 'ollama', 'model': 'qwen3:14b'}]
    }}, lambda name: backend)
    monkeypatch.setattr(client, 'provider', lambda: router if routed else backend)
    monkeypatch.setattr(grader, 'load_agent_spec', lambda p: '')
    (tmp_path / 'final_output.json').write_text('{"proof":"final-proof"}')
    (tmp_path / 'plan.json').write_text('{}')
    (tmp_path / 'steps').mkdir()
    (tmp_path / 'steps/one.json').write_text(json.dumps({'status': 'ok', 'output': {'text': 'x' * 2000}}))
    return backend, router


def grade(tmp_path):
    with budget.run_budget(budget.RunBudget({})):
        return grader.call_grader(['final evidence exists'], None, tmp_path)


def request_size(kwargs):
    # Independently calculate the documented UTF-8 + template + completion bound.
    return len(json.dumps(kwargs, ensure_ascii=False).encode('utf-8')) + 1024 + 16


def test_routed_grader_checks_the_model_inclusive_request(tmp_path, monkeypatch):
    backend, router = setup_grading(tmp_path, monkeypatch, routed=True)
    with config.runtime_config_scope(backend.config):
        assert grade(tmp_path)['pass_rate'] == 1
        full = backend.calls.pop()
        without_model = {k: v for k, v in full.items() if k != 'model'}
        backend.config['defaults']['context_tokens'] = request_size(without_model)
        router.decisions.clear()
        result = grade(tmp_path)
    assert result['pass_rate'] == 1, result
    assert not result['blocker_failed']
    assert len(router.decisions) == 1
    sent = backend.calls[-1]
    assert sent['model'] == 'qwen3:14b'
    assert 'omitted' in sent['messages'][0]['content']
    assert request_size(sent) <= backend.config['defaults']['context_tokens']


@pytest.mark.parametrize('routed', [False, True])
def test_omission_notice_cannot_overflow_a_fitting_base_prompt(tmp_path, monkeypatch, routed):
    backend, _ = setup_grading(tmp_path, monkeypatch, routed)
    with config.runtime_config_scope(backend.config):
        grade(tmp_path)
        full = backend.calls.pop()
        base = dict(full)
        base['messages'] = [{'role': 'user', 'content': full['messages'][0]['content'].split('\n\nSTEP OUTPUTS')[0]}]
        backend.config['defaults']['context_tokens'] = request_size(base)
        result = grade(tmp_path)
    assert result['pass_rate'] == 1, result
    sent = backend.calls[-1]
    assert 'final-proof' in sent['messages'][0]['content']
    assert 'x' * 100 not in sent['messages'][0]['content']
    assert request_size(sent) <= backend.config['defaults']['context_tokens']


def test_an_oversized_base_fails_closed_without_dispatch(tmp_path, monkeypatch):
    backend, _ = setup_grading(tmp_path, monkeypatch)
    backend.config['defaults']['context_tokens'] = 1
    with config.runtime_config_scope(backend.config):
        result = grade(tmp_path)
    assert result['pass_rate'] == 0 and result['blocker_failed']
    assert result['results'][0]['verdict'] == 'INCONCLUSIVE'
    assert not backend.calls
