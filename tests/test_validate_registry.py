"""validate.py --all must accept offline catalog records as crew stations."""
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("validate", REPO / "scripts" / "validate.py")
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)


def _write(tmp_path, reg):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    return p


def test_repo_registry_is_valid():
    assert validate.validate_registry(REPO / "agents" / "registry.json") == []


def test_catalog_record_can_be_a_station(tmp_path):
    reg = {
        "agents": {"a": {"path": "README.md"}},
        "theragen_agents": {"ext": {"path": "README.md"}},
        "crews": {"c": {"entry": "ext", "edges": [{"from": "ext", "to": "a", "type": "X"}]}},
    }
    assert validate.validate_registry(_write(tmp_path, reg)) == []


def test_unknown_station_still_fails(tmp_path):
    reg = {
        "agents": {"a": {"path": "README.md"}},
        "crews": {"c": {"entry": "a", "edges": [{"from": "a", "to": "ghost", "type": "X"}]}},
    }
    errs = validate.validate_registry(_write(tmp_path, reg))
    assert any("ghost" in e for e in errs)


def test_missing_catalog_file_fails(tmp_path):
    reg = {"agents": {}, "theragen_agents": {"ext": {"path": "nope/missing.md"}}, "crews": {}}
    errs = validate.validate_registry(_write(tmp_path, reg))
    assert any("file not found" in e for e in errs)
