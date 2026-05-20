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

First node in the JD-Crew. Find jobs across multiple sources, dedupe, validate, and emit a typed list.

## Role

The Scout is a wide-net retrieval agent. It hits multiple job-board APIs (Greenhouse + Lever today; Workday/SuccessFactors planned), normalizes results into the `Job` shape, dedupes by `(title, company, location)`, **validates that every `source_url` resolves before handoff**, and hands a clean list to the Technical Analyst and the Compliance & Risk agent.

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
    "id": "gh-anthropic-12345",
    "title": "Senior Data Engineer",
    "company": "anthropic",
    "location": "Remote-US",
    "source": "greenhouse",
    "source_url": "https://boards.greenhouse.io/...",
    "posted_at": "2026-04-22",
    "raw_description": "",
    "salary_hint": null
  }
]
```

## Execution order

1. Call one or more search tools (`search_linkedin`, `search_indeed`, `search_company_careers`) to gather candidates.
2. Concatenate the `results` arrays.
3. Call `dedupe_jobs(jobs)` to drop `(title, company, location)` duplicates.
4. Call `validate_url_active(jobs)` to HEAD-check every `source_url` in parallel.
5. Drop any job whose `source_url` returned non-live (4xx/5xx/timeout/error). Append a warning to the run log for each dropped job: `"dead-link dropped: <id> <source_url> (status: <code>)"`.
6. Truncate to the caller's `limit` (default 25) and emit `list[Job]`.

This order is intentional: dedupe before validate, so a dead duplicate doesn't pull down its live twin. Validate before output, so downstream agents never see a broken URL.

## Success criteria

- `len(jobs) >= 1` when matches exist (else the run halts and surfaces "no matches")
- **Every emitted job's `source_url` was validated live** (HTTP 2xx or 3xx via `validate_url_active`)
- No duplicates by `(title, company, location)`
- No older than `posted_within_days` from the query
- Run log records dropped jobs (dead links, duplicates) so the upstream can audit shrinkage

## Tools used

- `search_linkedin` — Greenhouse aggregator across 31+ curated boards (LinkedIn has no free public API)
- `search_indeed` — alias to `search_linkedin` (Indeed has no free public API); source labelled distinctly so dedupe still works
- `search_company_careers` — single-company Greenhouse → Lever fallback by slug
- `fetch_url` — fetch a full JD page when needed (rare in Scout; usually a Technical Analyst concern)
- `dedupe_jobs` — drop `(title, company, location)` duplicates; pure Python
- `validate_url_active` — HEAD-check `source_url`s in parallel and drop dead ones before handoff; pure Python + HTTP

Workday and SuccessFactors aren't covered yet (per-tenant URLs, no free aggregator). If a user requests a Workday-hosted company, fall back to `search_company_careers` and let it return `not-found`; surface that to the planner rather than fabricating jobs.

## Hand-off

Same `list[Job]` is sent in parallel to:
- **Technical Analyst** (deep read per job)
- **Compliance & Risk** (regulatory flags per job)
