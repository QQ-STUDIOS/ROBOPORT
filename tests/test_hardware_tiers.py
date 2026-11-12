"""Install-time machine binding and measured hardware tiers (no product names)."""

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import windows_deploy  # noqa: E402

TIERS = REPO / "deploy/windows/hardware-tiers.json"
EXAMPLE = REPO / "deploy/windows/deployment.example.json"
GIB = 2**30


def tier_a():
    return windows_deploy.load_tiers(TIERS)["A"]


def test_vram_is_read_from_vendor_rows_only():
    csv = "NVIDIA GeForce RTX 5090, 32607 MiB, 617.42\nIntel(R) Arc(TM) Graphics, 65536 MiB, 1.0"
    assert windows_deploy.gpu_vram_mib(csv, "NVIDIA") == 32607
    assert windows_deploy.gpu_vram_mib("", "NVIDIA") == 0
    assert windows_deploy.gpu_vram_mib("NVIDIA garbage, n/a, 1", "NVIDIA") == 0


def test_tier_a_is_measured_capacity_not_a_product_name():
    spec = tier_a()
    # The current workstation and any other card with the same capacity fit.
    assert windows_deploy.tier_fits(spec, "NVIDIA GeForce RTX 5090, 32607 MiB, 617.42", 127 * GIB, "qwen3:14b")
    assert windows_deploy.tier_fits(spec, "NVIDIA RTX 6000 Ada Generation, 49140 MiB, 560.1", 64 * GIB, "qwen3:14b")
    # Too little VRAM or RAM, or a model not approved for the tier, does not.
    assert not windows_deploy.tier_fits(spec, "NVIDIA GeForce RTX 4090, 24564 MiB, 560.1", 128 * GIB, "qwen3:14b")
    assert not windows_deploy.tier_fits(spec, "NVIDIA GeForce RTX 5090, 32607 MiB, 617.42", 32 * GIB, "qwen3:14b")
    assert not windows_deploy.tier_fits(spec, "NVIDIA GeForce RTX 5090, 32607 MiB, 617.42", 128 * GIB, "unreviewed:70b")


def test_only_tier_a_is_defined_and_example_is_unpinned():
    assert list(windows_deploy.load_tiers(TIERS)) == ["A"]
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert "expected_machine" not in example
    assert example["hardware_tier"] == "A"
    assert example["model"] in tier_a()["models"]
    windows_deploy.validate_config(example)


def test_configure_requires_a_bound_machine(tmp_path):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="not bound"):
        windows_deploy.configure_release(tmp_path / "release", tmp_path / "data", config)


def test_configure_records_the_machine_setup_observed(tmp_path, monkeypatch):
    config_path = tmp_path / "deployment.json"
    config_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    release, data = tmp_path / "release", tmp_path / "data"
    monkeypatch.setattr(sys, "argv", ["windows_deploy.py", "configure", "--release", str(release), "--data", str(data),
                                      "--config", str(config_path), "--machine", "NEW-BOX", "--hardware-tier", "A"])
    assert windows_deploy.main() == 0
    policy = yaml.safe_load((data / "launchpad/policy.yaml").read_text(encoding="utf-8"))
    assert policy["environment_id"] == "NEW-BOX"
    assert policy["expected_machine"] == "NEW-BOX"


def test_configure_rejects_a_pin_to_another_machine(tmp_path, monkeypatch):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    config["expected_machine"] = "OTHER-BOX"
    config_path = tmp_path / "deployment.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["windows_deploy.py", "configure", "--release", str(tmp_path / "r"), "--data",
                                      str(tmp_path / "d"), "--config", str(config_path), "--machine", "NEW-BOX"])
    with pytest.raises(ValueError, match="different machine"):
        windows_deploy.main()

