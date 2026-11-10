"""Back-compat shim — FaultProvider now lives in the runtime package so the
offline fault-injection benchmark can use it too. Import it from there.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from roboport_runtime.fault_provider import FaultProvider  # noqa: F401,E402
