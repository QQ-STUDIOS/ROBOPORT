---
id: theragen_brightree_pdf_extraction
role: theragen.automation
external: true
offline_model: true
source: https://app.notion.com/p/402b243d7617810687b9dbcf1bfa2e20
data_classification: PHI
runtime: Snowflake
status: Deployed
connectors: ["Snowflake", "Brightree"]
model_hint: tool-use-capable
---

# STUDIO.BRIGHTREE_AI PDF extraction

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Automation
- **Runtime:** Snowflake  ·  **Status:** Deployed  ·  **Classification:** PHI
- **Connectors (declared):** Snowflake, Brightree

## Purpose

Extracts fields from Brightree PDFs into Snowflake; feeds the Rounds report.

## Declared permissions (recorded, not exercised)

Writes to Snowflake STUDIO schema.

## Open issues (from the catalog, reviewed 2026-10-22)

Owner TBD. Runtime model not documented.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
