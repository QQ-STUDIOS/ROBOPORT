"""Deterministic JD report join. Missing or duplicate boundaries fail loudly."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime


def synthesize(jobs: list[dict], technical: list[dict], compliance: list[dict], matches: list[dict]) -> dict:
    if not jobs:
        raise ValueError('missing jobs for synthesis')

    def index(rows, key):
        result = {}
        for row in rows:
            identifier = row[key]
            if identifier in result:
                raise ValueError(f'duplicate boundary: {identifier}')
            result[identifier] = row
        return result

    job_map = index(jobs, 'id')
    maps = [index(rows, 'job_id') for rows in (technical, compliance, matches)]
    for mapping in maps:
        if set(mapping) != set(job_map):
            raise ValueError('missing or unexpected job boundary in synthesis')
    rows = [{'job': job, 'technical': maps[0][jid], 'compliance': maps[1][jid], 'match': maps[2][jid]} for jid, job in job_map.items()]
    rows.sort(key=lambda row: row['job'].get('posted_at', ''), reverse=True)
    rows.sort(key=lambda row: (row['match']['priority'], -row['match']['fit_score']))
    for rank, row in enumerate(rows, 1):
        row['rank'] = rank
    return {'generated_at': datetime.now(UTC).isoformat(), 'summary': {'total_jobs': len(jobs), 'verdicts': dict(Counter(row['match']['verdict'] for row in rows))},
            'ranked_matches': rows, 'warnings': []}


def from_accumulated(accumulated: dict) -> dict:
    rows = []
    for value in accumulated.values():
        rows.extend(value if isinstance(value, list) else [value])
    jobs = [r for r in rows if isinstance(r, dict) and 'id' in r and 'title' in r]
    technical = [r for r in rows if isinstance(r, dict) and 'job_id' in r and 'stack' in r]
    compliance = [r for r in rows if isinstance(r, dict) and 'job_id' in r and 'regulated_data' in r]
    matches = [r for r in rows if isinstance(r, dict) and 'job_id' in r and 'fit_score' in r]
    return synthesize(jobs, technical, compliance, matches)
