"""Planner metadata is authoritative even when a model returns stale flags."""
import json
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from roboport_runtime import planner

REGISTRY = json.loads((Path(__file__).resolve().parents[1] / 'agents/registry.json').read_text())


def model_plan(owner, output_type='DataReport', deterministic=True):
    return {'goal': 'g', 'deliverable': output_type, 'steps': [
        {'id': 's1', 'owner': owner, 'wave': 0, 'input': {},
         'output_type': output_type, 'success_criteria': ['valid output'],
         'deterministic': deterministic}]}


@pytest.mark.parametrize('owner,kind,claimed,want', [
    ('reporting', 'DataReport', True, False),
    ('data_engineering', 'CleanedDataset', True, False),
    ('compliance_risk', 'RiskAssessment', True, False),
    ('synthesizer', 'FinalReport', False, True),
])
def test_planner_uses_registry_determinism(monkeypatch, owner, kind, claimed, want):
    response = model_plan(owner, kind, claimed)
    monkeypatch.setattr(planner, 'call_model_json', lambda **kwargs: deepcopy(response))
    result = planner.call_planner('g', {}, REGISTRY)
    assert result['steps'][0]['deterministic'] is want
    assert result['steps'][0]['input'] == {}
    assert result['steps'][0]['output_type'] == kind


def test_planner_rejects_unknown_owner(monkeypatch):
    monkeypatch.setattr(planner, 'call_model_json', lambda **kwargs: model_plan('unknown'))
    with pytest.raises(ValueError, match='owner'):
        planner.call_planner('g', {}, REGISTRY)


def test_dataops_rejects_swapped_owner_output_contract(monkeypatch):
    response = model_plan('reporting', 'CleanedDataset', False)
    response['steps'].append({**response['steps'][0], 'id': 's2', 'wave': 1,
                              'owner': 'data_engineering', 'output_type': 'DataReport'})
    monkeypatch.setattr(planner, 'call_model_json', lambda **kwargs: deepcopy(response))
    with pytest.raises(ValueError, match='DataOps'):
        planner.call_planner('g', {'target': 'dataops_crew'}, REGISTRY)
