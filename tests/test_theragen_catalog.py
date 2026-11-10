"""Frontier 3 — the Theragen agent catalog.

The real Theragen agents are imported as OFFLINE, typed-contract records under the
registry's `theragen_agents` key — deliberately separate from ROBOPORT's runnable
`agents`, and never connected to any production system or PHI. These tests prove the
catalog is well-formed, each record has a matching offline spec, the external agents
do not leak into the runnable set, and governance stays clean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import governance  # noqa: E402

REGISTRY = json.loads((REPO / "agents" / "registry.json").read_text())
CATALOG = REGISTRY.get("theragen_agents", {})

REQUIRED_FIELDS = ("path", "name", "role", "external", "offline_model",
                   "data_classification", "runtime", "status", "model_hint", "source")
ALLOWED_CLASSIFICATION = {"PHI", "Confidential", "Internal", "Unclassified", "Public"}


def test_catalog_present_and_populated():
    assert len(CATALOG) == 14                      # all 14 Notion '🧠 1) Agents' rows


def test_catalog_is_separate_from_runnable_agents():
    runnable = set(REGISTRY["agents"])
    catalog = set(CATALOG)
    assert runnable.isdisjoint(catalog)            # no external catalog agent leaks into runnable
    assert len(runnable) == 19                      # runnable set stays fixed as crews are modeled
    # A catalog agent MAY be a modeled-crew station (frontier 4), but it is still never
    # executable: every crew station that is a catalog agent resolves only via theragen_agents.
    for crew in REGISTRY["crews"].values():
        stations = {crew["entry"]} | {e["from"] for e in crew["edges"]} | {e["to"] for e in crew["edges"]}
        for st in stations:
            assert st in runnable or st in catalog   # every station resolves to a registry citizen
            if st in catalog:
                assert st not in runnable            # a catalog station is never also runnable


@pytest.mark.parametrize("key", sorted(CATALOG))
def test_record_well_formed(key):
    rec = CATALOG[key]
    assert key.startswith("theragen_")
    for f in REQUIRED_FIELDS:
        assert f in rec and rec[f] not in (None, ""), f"{key} missing {f}"
    assert rec["external"] is True and rec["offline_model"] is True
    assert rec["role"].startswith("theragen.")
    assert rec["data_classification"] in ALLOWED_CLASSIFICATION
    assert rec["source"].startswith("https://app.notion.com/")


@pytest.mark.parametrize("key", sorted(CATALOG))
def test_spec_file_exists_and_is_marked_offline(key):
    rec = CATALOG[key]
    spec = REPO / rec["path"]
    assert spec.is_file(), f"missing spec {rec['path']}"
    text = spec.read_text(encoding="utf-8")
    assert f"id: {key}" in text                     # frontmatter id matches registry key
    assert "offline_model: true" in text
    assert "Offline model — not a runnable ROBOPORT agent." in text   # the safety banner


def test_catalog_doc_lists_every_agent():
    doc = (REPO / "docs" / "THERAGEN_AGENTS.md").read_text(encoding="utf-8")
    for rec in CATALOG.values():
        assert rec["name"] in doc
        assert rec["path"] in doc                   # each row links its spec


def test_governance_stays_clean_with_catalog():
    # the catalog is pure reference — it must not perturb the Phase-5 gate
    eval_sets = governance.discover_eval_sets(REPO / "evals")
    report = governance.run_governance(eval_sets, REGISTRY)
    assert report["ok"], report["violations"]


def test_phi_agents_declare_their_classification():
    # the PHI-classified agents (Brightree, LegacyRAG) must carry the label, so a
    # later modeled crew can be gated on it — the label is metadata, not data.
    phi = {k for k, v in CATALOG.items() if v["data_classification"] == "PHI"}
    assert "theragen_brightree_pdf_extraction" in phi
    assert "theragen_legacyrag_retrieval" in phi


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
