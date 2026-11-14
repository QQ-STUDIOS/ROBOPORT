"""Provider-boundary regression cases for trustworthy grading decisions."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from roboport_runtime import budget, client, grader


class AssessmentBackend:
    name = 'ollama'
    config = {'defaults': {'context_tokens': 100000, 'max_tokens': 2048}}

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def model_for(self, hint):
        return 'qwen3:14b'

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return {'content': json.dumps(response), 'tool_calls': [], 'usage': {'cost_usd': 0}}


def assessment(finding='SUPPORTED', quote='"completed_count":3', expectation_id=0):
    return {'results': [{'expectation_id': expectation_id, 'finding': finding,
                        'citations': [] if finding == 'INSUFFICIENT' else [
                            {'source': 'final_output', 'quote': quote}]}], 'meta_critique': []}


def run_grade(tmp_path, monkeypatch, responses, expectations=None, blockers=None):
    (tmp_path / 'plan.json').write_text('{}')
    (tmp_path / 'final_output.json').write_text('{"completed_count":3,"proof":"final-proof"}')
    backend = AssessmentBackend(responses)
    monkeypatch.setattr(client, 'provider', lambda: backend)
    with budget.run_budget(budget.RunBudget({})):
        result = grader.call_grader(expectations or ['completed_count is 3.'], None, tmp_path, blockers=blockers)
    return result, backend


def test_stable_ids_preserve_punctuation_and_compute_scores(tmp_path, monkeypatch):
    response = assessment()
    result, backend = run_grade(tmp_path, monkeypatch, [response, response])
    assert result['results'][0]['expectation'] == 'completed_count is 3.'
    assert result['results'][0]['verdict'] == 'PASS'
    assert result['results'][0]['citations'] == [{'source': 'final_output', 'quote': '"completed_count":3'}]
    assert result['pass_rate'] == 1 and not result['blocker_failed']
    assert not result['review_required']
    assert len(backend.calls) == 2


@pytest.mark.parametrize('first,second', [('SUPPORTED', 'CONTRADICTED'), ('CONTRADICTED', 'SUPPORTED'), ('SUPPORTED', 'INSUFFICIENT')])
def test_disagreement_never_becomes_a_trusted_pass_or_fail(tmp_path, monkeypatch, first, second):
    result, _ = run_grade(tmp_path, monkeypatch, [assessment(first), assessment(second)])
    assert result['results'][0]['verdict'] == 'INCONCLUSIVE'
    assert result['review_required'] and result['blocker_failed'] and result['pass_rate'] == 0
    assert 'disagree' in result['results'][0]['evidence'].lower()


@pytest.mark.parametrize('quote', ['"completed_count":99', '', '  '])
def test_unverifiable_citation_fails_closed(tmp_path, monkeypatch, quote):
    result, _ = run_grade(tmp_path, monkeypatch, [assessment(quote=quote)] * 2)
    assert result['pass_rate'] == 0 and result['review_required'] and result['blocker_failed']
    assert result['results'][0]['verdict'] == 'INCONCLUSIVE'


@pytest.mark.parametrize('finding,want', [('CONTRADICTED', 'FAIL'), ('INSUFFICIENT', 'INCONCLUSIVE')])
def test_agreed_findings_have_distinct_verdicts(tmp_path, monkeypatch, finding, want):
    response = assessment(finding)
    result, _ = run_grade(tmp_path, monkeypatch, [response, response], blockers=['completed_count is 3.'])
    assert result['results'][0]['verdict'] == want
    assert result['blocker_failed'] and result['pass_rate'] == 0
    assert not result['review_required']


@pytest.mark.parametrize('malformation', ['duplicate', 'missing', 'out_of_range', 'wrong_source', 'empty_citations'])
def test_malformed_assessment_is_review_required(tmp_path, monkeypatch, malformation):
    response = assessment()
    row = response['results'][0]
    if malformation == 'duplicate':
        response['results'].append(copy.deepcopy(row))
    elif malformation == 'missing':
        response['results'] = []
    elif malformation == 'out_of_range':
        row['expectation_id'] = 1
    elif malformation == 'wrong_source':
        row['citations'][0]['source'] = 'run_log'
    else:
        row['citations'] = []
    result, _ = run_grade(tmp_path, monkeypatch, [response] * 2)
    assert result['review_required'] and result['blocker_failed'] and result['pass_rate'] == 0


def test_second_assessment_error_does_not_keep_first_pass(tmp_path, monkeypatch):
    result, _ = run_grade(tmp_path, monkeypatch, [assessment(), RuntimeError('verification unavailable')])
    assert result['review_required'] and result['blocker_failed'] and result['pass_rate'] == 0
    assert result['results'][0]['verdict'] == 'INCONCLUSIVE'


@pytest.mark.parametrize('include_steps', [False, True])
def test_citation_must_come_from_the_selected_prompt(tmp_path, monkeypatch, include_steps):
    (tmp_path / 'final_output.json').write_text('{}')
    (tmp_path / 'steps').mkdir()
    (tmp_path / 'steps/one.json').write_text('{"status":"ok","output":{"proof":"step-secret"}}')
    response = assessment(quote='step-secret')
    response['results'][0]['citations'][0]['source'] = 'steps'
    backend = AssessmentBackend([response, response])
    backend.name = 'anthropic'
    backend.model_for = lambda hint: 'claude-sonnet-4-6'
    backend.config = {'defaults': {'context_tokens': 512, 'max_tokens': 16}}
    backend.count_input_tokens = lambda **kwargs: (600 if 'step-secret' in kwargs['messages'][0]['content'] and not include_steps else 100)
    monkeypatch.setattr(client, 'provider', lambda: backend)
    with budget.run_budget(budget.RunBudget({})):
        result = grader.call_grader(['step proof exists'], None, tmp_path)
    assert result['pass_rate'] == (1 if include_steps else 0)
    assert result['review_required'] is (not include_steps)
    assert result['blocker_failed'] is (not include_steps)


def test_ids_allow_reordered_and_identically_worded_expectations(tmp_path, monkeypatch):
    first = assessment()
    other = assessment('CONTRADICTED', expectation_id=1)['results'][0]
    first['results'].append(other)
    second = copy.deepcopy(first)
    second['results'].reverse()
    result, _ = run_grade(tmp_path, monkeypatch, [first, second],
                          expectations=['same wording.', 'same wording.'])
    assert [row['verdict'] for row in result['results']] == ['PASS', 'FAIL']
    assert result['pass_rate'] == 0.5 and not result['review_required']


def test_second_assessment_respects_remaining_call_budget(tmp_path, monkeypatch):
    (tmp_path / 'final_output.json').write_text('{"completed_count":3}')
    backend = AssessmentBackend([assessment(), assessment()])
    monkeypatch.setattr(client, 'provider', lambda: backend)
    limits = budget.RunBudget({'budgets': {'per_run': {'max_llm_calls': 1}}})
    with budget.run_budget(limits):
        result = grader.call_grader(['completed_count is 3.'], None, tmp_path)
    assert len(backend.calls) == 1
    assert result['review_required'] and result['blocker_failed'] and result['pass_rate'] == 0
    assert result['verification']['assessments_completed'] == 1
