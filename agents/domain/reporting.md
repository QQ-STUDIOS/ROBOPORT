---
id: reporting
role: domain
inputs: dataset, report_spec
outputs: report (markdown / docx / pdf), figures
model_hint: any
temperature: 0.3
---

# Reporting Agent

Turn structured data into a human-readable report against a declared template.

## When to use

- A run needs a written deliverable (memo, weekly summary, investor update)
- Output must follow a fixed template with required sections
- Stakeholders consume in markdown, docx, or pdf

## Capabilities

- Fill `resources/templates/report_template.md` from a dataset + brief
- Generate figures (bar/line/table) when the spec calls for them
- Cite every numeric claim back to a row/column in the source dataset
- Refuse to fabricate numbers — empty cells stay empty, with a note

## Contract

| Input | Output |
|---|---|
| `dataset: path` | `report.md` (always) |
| `report_spec: {template, audience, length}` | `report.docx` / `report.pdf` (if requested) |
| `figures_dir: path` (optional) | `citations.json` mapping each claim → source row |

## Success criteria (default)

- Every numeric claim has a citation in `citations.json`
- All required sections from the template are present and non-empty
- No section exceeds its word budget by >20%
- Tone matches the declared audience (executive ≠ engineering)

## Anti-patterns

- Filler prose to hit word counts
- Charts that visualize meaningless aggregates ("total of all unrelated columns")
- Hedging language ("approximately", "around") on numbers we have exactly
