"""Frontier 4a — the modeled Theragen crews (read-only reporting flows).

Brightree->Rounds (PHI) and Tenant-Scan (Confidential) are OFFLINE models of real
Theragen workflows: a catalog agent (external, never executed) feeds ROBOPORT's
reporting agent. These tests prove the crews are registered and governed, the
catalog-station recognition works (and R4 still bites a genuinely unknown station),
and the new typed contracts hold.
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
SCHEMA = json.loads((REPO / "resources" / "schemas" / "output.schema.json").read_text())

jsonschema = pytest.importorskip("jsonschema")


# --- registry / governance ---------------------------------------------------

def test_both_crews_registered_with_catalog_entry_stations():
    for name, entry, edge_type, cls in [
        ("brightree_rounds_crew", "theragen_brightree_pdf_extraction", "ExtractedRecords", "PHI"),
        ("tenant_scan_crew", "theragen_tenant_scan", "SiteInventory", "Confidential"),
    ]:
        crew = REGISTRY["crews"][name]
        assert crew["entry"] == entry
        assert crew["classification"] == cls
        assert crew["edges"][0]["from"] == entry
        assert crew["edges"][0]["to"] == "reporting"       # terminal is the runnable reporter
        assert crew["edges"][0]["type"] == edge_type
        # the entry station is a catalog agent, not a runnable one
        assert entry in REGISTRY["theragen_agents"]
        assert entry not in REGISTRY["agents"]


def test_governance_clean_with_modeled_crews():
    eval_sets = governance.discover_eval_sets(REPO / "evals")
    targets = {es.get("target") for es in eval_sets}
    assert {"brightree_rounds_crew", "tenant_scan_crew"} <= targets
    report = governance.run_governance(eval_sets, REGISTRY)
    assert report["ok"], report["violations"]


def test_catalog_station_recognized_only_via_catalog():
    # a crew whose station is in neither agents nor theragen_agents still fails R4
    bad = {
        "agents": REGISTRY["agents"],
        "theragen_agents": REGISTRY["theragen_agents"],
        "crews": {"bogus": {"entry": "no_such_agent",
                            "edges": [{"from": "no_such_agent", "to": "reporting", "type": "X"}]}},
    }
    v = governance.check_stations(bad)
    assert any(x["rule"] == "R4" and "no_such_agent" in x["subject"] for x in v)
    # and the real catalog crews raise no station violation
    assert governance.check_stations(REGISTRY) == []


def test_edge_without_type_still_flagged():
    bad = {
        "agents": REGISTRY["agents"], "theragen_agents": REGISTRY["theragen_agents"],
        "crews": {"c": {"entry": "theragen_tenant_scan",
                        "edges": [{"from": "theragen_tenant_scan", "to": "reporting"}]}},
    }
    v = governance.check_stations(bad)
    assert any(x["rule"] == "R4" and "output contract" in x["message"] for x in v)


# --- typed contracts ---------------------------------------------------------

def _schema(defn: str) -> dict:
    return {"$ref": f"#/definitions/{defn}", "definitions": SCHEMA["definitions"]}


def test_extracted_records_contract():
    good = {
        "source": "Brightree PDF (synthetic fixture)",
        "schema_doc": {"columns": [
            {"name": "patient_id", "dtype": "str", "phi": True},
            {"name": "visit_count", "dtype": "int"},
        ]},
        "row_count": 42,
        "phi_columns": ["patient_id"],
        "lineage_log": ["extract", "normalize headers"],
    }
    jsonschema.validate(good, _schema("ExtractedRecords"))
    bad = dict(good)
    del bad["phi_columns"]                                  # must declare PHI columns
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema("ExtractedRecords"))


def test_site_inventory_contract():
    good = {
        "generated_at": "2026-11-10T00:00:00Z",
        "total_sites": 2,
        "sites": [
            {"site_id": "s1", "title": "Legacy HR", "disposition": "dormant"},
            {"site_id": "s2", "title": "Active Ops", "disposition": "keep"},
        ],
    }
    jsonschema.validate(good, _schema("SiteInventory"))
    bad = json.loads(json.dumps(good))
    del bad["sites"][0]["site_id"]                          # a site must have an id
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema("SiteInventory"))
    # disposition is a closed enum
    bad2 = json.loads(json.dumps(good))
    bad2["sites"][0]["disposition"] = "delete"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad2, _schema("SiteInventory"))


def test_brightree_eval_blocks_phi_flow():
    es = next(es for es in governance.discover_eval_sets(REPO / "evals")
              if es.get("target") == "brightree_rounds_crew")
    blockers = " ".join(b for e in es["evals"] for b in e.get("blockers", []))
    assert "PHI" in blockers                                # PHI non-flow is a pinned blocker


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
