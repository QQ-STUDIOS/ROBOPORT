import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from roboport_runtime import dataops


def test_materialized_sales(tmp_path):
    dataset = dataops.prepare_dataset('resources/datasets/sales_messy.csv', tmp_path)
    assert dataset['row_count'] == 4
    assert dataset['dropped_totals_rows'] == 1
    assert dataset['missing_numeric_cells'] == 1
    assert dataops.dataset_facts(dataset) == {'row_count': 4, 'units_sold': 345, 'revenue': 43300.0,
                                           'weekly_revenue': {'2026-W01': 21500.0, '2026-W02': 21800.0}}
    report = dataops.build_report(dataset)
    assert dataops.validate_report(report, dataset) == []
    report['sections'][0]['body'] = 'Revenue is 999999.'
    assert dataops.validate_report(report, dataset)


def test_empty_fixture_halts(tmp_path):
    dataset = dataops.prepare_dataset('resources/datasets/sales_empty.csv', tmp_path)
    assert dataset['row_count'] == 0
    report = dataops.build_report(dataset)
    assert report['source_row_count'] == 0
    assert not report['figures']
    assert 'No data' in report['sections'][0]['body']


def test_real_128_rows_and_tampering(tmp_path):
    dataset = dataops.prepare_dataset('resources/datasets/sales_128.csv', tmp_path)
    assert dataset['row_count'] == 128
    assert len(list(csv.DictReader(Path(dataset['artifact_path']).open()))) == 128
    assert dataops.dataset_facts(dataset)['units_sold'] == 8256
    Path(dataset['artifact_path']).write_text('corrupt')
    with pytest.raises(ValueError, match='hash mismatch'):
        dataops.dataset_facts(dataset)


def test_fault_is_actual_missing_required_field(tmp_path):
    import json
    import jsonschema
    dataset = dataops.prepare_dataset('resources/datasets/sales_messy.csv', tmp_path)
    faulty = dataops.inject_schema_fault({'status': 'ok', 'output': dataset})
    schema = json.loads((REPO / 'resources/schemas/output.schema.json').read_text())
    contract = {'$ref': '#/definitions/CleanedDataset', 'definitions': schema['definitions']}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(faulty['output'], contract)
    jsonschema.validate(dataset, contract)
    assert 'columns' in dataset['schema_doc']


def test_source_unchanged_and_path_guard(tmp_path):
    source = REPO / 'resources/datasets/sales_messy.csv'
    before = source.read_bytes()
    dataops.prepare_dataset('resources/datasets/sales_messy.csv', tmp_path)
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match='fixtures'):
        dataops.prepare_dataset('../outside.csv', tmp_path)


def test_prepare_context_controls_synthetic_fault(tmp_path):
    context = {'data_source': {'path': 'resources/datasets/sales_messy.csv', 'schema_fault': True}}
    with pytest.raises(ValueError, match='synthetic'):
        dataops.prepare_context(context, tmp_path)
    context['data_source']['synthetic'] = True
    grounds = dataops.prepare_context(context, tmp_path)
    assert grounds['schema_fault'] is True
    assert grounds['dataset']['row_count'] == 4
    assert dataops.validate_report(grounds['report'], grounds['dataset']) == []


def test_schema_fault_restricted_to_controlled_fixture(tmp_path):
    context = {'data_source': {'path': 'resources/datasets/sales_128.csv',
                               'synthetic': True, 'schema_fault': True}}
    with pytest.raises(ValueError, match='controlled'):
        dataops.prepare_context(context, tmp_path)


def test_frozen_report_still_authenticates_csv(tmp_path):
    dataset = dataops.prepare_dataset('resources/datasets/sales_messy.csv', tmp_path)
    report = dataops.build_report(dataset)
    Path(dataset['artifact_path']).write_text('tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        dataops.validate_report(report, dataset, report)
