"""Persisted grading contract, including resolved citation integrity shapes."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'resources/schemas/grading.schema.json').read_text())


def sample(method='deterministic_checks_and_blinded_consensus'):
    return {'run_id': 'offline', 'results': [{'expectation': 'answer is 7', 'verdict': 'PASS', 'evidence': 'Compared 7.', 'citations': [{'source': 'final_output', 'pointer': '/answer', 'quote': '7'}]}], 'pass_rate': 1, 'blocker_failed': False,
            'verification': {'method': method, 'assessments_completed': 2, 'deterministic_coverage': 1}}


@pytest.mark.parametrize('method', ['blinded_consensus', 'deterministic_checks_and_blinded_consensus'])
def test_persisted_methods(method):
    jsonschema.Draft7Validator.check_schema(SCHEMA)
    jsonschema.validate(sample(method), SCHEMA)


@pytest.mark.parametrize('mutation', [
    lambda d: d['verification'].update(method='unknown'),
    lambda d: d['verification'].update(deterministic_coverage=-1),
    lambda d: d['verification'].update(deterministic_coverage=1.5),
    lambda d: d['verification'].pop('deterministic_coverage'),
    lambda d: d['results'][0]['citations'][0].update(pointer='answer'),
    lambda d: d['results'][0]['citations'][0].update(source=''),
    lambda d: d['results'][0]['citations'][0].pop('quote'),
    lambda d: d['results'][0]['citations'][0].update(quote=7),
])
def test_malformed_persisted_verification_and_references(mutation):
    data = copy.deepcopy(sample())
    mutation(data)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)

def test_legacy_verification_without_coverage_and_empty_quote_root_pointer():
    data = sample('blinded_consensus')
    data['verification'].pop('deterministic_coverage')
    data['results'][0]['citations'][0].update(pointer='', quote='')
    jsonschema.validate(data, SCHEMA)


@pytest.mark.parametrize('pointer', ['/bad~2escape', '/bad~'])
def test_invalid_pointer_escape(pointer):
    data = sample()
    data['results'][0]['citations'][0]['pointer'] = pointer
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
