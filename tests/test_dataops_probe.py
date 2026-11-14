"""Actual executor schema-failure probe, with only provider I/O doubled."""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from roboport_runtime import client, dataops, executor
from roboport_runtime.config import runtime_config_scope
from roboport_runtime.providers import Provider
from station_packs import load_registry


class CopyDatasetProvider(Provider):
    name = 'fault'
    config = {'defaults': {'context_tokens': 65536, 'max_tokens': 4096}}

    def __init__(self):
        self.calls = []

    def chat_with_tools(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        prompt = kwargs['messages'][0]['content']
        line = next(line for line in prompt.splitlines() if line.startswith('input: '))
        ground = json.loads(line.removeprefix('input: '))['dataops_grounding']
        return {'content': json.dumps({'status': 'ok', 'output': ground['dataset'],
                                      'criteria_results': [], 'error': None}), 'tool_calls': []}


def test_persistent_probe_real_executor_one_repair_no_report(tmp_path, monkeypatch):
    ground = dataops.prepare_context({'data_source': {'path': 'resources/datasets/sales_messy.csv',
                                                     'synthetic': True, 'schema_fault': True}},
                                    tmp_path / 'artifacts')
    provider = CopyDatasetProvider()
    monkeypatch.setattr(client, 'get_provider', lambda: provider)
    with runtime_config_scope({'defaults': provider.config['defaults']}):
        result = dataops.run_failure_probe(ground, load_registry(REPO), executor.call_executor, tmp_path)
    assert result['expected_failure'] is True
    assert result['report_produced'] is False
    assert result['status'] == 'failed'
    assert result['layer'] == 'schema_invalid'
    assert result['repair_attempts'] == 1
    assert result['fault_injection']['count'] == 2
    assert result['llm_calls'] == 2
    assert len(provider.calls) == 2
    faulty_assistant = json.loads(provider.calls[1]['messages'][-2]['content'])
    assert 'columns' not in faulty_assistant['output']['schema_doc']
    assert all('output_type: CleanedDataset' in call['messages'][0]['content'] for call in provider.calls)
    saved = json.loads((tmp_path / 'steps/schema_failure_probe.json').read_text())
    assert saved == result
    assert 'persistent_schema_fault' not in ground
    assert 'columns' in ground['dataset']['schema_doc']
    assert not (tmp_path / 'steps/report.json').exists()


def test_probe_requires_controlled_fault(tmp_path):
    with pytest.raises(ValueError, match='controlled'):
        dataops.run_failure_probe({'schema_fault': False}, {}, lambda *a, **kw: pytest.fail('must not execute'), tmp_path)


def test_probe_does_not_accept_unobserved_expected_failure(tmp_path):
    ground = dataops.prepare_context({'data_source': {'path': 'resources/datasets/sales_messy.csv',
                                                     'synthetic': True, 'schema_fault': True}}, tmp_path / 'artifacts')
    with pytest.raises(ValueError, match='did not demonstrate'):
        dataops.run_failure_probe(ground, {}, lambda *a, **kw: {'status': 'ok', 'repair_attempts': 1}, tmp_path)
    saved = json.loads((tmp_path / 'steps/schema_failure_probe.json').read_text())
    assert saved['expected_failure'] is False


def test_incomplete_repair_never_counts_as_verified_probe(tmp_path):
    ground = dataops.prepare_context({'data_source': {'path': 'resources/datasets/sales_messy.csv',
                                                     'synthetic': True, 'schema_fault': True}}, tmp_path / 'artifacts')
    incomplete = {'status': 'failed', 'layer': 'schema_invalid', 'repair_attempts': 1,
                  'repaired': False, 'llm_calls': 1,
                  'fault_injection': {'kind': 'missing_schema_columns', 'count': 1}}
    with pytest.raises(ValueError, match='did not demonstrate'):
        dataops.run_failure_probe(ground, {}, lambda *a, **kw: incomplete, tmp_path)
    saved = json.loads((tmp_path / 'steps/schema_failure_probe.json').read_text())
    assert saved['expected_failure'] is False
