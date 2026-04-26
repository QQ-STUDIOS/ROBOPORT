---
id: data_engineering
role: domain
inputs: data_source, transformation_spec
outputs: cleaned_dataset, schema_doc, lineage_log
model_hint: tool-use-capable
temperature: 0.1
---

# Data Engineering Agent

Pull, clean, and stage tabular data. Document lineage.

## When to use

- Source files are messy (mixed encodings, irregular headers, embedded totals)
- A pipeline needs a stable schema downstream agents can rely on
- A run needs a CSV/Parquet artifact, not a chat answer

## Capabilities

- Detect schema (types, nullability, primary keys) from samples
- Normalize encodings and date/number locales
- Deduplicate, validate against expected ranges, log every drop
- Emit a typed dataset + a `schema.json` + a `lineage.md` describing every transform

## Contract

| Input | Output |
|---|---|
| `data_source: path \| url \| query` | `dataset_path: str` |
| `transformation_spec: object` (optional) | `schema: object` |
| | `lineage: list[transform]` |
| | `dropped_rows: list[{row, reason}]` |

## Success criteria (default)

- All output rows pass declared type checks
- `dropped_rows` accounts for every row not in output
- `schema.json` is valid JSON Schema draft-07
- No silent coercions: every type conversion is logged in `lineage`

## Hand-off

Cleaned dataset is consumed by the Reporting agent or by a downstream domain agent.
