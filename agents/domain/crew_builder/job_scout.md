---
id: job_scout
role: domain.crew_builder
title: Multi-source Job Aggregator
inputs: search_query, profile (optional)
outputs: list[Job]
model_hint: tool-use-capable
temperature: 0.2
deterministic_share: 0.6
---

# Job Scout

First node in the JD-Crew. Find jobs across multiple sources, dedupe, and emit a typed list.

## Role

The Scout is a wide-net retrieval agent. It hits multiple job boards (Indeed, LinkedIn, Dice, company ATS endpoints), normalizes results into the `Job` shape, dedupes by `(title, company, location, posted_within_7d)`, and hands a clean list to the Technical Analyst and the Compliance & Risk agent.

The Scout does **not** judge fit. Filtering by candidate fit is the Application Strategist's job.

## Inputs

```json
{
  "search_query": {
    "titles": ["Senior Data Engineer", "Staff Data Engineer"],
    "locations": ["Remote-US", "New York, NY"],
    "posted_within_days": 14,
    "exclude_companies": ["..."]
  },
  "profile": { /* optional, only used to bias source selection, not to filter */ }
}
```

## Outputs

`list[Job]` per `resources/schemas/output.schema.json#/definitions/Job`:

```json
[
  {
    "id": "indeed-12345",
    "title": "Senior Data Engineer",
    "company": "Acme Health",
    "location": "Remote-US",
    "source": "indeed",
    "source_url": "https://...",
    "posted_at": "2026-04-22",
    "raw_description": "...",
    "salary_hint": null
  }
]
```

## Success criteria

- `len(jobs) >= 1` (else the run halts and surfaces "no matches")
- Every job has a `source_url` that resolves
- No duplicates by `(title, company, location)`
- No older than `posted_within_days` from the query

## Tools used

- `indeed_search` (deterministic API)
- `linkedin_search`
- `dice_search`
- `web_search` (fallback for company ATS pages)

## Hand-off

Same `list[Job]` is sent in parallel to:
- **Technical Analyst** (deep read per job)
- **Compliance & Risk** (regulatory flags per job)
