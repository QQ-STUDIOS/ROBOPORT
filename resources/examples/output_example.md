# Example: JD-Crew FinalReport

Below is the kind of artifact a successful JD-Crew run produces. It conforms to `resources/schemas/output.schema.json#/definitions/FinalReport` and is what the Synthesizer emits.

```json
{
  "generated_at": "2026-04-25T17:32:00Z",
  "query": {
    "titles": ["Senior Data Engineer", "Staff Data Engineer"],
    "locations": ["Remote-US", "New York, NY"],
    "posted_within_days": 14
  },
  "summary": {
    "total_jobs": 4,
    "verdicts": {"apply": 2, "tailor_first": 1, "skip": 1, "research_more": 0}
  },
  "ranked_matches": [
    {
      "rank": 1,
      "job": {
        "id": "indeed-aH9k2",
        "title": "Staff Data Engineer",
        "company": "Acme Health",
        "location": "Remote-US",
        "source": "indeed",
        "source_url": "https://www.indeed.com/viewjob?jk=aH9k2",
        "posted_at": "2026-04-22"
      },
      "technical": {
        "job_id": "indeed-aH9k2",
        "stack": {
          "must_have":     ["Python", "Spark", "AWS", "dbt"],
          "nice_to_have":  ["Airflow", "Snowflake"],
          "buzzword_only": ["AI"]
        },
        "seniority_signal": "Staff",
        "team_shape": "Data platform team of 5, reports to Director of Data",
        "red_flags": [],
        "skills_overlap_with_profile": 0.85,
        "confidence": 0.9
      },
      "compliance": {
        "job_id": "indeed-aH9k2",
        "regulated_data": ["PHI", "PII"],
        "frameworks_implied": ["HIPAA", "SOC2"],
        "geographic_constraints": ["US-only"],
        "clearance_required": false,
        "findings": [
          {
            "kind": "HIPAA_exposure",
            "severity": "informational",
            "evidence": "JD: 'build pipelines that ingest de-identified patient records'",
            "citation_url": "https://www.indeed.com/viewjob?jk=aH9k2"
          }
        ],
        "confidence": 0.92
      },
      "match": {
        "job_id": "indeed-aH9k2",
        "fit_score": 0.84,
        "verdict": "apply",
        "reasoning_for": [
          "85% stack overlap; candidate has all four must-haves",
          "Staff seniority matches candidate's level",
          "HIPAA experience is a differentiator vs. typical applicants"
        ],
        "reasoning_against": [
          "Posted 3 days ago — competitive timing"
        ],
        "recommended_actions": [
          "Lead resume bullet 1 with Spark + dbt at scale",
          "Mention HIPAA pipeline experience in cover letter opener"
        ],
        "priority": 1
      }
    },
    {
      "rank": 2,
      "job": {
        "id": "linkedin-x4F1q",
        "title": "Senior Data Engineer",
        "company": "Bolt Telehealth",
        "location": "New York, NY",
        "source": "linkedin",
        "source_url": "https://linkedin.com/jobs/view/x4F1q",
        "posted_at": "2026-04-19"
      },
      "match": {
        "job_id": "linkedin-x4F1q",
        "fit_score": 0.71,
        "verdict": "tailor_first",
        "priority": 2,
        "reasoning_for": ["Strong stack overlap"],
        "reasoning_against": ["NYC on-site Tuesdays; candidate is Remote-US"],
        "recommended_actions": ["Confirm hybrid policy before applying"]
      }
    }
  ],
  "warnings": [
    "salary_estimator skipped for 1 job: insufficient corroborating sources"
  ]
}
```

## How to read it

- `ranked_matches` is the headline. Sorted by `priority` then `fit_score`.
- Every `compliance.findings[].evidence` is a literal quote from the JD — no paraphrasing, no invention.
- `warnings` carries soft failures from optional steps (salary estimation, etc.). The run still succeeded.

## What "good" looks like vs. "bad"

| Signal | Good | Bad |
|---|---|---|
| `findings[].evidence` | quotes the JD | summarizes the JD |
| `verdict` distribution | mostly `apply` + `tailor_first` for tight queries; mostly `skip` for loose ones | every match is `apply` (no skepticism) |
| `confidence` values | spread across the range, calibrated | all 0.9+ (uncalibrated) |
| `warnings` | named optional-step failures | empty even when the run had soft failures |
