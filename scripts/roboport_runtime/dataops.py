"""Artifact-backed, read-only sales DataOps transformations and report evidence.

Only synthetic repository CSV inputs are read; normalized artifacts are written
under the caller's run directory. No source file is changed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COLUMNS = [('week', 'string'), ('region', 'string'), ('units_sold', 'integer'), ('revenue', 'number')]


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_dataset(input_path, artifact_dir):
    """Materialize normalized CSV and return its auditable typed contract."""
    source = (REPO / input_path).resolve()
    if not source.is_relative_to(REPO / 'resources' / 'datasets'):
        raise ValueError('DataOps inputs must be repository dataset fixtures')
    raw = source.read_bytes()
    text = raw.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    headers = next(reader)
    normalized_headers = [h.strip().lower().replace(' ', '_').replace('($)', '').rstrip('_') for h in headers]
    if normalized_headers != [x[0] for x in COLUMNS]:
        raise ValueError(f'unsupported CSV columns: {headers!r}')
    rows, dropped, missing = [], 0, 0
    for row in reader:
        if not row or not any(v.strip() for v in row):
            continue
        if len(row) != 4:
            raise ValueError('CSV row width does not match header')
        if row[0].strip().upper() == 'TOTAL':
            dropped += 1
            continue
        week, region, units, revenue = [v.strip() for v in row]
        units_value = str(int(units.replace(',', ''))) if units else ''
        revenue_value = format(Decimal(revenue.replace(',', '')), 'f') if revenue else ''
        missing += int(not units_value) + int(not revenue_value)
        rows.append([week, region.lower(), units_value, revenue_value])
    destination = Path(artifact_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / 'normalized-sales.csv'
    if path == source:
        raise ValueError('artifact must not overwrite source')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                                         dir=destination, delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow([x[0] for x in COLUMNS])
            writer.writerows(rows)
        # Replace the entry itself, never follow a pre-existing symbolic/hard link.
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return {
        'schema_doc': {'columns': [{'name': n, 'dtype': t} for n, t in COLUMNS]},
        'row_count': len(rows), 'artifact_path': str(path),
        'artifact_sha256': _hash(path), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_path': str(source), 'dropped_totals_rows': dropped, 'missing_numeric_cells': missing,
        'lineage_log': [f'load: {source.name}; sha256={hashlib.sha256(raw).hexdigest()}',
                        'header-normalization: trim, lowercase, underscores, remove currency marker',
                        'value-normalization: trim whitespace, lowercase region, parse numeric thousands separators; preserve null numeric cells',
                        f'totals-row drop: {dropped}', f'normalized CSV: sha256={_hash(path)}; rows={len(rows)}'],
    }


def dataset_facts(dataset):
    """Reopen and authenticate the CSV before deriving any report number."""
    path = Path(dataset['artifact_path'])
    if _hash(path) != dataset['artifact_sha256']:
        raise ValueError('normalized artifact hash mismatch')
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != [n for n, _ in COLUMNS]:
            raise ValueError('normalized artifact columns mismatch')
        rows = list(reader)
    if len(rows) != dataset['row_count']:
        raise ValueError('normalized artifact row count mismatch')
    weekly = {}
    for row in rows:
        weekly.setdefault(row['week'], Decimal(0))
        weekly[row['week']] += Decimal(row['revenue']) if row['revenue'] else 0
    return {'row_count': len(rows),
            'units_sold': sum(int(r['units_sold']) for r in rows if r['units_sold']),
            'revenue': float(sum((Decimal(r['revenue']) for r in rows if r['revenue']), Decimal(0))),
            'weekly_revenue': {k: float(v) for k, v in sorted(weekly.items())}}


def build_report(dataset):
    facts = dataset_facts(dataset)
    if not facts['row_count']:
        return {'generated_at': datetime.now(timezone.utc).isoformat(),
                'title': 'Weekly sales report', 'source_row_count': 0,
                'sections': [{'heading': 'Summary', 'body': 'No data: empty input; reporting halted.'},
                             {'heading': 'Trends', 'body': 'No trends available.'},
                             {'heading': 'Data Quality', 'body': '; '.join(dataset['lineage_log'])}],
                'figures': [], 'artifact_sha256': dataset['artifact_sha256']}
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'title': 'Weekly sales report',
            'source_row_count': facts['row_count'],
            'sections': [
                {'heading': 'Summary', 'body': f"Rows: {facts['row_count']}; known units sold: {facts['units_sold']}; revenue: {facts['revenue']}."},
                {'heading': 'Trends', 'body': 'Weekly revenue: ' + json.dumps(facts['weekly_revenue'], sort_keys=True)},
                {'heading': 'Data Quality', 'body': f"Missing numeric cells preserved: {dataset['missing_numeric_cells']}; totals rows excluded: {dataset['dropped_totals_rows']}. Transformations: " + '; '.join(dataset['lineage_log'])}],
            'figures': [{'column': 'units_sold', 'aggregation': 'sum', 'value': facts['units_sold']},
                        {'column': 'revenue', 'aggregation': 'sum', 'value': facts['revenue']}],
            'artifact_sha256': dataset['artifact_sha256']}


def validate_dataset(output, expected):
    dataset_facts(expected)  # authenticate the materialized evidence before comparison
    return [f'DataOps artifact mismatch: {key}' for key in expected if output.get(key) != expected[key]]


def validate_report(output, dataset, expected_report=None):
    dataset_facts(dataset)  # cached report comparisons still reauthenticate the CSV
    expected = expected_report or build_report(dataset)
    # Require exact trusted content: merely matching number tokens cannot prove
    # that a model's narrative makes the correct claims about those numbers.
    keys = list(expected) if expected_report is not None else ['title', 'sections', 'source_row_count', 'figures', 'artifact_sha256']
    return [f'DataOps report grounding mismatch: {key}' for key in keys
            if output.get(key) != expected[key]]


def inject_schema_fault(result):
    """Remove a genuinely required field before the normal schema validator."""
    import copy
    faulty = copy.deepcopy(result)
    faulty['output']['schema_doc'].pop('columns', None)
    return faulty


def prepare_context(context, artifact_dir):
    """Prepare explicit CSV source evidence for live model planning/execution.

    The schema-fault switch is only admitted for explicitly synthetic inputs;
    the executor, not this function, performs the single boundary injection.
    """
    source = context.get('data_source')
    if not isinstance(source, dict) or not isinstance(source.get('path'), str):
        raise ValueError('DataOps context requires data_source.path')
    fault = source.get('schema_fault', False)
    if not isinstance(fault, bool):
        raise ValueError('schema_fault must be boolean')
    if fault and source.get('synthetic') is not True:
        raise ValueError('schema fault injection requires explicit synthetic source')
    if fault and (REPO / source['path']).resolve() != (REPO / 'resources/datasets/sales_messy.csv').resolve():
        raise ValueError('schema fault injection is restricted to the controlled sales_messy fixture')
    dataset = prepare_dataset(source['path'], artifact_dir)
    return {'dataset': dataset, 'report': build_report(dataset), 'schema_fault': fault,
            'facts': dataset_facts(dataset)}


def run_failure_probe(ground, registry, executor_callback, run_dir):
    """Exercise the actual single-repair failure branch in an isolated step.

    This controlled synthetic probe never schedules reporting. Its expected
    schema failure is evidence about containment, not a failed primary run.
    """
    from copy import deepcopy
    if ground.get('schema_fault') is not True:
        raise ValueError('persistent-fault probe requires controlled schema-fault grounding')
    if Path(ground['dataset']['source_path']).resolve() != (REPO / 'resources/datasets/sales_messy.csv').resolve():
        raise ValueError('persistent-fault probe requires the controlled sales_messy fixture')
    probe_ground = deepcopy(ground)
    probe_ground['persistent_schema_fault'] = True
    step = {'id': 'schema_failure_probe', 'owner': 'data_engineering', 'wave': 0,
            'input': {'dataops_grounding': probe_ground,
                      'instruction': 'Return the supplied dataset contract exactly. This isolated controlled synthetic probe exercises the schema-repair failure containment path; do not produce a report.'},
            'output_type': 'CleanedDataset', 'success_criteria': [],
            'deterministic': False, 'allowed_tools': []}
    result = executor_callback(step, {}, registry, runtime_dataops_grounding=probe_ground)
    expected = (result.get('status') == 'failed' and result.get('layer') == 'schema_invalid'
                and result.get('repair_attempts') == 1 and result.get('repaired') is True
                and result.get('fault_injection', {}).get('count') == 2
                and result.get('llm_calls') == 2)
    record = {**result, 'expected_failure': expected, 'report_produced': False,
              'probe_kind': 'persistent_missing_schema_columns'}
    directory = Path(run_dir) / 'steps'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'schema_failure_probe.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    if not expected:
        raise ValueError('Persistent-fault probe did not demonstrate schema-invalid containment after exactly one repair')
    return record
