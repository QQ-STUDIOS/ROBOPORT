---
id: theragen_legacyrag_retrieval
role: theragen.research
external: true
offline_model: true
source: https://app.notion.com/p/402b243d7617812aa7bde80f14ec9153
data_classification: PHI
runtime: Local
status: Paused
connectors: ["SQLite", "Qdrant", "Ollama"]
model_hint: reasoning-strong
---

# LegacyRAG retrieval

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Research
- **Runtime:** Local  ·  **Status:** Paused  ·  **Classification:** PHI
- **Connectors (declared):** SQLite, Qdrant, Ollama

## Purpose

Search and Q&A over the legacy document catalog.

## Declared permissions (recorded, not exercised)

Local SQLite catalog and Qdrant index; nomic-embed-text embeddings.

## Open issues (from the catalog, reviewed 2026-10-22)

Owner TBD. Restore not yet verified; transfer copy sits in OneDrive; 1.18M catalogued vs 335k document count gap.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
