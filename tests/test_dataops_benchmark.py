"""Provider-boundary doubles exercise actual planner/executor/benchmark plumbing."""
import copy
import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import benchmark
from roboport_runtime import client, executor, planner
from roboport_runtime.config import runtime_config_scope
from roboport_runtime.providers import Provider
from station_packs import load_registry


class ArtifactProvider(Provider):
    name = 'fault'
    config = {'defaults': {'context_tokens': 65536, 'max_tokens': 4096}}

    def __init__(self, *, tamper=False, bad_repair=False, bad_timestamp=False, step_ids=None):
        self.requests = []
        self.step_ids = step_ids
        self.tamper = tamper
        self.bad_timestamp = bad_timestamp
        self.bad_repair = bad_repair
        self.repair_calls = 0

    def chat_with_tools(self, **request):
        self.requests.append(copy.deepcopy(request))
        prompt = request['messages'][0]['content']
        if prompt.startswith('GOAL\n'):
            output = {'goal': 'Artifact-backed sales report', 'deliverable': 'DataReport',
                      'steps': [{'id': sid, 'owner': owner, 'wave': i, 'input': {},
                                 'output_type': kind, 'success_criteria': ['Output matches the authenticated artifact contract'], 'deterministic': False}
                                for i, (sid, owner, kind) in enumerate([
                                    ('clean', 'data_engineering', 'CleanedDataset'),
                                    ('report', 'reporting', 'DataReport')])]}
            if self.step_ids:
                for step, sid in zip(output['steps'], self.step_ids):
                    step['id'] = sid
        else:
            source_line = next(line for line in prompt.splitlines() if line.startswith('input: '))
            grounds = json.loads(source_line.removeprefix('input: '))['dataops_grounding']
            is_report = '\noutput_type: DataReport\n' in prompt
            artifact = copy.deepcopy(grounds['report' if is_report else 'dataset'])
            if request['messages'][-1]['content'].startswith('Your previous JSON'):
                self.repair_calls += 1
                # The actual faulty assistant JSON must reach the repair call.
                faulty = json.loads(request['messages'][-2]['content'])
                if not is_report:
                    assert 'columns' not in faulty['output']['schema_doc']
                else:
                    assert faulty['output']['sections'][0]['body'] == 'Revenue is 999999.'
                if self.bad_repair:
                    artifact['schema_doc'].pop('columns')
            if is_report and self.bad_timestamp:
                artifact.pop('generated_at', None)
            if is_report and self.tamper:
                artifact['sections'][0]['body'] = 'Revenue is 999999.'
            output = {'status': 'ok', 'output': artifact, 'criteria_results': [], 'error': None}
        return {'content': json.dumps(output), 'tool_calls': [],
                'usage': {'provider': 'fault', 'model': 'test-artifact', 'cost_usd': 0,
                          'prompt_tokens': 10, 'completion_tokens': 10, 'latency_ms': 1}}


def execute(tmp_path, monkeypatch, eval_id=1, **provider_options):
    provider = ArtifactProvider(**provider_options)
    # Wire the CLI's real runtime without bypassing planner/executor logic.
    monkeypatch.setattr(benchmark, 'call_planner', planner.call_planner)
    monkeypatch.setattr(benchmark, 'call_executor', executor.call_executor)
    monkeypatch.setattr(client, 'get_provider', lambda: provider)
    evals = json.loads((REPO / 'evals/dataops_evals.json').read_text())
    ev = {**next(e for e in evals['evals'] if e['id'] == eval_id), 'target': 'dataops_crew'}
    config = {'defaults': provider.config['defaults'], 'budgets': {'per_run': {'max_llm_calls': 10}}}
    with runtime_config_scope(config):
        outcome, grading = benchmark.execute_run(ev, tmp_path, load_registry(REPO), config=config,
                                                live=True, grade=False)
    assert grading is None
    return outcome, provider


