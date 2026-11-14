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


@pytest.mark.parametrize('routed', [False, True])
@pytest.mark.parametrize('fits', ['steps', 'notice', 'base', 'none'])
def test_anthropic_fallbacks_use_token_counts(tmp_path, monkeypatch, routed, fits):
    backend, router = setup_grading(tmp_path, monkeypatch, routed)
    backend.name = 'anthropic'
    backend.model_for = lambda hint: 'claude-sonnet-4-6'
    backend.config['defaults']['context_tokens'] = 512
    router.policy['candidates']['reasoning-strong'] = [
        {'provider': 'anthropic', 'model': 'claude-sonnet-4-6'}]
    counted = []
    def count_tokens(**kwargs):
        counted.append(kwargs)
        prompt = kwargs['messages'][0]['content']
        kind = 'steps' if 'x' * 100 in prompt else 'notice' if 'omitted' in prompt else 'base'
        return 100 if kind == fits else 600
    backend.count_input_tokens = count_tokens
    with config.runtime_config_scope(backend.config):
        result = grade(tmp_path)
    if fits == 'none':
        assert result['blocker_failed'] and result['pass_rate'] == 0
        assert not backend.calls
        assert len(counted) == 3
    else:
        assert result['pass_rate'] == 1, result
        assert backend.calls == [counted[-1]]
        prompt = backend.calls[0]['messages'][0]['content']
        assert ('x' * 100 in prompt) == (fits == 'steps')
        assert ('omitted' in prompt) == (fits == 'notice')
        assert len(counted) == {'steps': 1, 'notice': 2, 'base': 3}[fits]
    if routed:
        assert len(router.decisions) == 1
        assert all(k['model'] == 'claude-sonnet-4-6' for k in counted)


@pytest.mark.parametrize('evidence', ['', ' ', '\n\t'])
def test_blank_evidence_cannot_pass_a_declared_blocker(tmp_path, monkeypatch, evidence):
    backend, _ = setup_grading(tmp_path, monkeypatch)
    backend.chat_with_tools = lambda **kwargs: {'content': json.dumps({
        'results': [{'expectation': 'final evidence exists', 'verdict': 'PASS', 'evidence': evidence}],
        'pass_rate': 1, 'blocker_failed': False}), 'usage': {'cost_usd': 0}}
    with config.runtime_config_scope(backend.config), budget.run_budget(budget.RunBudget({})):
        result = grader.call_grader(['final evidence exists'], None, tmp_path,
                                    blockers=['final evidence exists'])
    assert result['pass_rate'] == 0
    assert result['blocker_failed']
