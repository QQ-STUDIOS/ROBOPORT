"""check_recovery: boot-time jitter must not count as a reboot."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# psutil is a Windows deploy dependency (deploy/windows/requirements.lock.txt),
# not a CI one; check_recovery itself never touches it.
if importlib.util.find_spec("psutil") is None:
    sys.modules["psutil"] = types.ModuleType("psutil")

from deployment_report import BOOT_TIME_TOLERANCE_SECONDS, check_recovery

RELEASE = r"C:\Roboport\releases\20261110T045848557Z"
BASELINE = 1_794_202_669.394
TASK_OK = {"status": "passed", "registered": True}
TASK_BAD = {"status": "failed", "registered": True, "launch_parent_matches": False}


def _check(boot_time, *, supervisor_create_time=None, task=TASK_OK, release=RELEASE, baseline=BASELINE):
    previous = {"release_root": RELEASE, "verified_boot_time": baseline}
    if supervisor_create_time is None:
        supervisor_create_time = boot_time + 120
    return check_recovery(previous, boot_time=boot_time, supervisor_create_time=supervisor_create_time,
                          release_root=release, task_observation=task)


def test_subsecond_jitter_without_reboot_stays_pending():
    # Observed on the pilot workstation: same boot, psutil read 0.028 s later, and the
    # supervisor had been started directly (not by the task) -> was reported "failed".
    result = _check(BASELINE + 0.028, supervisor_create_time=BASELINE + 3600, task=TASK_BAD)
    assert result["status"] == "pending"


def test_jitter_inside_tolerance_stays_pending():
    assert _check(BASELINE + BOOT_TIME_TOLERANCE_SECONDS)["status"] == "pending"


def test_real_reboot_with_task_launch_passes():
    assert _check(BASELINE + 6 * 3600)["status"] == "passed"


def test_real_reboot_without_task_launch_fails():
    assert _check(BASELINE + 6 * 3600, task=TASK_BAD)["status"] == "failed"


def test_supervisor_older_than_boot_fails():
    boot = BASELINE + 6 * 3600
    assert _check(boot, supervisor_create_time=boot - 10)["status"] == "failed"


def test_other_release_or_missing_baseline_stays_pending():
    assert _check(BASELINE + 6 * 3600, release=r"C:\Roboport\releases\other")["status"] == "pending"
    assert _check(BASELINE + 6 * 3600, baseline=None)["status"] == "pending"