@pytest.mark.parametrize('eval_id,expected_rows', [(1, 4), (2, 0), (4, 128)])
def test_real_artifacts_two_model_steps(tmp_path, monkeypatch, eval_id, expected_rows):
    outcome, provider = execute(tmp_path, monkeypatch, eval_id)
    assert outcome['status'] == 'ok', outcome
    assert len(provider.requests) == 3  # live planner and two live model agent calls
    cleaned = json.loads((tmp_path / 'steps/clean.json').read_text())
    report = json.loads((tmp_path / 'steps/report.json').read_text())
    artifact = Path(cleaned['output']['artifact_path'])
    assert artifact.is_file()
    with artifact.open(newline='') as stream:
        assert len(list(csv.DictReader(stream))) == expected_rows
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == cleaned['output']['artifact_sha256']
    assert report['output']['source_row_count'] == expected_rows
    assert cleaned['llm_calls'] == report['llm_calls'] == 1
    if expected_rows == 0:
        assert report['output']['figures'] == []
        assert 'No data' in report['output']['sections'][0]['body']
    assert json.loads((tmp_path / 'budget.json').read_text())['llm_calls'] == 3


def test_injected_fault_repaired_exactly_once_saved(tmp_path, monkeypatch):
    outcome, provider = execute(tmp_path, monkeypatch, 3)
    assert outcome['status'] == 'ok', outcome
    assert provider.repair_calls == 2
    assert len(provider.requests) == 6
    cleaned = json.loads((tmp_path / 'steps/clean.json').read_text())
    assert cleaned['fault_injection'] == {'kind': 'missing_schema_columns', 'count': 1}
    assert cleaned['repaired'] is True
    assert cleaned['llm_calls'] == 2
    assert cleaned['output']['schema_doc']['columns']
    assert (tmp_path / 'steps/report.json').is_file()


def test_wrong_numeric_claim_rejected(tmp_path, monkeypatch):
    outcome, provider = execute(tmp_path, monkeypatch, tamper=True)
    assert outcome['status'] == 'failed'
    assert outcome['step_failed'] == 'report'
    saved = json.loads((tmp_path / 'steps/report.json').read_text())
    assert saved['layer'] == 'schema_invalid'
    assert provider.repair_calls == 1  # constrained contract mismatch gets at most one repair


def test_failed_single_repair_never_reaches_reporting(tmp_path, monkeypatch):
    outcome, provider = execute(tmp_path, monkeypatch, 3, bad_repair=True)
    assert outcome['status'] == 'failed'
    assert outcome['step_failed'] == 'clean'
    assert provider.repair_calls == 2
    assert len(provider.requests) == 5
    assert not (tmp_path / 'steps/report.json').exists()
    failed = json.loads((tmp_path / 'steps/clean.json').read_text())
    assert failed['fault_injection']['count'] == 1 and failed['repair_attempts'] == 1


def test_failed_timestamp_repair_cannot_be_overwritten_by_grounding_pass(tmp_path, monkeypatch):
    outcome, provider = execute(tmp_path, monkeypatch, bad_timestamp=True)
    assert outcome['status'] == 'failed'
    assert outcome['step_failed'] == 'report'



@pytest.mark.parametrize('ids', [('same', 'same'), ('schema_failure_probe', 'report')])
def test_colliding_plan_ids_rejected_before_any_execution(tmp_path, monkeypatch, ids):
    outcome, provider = execute(tmp_path, monkeypatch, 3, step_ids=ids)
    assert outcome['status'] == 'failed'
    assert len(provider.requests) == 1
    assert not (tmp_path / 'steps').exists()


def test_offline_dataops_preserves_synthetic_mode(tmp_path):
    import subprocess
    result = subprocess.run([sys.executable, 'scripts/benchmark.py', '--target', 'dataops_crew',
                             '--eval-set', 'evals/dataops_evals.json', '--runs', '1', '--grade',
                             '--out', str(tmp_path / 'offline')], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
