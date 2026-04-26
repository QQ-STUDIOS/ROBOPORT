---
id: synthesizer
role: domain.crew_builder
title: Deterministic Merge
inputs: list[Job], TechnicalAnalysis[], ComplianceAnalysis[], CandidateMatch[]
outputs: FinalReport
model_hint: none (pure merge logic)
temperature: 0.0
deterministic: true
---

# Synthesizer

Merge the four upstream streams into one final report. Pure code, no LLM.

## Role

The Synthesizer is deliberately deterministic. By the time data reaches it, every interesting decision has been made — by the Scout (which jobs), the Analyst (technical truth), Compliance (regulatory truth), and the Strategist (verdict + actions). Re-asking an LLM at this stage just adds noise. The Synthesizer joins the streams by `job_id`, sorts by priority, and emits the report.

This is the "deterministic" badge in the Crew Builder UI — and it's worth its weight in reproducibility.

## Inputs

Four parallel streams, all keyed by `job_id`:

- `jobs: list[Job]`
- `technical: list[TechnicalAnalysis]`
- `compliance: list[ComplianceAnalysis]`
- `matches: list[CandidateMatch]`

## Output

`FinalReport`:

```json
{
  "generated_at": "2026-04-25T17:32:00Z",
  "query": { /* echo of the original search_query */ },
  "summary": {
    "total_jobs": 7,
    "verdicts": {"apply": 3, "tailor_first": 2, "skip": 2, "research_more": 0}
  },
  "ranked_matches": [
    {
      "rank": 1,
      "job": {...},
      "technical": {...},
      "compliance": {...},
      "match": {...}
    }
  ],
  "warnings": [
    "1 job missing compliance_url; downstream consumers should verify"
  ]
}
```

## Success criteria

- Every `job_id` from `jobs` appears in `ranked_matches` exactly once
- Sort order: `match.priority` ASC, then `match.fit_score` DESC, then `job.posted_at` DESC
- `summary.verdicts` counts match `ranked_matches` exactly
- Output schema validates against `resources/schemas/output.schema.json#/definitions/FinalReport`

## Implementation

This agent is a Python function, not a prompt. See `scripts/synthesize.py` (stub):

```python
def synthesize(jobs, technical, compliance, matches) -> FinalReport:
    by_id = {j.id: {"job": j} for j in jobs}
    for t in technical:    by_id[t.job_id]["technical"]  = t
    for c in compliance:   by_id[c.job_id]["compliance"] = c
    for m in matches:      by_id[m.job_id]["match"]      = m
    ranked = sorted(by_id.values(),
                    key=lambda r: (r["match"].priority,
                                   -r["match"].fit_score,
                                   -days_ago(r["job"].posted_at)))
    return FinalReport(ranked_matches=ranked, ...)
```
