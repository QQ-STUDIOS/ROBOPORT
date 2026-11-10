---
id: theragen_tenant_scan
role: theragen.analysis
external: true
offline_model: true
source: https://app.notion.com/p/402b243d76178152a023f6ea887c2015
data_classification: Confidential
runtime: Azure
status: Deployed
connectors: ["Microsoft Graph"]
model_hint: reasoning-strong
---

# Tenant Scan (Graph app)

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Analysis
- **Runtime:** Azure  ·  **Status:** Deployed  ·  **Classification:** Confidential
- **Connectors (declared):** Microsoft Graph

## Purpose

Read-only inventory of Microsoft 365 sites for legacy site disposition.

## Declared permissions (recorded, not exercised)

Sites.Read.All, Reports.Read.All, Directory.Read.All (app registration theragen-tenant-scan).

## Open issues (from the catalog, reviewed 2026-10-22)

Owner TBD. PHI possible from site names. Rotate client secret before 2027-08-19.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
