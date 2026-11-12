"""Private, source-bound strategy selection; public CandidateMatch remains text."""
import hashlib
import json
import math
from datetime import date

from jsonschema import Draft7Validator

FIELDS = ('reasoning_for', 'reasoning_against', 'recommended_actions')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def build_catalog(inputs):
    candidate, jobs, context = (inputs[k] for k in ('candidate_profile', 'jobs', 'evaluation_context'))
    if len(jobs) != 1 or context['timezone'] != 'UTC':
        raise ValueError('Fact-bound strategy requires one supplied job and frozen UTC date')
    job = jobs[0]
    if job.get('source') != 'controlled-deployment-fixture' or not any(isinstance(v, str) and 'synthetic candidate' in v.casefold() for v in candidate.get('limitations', [])):
        raise ValueError('Fact-bound catalogue is limited to the controlled synthetic deployment fixture')
    as_of, posted = date.fromisoformat(context['as_of_date']), date.fromisoformat(job['posted_at'])
    skills = candidate['skills']
    if not skills or not all(isinstance(s, str) and s.strip() for s in skills):
        raise ValueError('Declared skills must be non-empty strings')
    options = {field: {} for field in FIELDS}

    def add(field, key, text, *sources):
        options[field][key] = {'text': text, 'source_fields': list(sources)}

    for index, skill in enumerate(skills):
        add('reasoning_for', f'skill_{index}', f'Supplied candidate skills declare {skill}; project experience and proficiency are not independently verified.', f'candidate_profile.skills[{index}]')
    add('reasoning_for', 'total_experience', f'Supplied candidate profile reports {candidate["experience_years"]} total years of experience; this does not establish years per skill.', 'candidate_profile.experience_years')
    add('reasoning_for', 'location_authorization', f'Supplied candidate location: {candidate["location"]}; supplied work authorization: {candidate["work_authorization"]}. These declarations do not independently establish eligibility.', 'candidate_profile.location', 'candidate_profile.work_authorization')
    relation = 'past' if posted < as_of else 'today' if posted == as_of else 'future'
    add('reasoning_for', 'posting_date', f'Supplied posting date {posted.isoformat()} is {relation} relative to frozen evaluation date {as_of.isoformat()} UTC; listing activity is unverified.', 'jobs[0].posted_at', 'evaluation_context.as_of_date', 'evaluation_context.timezone')
    add('reasoning_against', 'skill_tenure_unknown', 'Years per skill are not established by the supplied skills and total experience; meeting specific tenure requirements remains unverified.', 'candidate_profile.skills', 'candidate_profile.experience_years', 'jobs[0].raw_description')
    add('reasoning_against', 'pipeline_unknown', 'Data-pipeline project experience is not established by a skills declaration; verify it before any experience claim.', 'candidate_profile.skills', 'jobs[0].raw_description')
    add('reasoning_against', 'compliance_unknown', 'Job requirements or inferred compliance obligations do not establish candidate compliance experience or credentials; verify them separately.', 'candidate_profile', 'jobs[0].raw_description')
    add('reasoning_against', 'listing_unknown', 'This evaluation uses synthetic deployment inputs. Listing activity and real candidate qualifications are unverified; no application is authorized by this evaluation.', 'candidate_profile.limitations', 'jobs[0].source')
    add('recommended_actions', 'highlight_skills', 'Describe only the supplied declared skills; do not invent project experience or tenure.', 'candidate_profile.skills')
    add('recommended_actions', 'verify_tenure', 'Verify years per required skill before claiming the job tenure requirement is met.', 'candidate_profile.experience_years', 'candidate_profile.skills', 'jobs[0].raw_description')
    add('recommended_actions', 'verify_pipeline', 'Verify data-pipeline project experience before claiming it; omit unsupported experience from any resume.', 'candidate_profile.skills', 'jobs[0].raw_description')
    add('recommended_actions', 'verify_compliance', 'Verify compliance experience and credentials before claiming familiarity with any job-related framework.', 'candidate_profile', 'jobs[0].raw_description')
    add('recommended_actions', 'fixture_only', 'Use this synthetic listing only for deployment evaluation; verify a real listing and qualifications before considering an application.', 'jobs[0].source', 'candidate_profile.limitations')
    body = {'version': 1, 'job_id': job['id'], 'source_sha256': digest({'candidate_profile': candidate, 'jobs': jobs, 'evaluation_context': context}), 'options': options,
            'mandatory_unknowns': list(options['reasoning_against'])}
    return {**body, 'catalog_sha256': digest(body)}


def bound_catalog(inputs):
    expected = build_catalog(inputs)
    if inputs['strategy_catalog'] != expected:
        raise ValueError('Strategy catalogue does not match supplied source facts')
    return expected


def selection_schema(catalog):
    properties = {'job_id': {'const': catalog['job_id']}, 'fit_score': {'type': 'number', 'minimum': 0, 'maximum': 1},
                  'verdict': {'enum': ['apply', 'tailor_first', 'skip', 'research_more']}, 'priority': {'type': 'integer', 'minimum': 1, 'maximum': 5}}
    for field in FIELDS:
        properties[field] = {'type': 'array', 'items': {'enum': list(catalog['options'][field])}, 'minItems': 1, 'uniqueItems': True}
    properties['reasoning_against']['allOf'] = [{'contains': {'const': key}} for key in catalog['mandatory_unknowns']]
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def selection_issues(catalog, selection):
    errors = [error.message for error in Draft7Validator(selection_schema(catalog)).iter_errors(selection)]
    score = selection.get('fit_score') if isinstance(selection, dict) else None
    if isinstance(score, float) and not math.isfinite(score):
        errors.append('Fit score must be finite')
    return errors


def compile_selection(inputs, selection):
    catalog = bound_catalog(inputs)
    errors = selection_issues(catalog, selection)
    if errors:
        raise ValueError('; '.join(errors))
    output = {key: selection[key] for key in ('job_id', 'fit_score', 'verdict', 'priority')}
    output.update({field: [catalog['options'][field][key]['text'] for key in selection[field]] for field in FIELDS})
    evidence = {key: catalog[key] for key in ('version', 'source_sha256', 'catalog_sha256')}
    evidence.update(selection=selection, selected_sources={field: [catalog['options'][field][key] for key in selection[field]] for field in FIELDS})
    return output, evidence
