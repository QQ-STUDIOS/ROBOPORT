"""Controlled live JD crew smoke, using supplied data rather than live sourcing."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def build_plan() -> dict:
    fixture = json.loads((REPO / 'tests/fixtures/deployment_jd.json').read_text(encoding='utf-8'))
    as_of = datetime.now(UTC).date()
    posting_dates = []
    for job in fixture['jobs']:
        age = (as_of - date.fromisoformat(job['posted_at'])).days
        posting_dates.append({'job_id': job['id'], 'posted_at': job['posted_at'],
                              'age_days': age, 'relation': 'past' if age > 0 else 'today' if age == 0 else 'future'})
    fixture['evaluation_context'] = {'as_of_date': as_of.isoformat(), 'timezone': 'UTC', 'posting_dates': posting_dates}
    factual_instruction = (' Use the frozen evaluation_context date and posting_dates, not model training dates, for temporal reasoning. '
                           'A posting on or before as_of_date is not future-dated. Posting recency does not prove a listing is active. '
                           'Candidate skills do not establish specific projects or years per skill. Do not recommend claiming data-pipeline '
                           'experience unless data pipelines is explicitly declared in candidate skills. Otherwise put this uncertainty '
                           'in reasoning_against and recommend verifying additional experience before claiming it.')
    plan = {'goal': 'Validate the JD crew with controlled fixture data and real model inference', 'deliverable': 'FinalReport',
            'estimated_llm_calls': 4, 'estimated_tool_calls': 0, 'fallback': 'fail on invalid boundary',
            'steps': [{'id': sid, 'owner': owner, 'wave': wave, 'output_type': output,
                       'input': {**fixture, 'instruction': 'This is controlled deployment fixture data, not an active job search. Analyze supplied data only. Preserve job IDs and fixture source. Do not fetch URLs or invent experience. Candidate facts are synthetic supplied inputs, not verified real qualifications. Do not name HIPAA, GDPR or SOC 2 in recommended_actions unless that exact capability is declared in candidate skills, certifications or compliance_frameworks. Discuss inferred job requirements and unknown candidate compliance experience in reasoning_against instead. Never advise claiming unverified familiarity. Fit scores are model judgment, not hiring probabilities.' + factual_instruction},
                       'allowed_tools': [], 'success_criteria': ['Produce schema-valid output from the supplied fixture'], 'deterministic': owner == 'synthesizer'}
                      for sid, owner, wave, output in [('scout', 'job_scout', 0, 'list[Job]'), ('technical', 'technical_analyst', 1, 'TechnicalAnalysis'),
                                                       ('compliance', 'compliance_risk', 1, 'ComplianceAnalysis'), ('strategy', 'application_strategist', 2, 'CandidateMatch'),
                                                       ('synth', 'synthesizer', 3, 'FinalReport')]]}

    from roboport_runtime.strategy_catalog import build_catalog
    strategy = plan['steps'][3]['input']
    strategy['strategy_catalog'] = build_catalog(strategy)
    strategy['instruction'] = ('Select and rank ONLY option IDs from strategy_catalog for the three reasoning/action arrays. Include every mandatory_unknowns ID in reasoning_against. Never output prose or extra fields; runtime compiles selected facts into CandidateMatch. Scores are model judgment; use supplied sources, not unsupported upstream claims.')
    return plan
