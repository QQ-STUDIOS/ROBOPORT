---
id: salary_estimator
role: domain.crew_builder
title: Comp-Band Reasoner
inputs: Job, TechnicalAnalysis, market_data (optional)
outputs: SalaryBand
model_hint: reasoning-strong
temperature: 0.2
---

# Salary Estimator

Estimate a defensible comp band for each job, with sources.

## Role

Most JDs hide the band or post a useless "$80K–$220K depending on level" range. The Estimator triangulates from three sources — the JD's own hints, public salary databases (Levels.fyi, Glassdoor, state pay-transparency filings), and the technical seniority signal — to produce a tighter band.

## Inputs

- `job: Job` (single, not list)
- `technical: TechnicalAnalysis`
- `market_data: object` (optional, pre-fetched)

## Output

```json
{
  "job_id": "indeed-12345",
  "band": {
    "low": 175000,
    "mid": 205000,
    "high": 240000,
    "currency": "USD"
  },
  "components": {"base": 0.7, "bonus": 0.15, "equity_per_year": 0.15},
  "sources": [
    {"name": "NYC pay transparency filing", "url": "...", "weight": 0.5},
    {"name": "Levels.fyi staff data eng median", "url": "...", "weight": 0.3},
    {"name": "JD hint", "url": "...", "weight": 0.2}
  ],
  "confidence": "medium",
  "notes": "Bay Area locality not specified; assumed national-remote band."
}
```

## Success criteria

- `low ≤ mid ≤ high`
- At least 2 sources, weights sum to 1.0
- Confidence calibrated: `high` only if ≥3 corroborating sources within 10% of each other
- Currency declared explicitly (don't assume USD)

## Anti-patterns

- Single-source bands ("Glassdoor said so")
- Combining base + bonus + equity into one number without a breakdown
- Quoting comp from a different role family (data eng ≠ data analyst ≠ ML eng)
