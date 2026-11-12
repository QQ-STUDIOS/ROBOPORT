"""Station packs: a site adds its own catalog, crews, evals and output contracts
under stations/<id>/ without changing the core."""

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import governance  # noqa: E402
import station_packs  # noqa: E402


def core_registry():
    return json.loads((REPO / "agents/registry.json").read_text(encoding="utf-8"))


def make_repo(tmp_path):
    """A copy of the core registry and output contracts, with no stations/."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "resources/schemas").mkdir(parents=True)
    shutil.copy(REPO / "agents/registry.json", tmp_path / "agents/registry.json")
    shutil.copy(REPO / "resources/schemas/output.schema.json", tmp_path / "resources/schemas/output.schema.json")
    return tmp_path


def make_pack(repo, pack_id="acme", registry=None, schemas=None, evals=None, meta_id=None):
    pack = repo / "stations" / pack_id
    pack.mkdir(parents=True)
    (pack / "station.json").write_text(json.dumps({"schema_version": 1, "id": meta_id or pack_id}), encoding="utf-8")
    if registry is not None:
        (pack / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    if schemas is not None:
        (pack / "schemas").mkdir()
        (pack / "schemas/output.schema.json").write_text(json.dumps({"definitions": schemas}), encoding="utf-8")
    if evals is not None:
        (pack / "evals").mkdir()
        (pack / "evals/acme_evals.json").write_text(json.dumps(evals), encoding="utf-8")
    return pack


EXAMPLE_REGISTRY = {
    "catalog_agents": {"acme_intake": {"path": "stations/acme/agents/intake.md", "name": "Intake", "external": True}},
    "crews": {"acme_crew": {"entry": "acme_intake", "classification": "Internal",
                            "edges": [{"from": "acme_intake", "to": "reporting", "type": "AcmeRecords"}]}},
}


def test_core_has_no_catalog_and_passes_governance_alone():
    assert "catalog_agents" not in core_registry()
    sets = governance.discover_eval_sets(REPO / "evals")
    assert governance.run_governance(sets, core_registry(), governance.ALL_RULES)["violations"] == []


def test_without_stations_the_framework_is_plain(tmp_path):
    repo = make_repo(tmp_path)
    assert station_packs.load_registry(repo) == core_registry()
    assert station_packs.eval_dirs(repo) == [repo / "evals"]
    core_schema = json.loads((REPO / "resources/schemas/output.schema.json").read_text(encoding="utf-8"))
    assert station_packs.load_output_schema(repo) == core_schema


def test_a_pack_adds_its_catalog_crews_evals_and_contracts(tmp_path):
    repo = make_repo(tmp_path)
    make_pack(repo, registry=EXAMPLE_REGISTRY, schemas={"AcmeRecords": {"type": "object"}},
              evals={"target": "acme_crew", "evals": []})
    merged = station_packs.load_registry(repo)
    assert "acme_intake" in merged["catalog_agents"] and "acme_crew" in merged["crews"]
    assert set(core_registry()["crews"]) <= set(merged["crews"])
    assert "AcmeRecords" in station_packs.load_output_schema(repo)["definitions"]
    assert repo / "stations/acme/evals" in station_packs.eval_dirs(repo)
    # A crew whose entry is a catalog agent passes the station-integrity rule.
    assert governance.check_stations(merged) == []


def test_an_explicit_registry_path_is_taken_as_is(tmp_path):
    repo = make_repo(tmp_path)
    make_pack(repo, registry=EXAMPLE_REGISTRY)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"agents": {}, "crews": {}}), encoding="utf-8")
    assert station_packs.load_registry(repo, other) == {"agents": {}, "crews": {}}


def test_a_pack_cannot_replace_a_core_entry(tmp_path):
    repo = make_repo(tmp_path)
    make_pack(repo, registry={"crews": {"jd_crew": {}}})
    with pytest.raises(ValueError, match="jd_crew is already defined"):
        station_packs.load_registry(repo)


def test_a_pack_cannot_replace_a_core_output_contract(tmp_path):
    repo = make_repo(tmp_path)
    make_pack(repo, schemas={"FinalReport": {}})
    with pytest.raises(ValueError, match="FinalReport is already defined"):
        station_packs.load_output_schema(repo)


def test_pack_identity_must_match_its_directory(tmp_path):
    pack = make_pack(tmp_path, meta_id="other")
    with pytest.raises(ValueError, match="id 'acme'"):
        station_packs.load_station(pack)
