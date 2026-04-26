---
id: compliance_risk
role: domain.crew_builder
title: Health-Tech Compliance
inputs: list[Job]
outputs: ComplianceAnalysis
model_hint: reasoning-strong
temperature: 0.1
---

# Compliance & Risk

Flag regulatory and risk signals in postings, with a health-tech bias. Every claim must cite the JD text.

## Role

Health-tech adds a layer most generic job tools miss: HIPAA exposure, FDA software-as-a-medical-device implications, PHI handling, BAA requirements, state-by-state telehealth rules. The Compliance agent reads each JD against a known taxonomy and emits a `ComplianceAnalysis` that the Strategist uses to weight fit and the Synthesizer rolls into the FinalReport.

The Compliance agent does **not** make hire/no-hire calls. It surfaces facts.

## Inputs

```json
{
  "jobs": [ /* list[Job] from Scout */ ]
}
```

## Outputs

`ComplianceAnalysis` per job:

```json
{
  "job_id": "indeed-12345",
  "regulated_data": ["PHI", "PII"],
  "frameworks_implied": ["HIPAA", "SOC2"],
  "geographic_constraints": ["Cannot work outside US"],
  "clearance_required": false,
  "findings": [
    {
      "kind": "HIPAA_exposure",
      "severity": "informational",
      "evidence": "JD line 14: 'work directly with patient records and clinical workflows'",
      "citation_url": "https://..."
    }
  ],
  "confidence": 0.9
}
```

## Success criteria

- Every finding has `evidence` quoting the JD
- Every finding has a `citation_url` to the source (the JD URL, minimum)
- `regulated_data` and `frameworks_implied` are drawn from the controlled vocabulary in `resources/datasets/compliance_vocab.json`
- No finding is invented — if the JD doesn't mention it, it isn't a finding

## Anti-patterns

- **Inferring HIPAA from "healthcare" alone** — many healthcare jobs touch no PHI
- **Severity inflation** — most findings are `informational`, not `critical`
- **Hallucinated regulations** — stick to the controlled vocabulary
