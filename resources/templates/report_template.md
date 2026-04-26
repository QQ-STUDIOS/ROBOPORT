# {{report_title}}

**Generated:** {{generated_at}}
**Audience:** {{audience}}
**Run:** `{{run_id}}`

---

## TL;DR

> {{tldr_one_paragraph}}

Three takeaways:
1. {{takeaway_1}}
2. {{takeaway_2}}
3. {{takeaway_3}}

---

## Context

{{context_paragraph}}

What we set out to answer:
- {{question_1}}
- {{question_2}}

---

## Findings

### Finding 1 — {{finding_1_title}}

{{finding_1_body}}

> Source: {{finding_1_citation}}

### Finding 2 — {{finding_2_title}}

{{finding_2_body}}

> Source: {{finding_2_citation}}

---

## Numbers

| Metric | Value | Source |
|---|---:|---|
| {{metric_1}} | {{metric_1_value}} | `{{metric_1_source}}` |
| {{metric_2}} | {{metric_2_value}} | `{{metric_2_source}}` |
| {{metric_3}} | {{metric_3_value}} | `{{metric_3_source}}` |

Every number above is cited back to a row in the source dataset. See `citations.json` for the row-level mapping.

---

## Recommendation

{{recommendation_body}}

**Priority:** {{priority}}
**Owner:** {{owner}}
**Decision needed by:** {{decision_date}}

---

## Caveats

- {{caveat_1}}
- {{caveat_2}}

---

## Appendix

- Methodology: {{methodology_link}}
- Raw outputs: `runs/{{run_id}}/`
- Reproducibility: `python scripts/replay.py --run {{run_id}}`
