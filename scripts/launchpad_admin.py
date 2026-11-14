"""Enroll explicitly named local operators; no credential is written to a URL/log."""

import argparse
import getpass
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from dashboard.launchpad.auth import Auth
from dashboard.launchpad.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    enroll = sub.add_parser("enroll")
    enroll.add_argument("--data-root", type=Path, required=True)
    enroll.add_argument("--username", required=True)
    enroll.add_argument(
        "--role", choices=["operator", "reviewer", "admin"], required=True
    )
    args = parser.parse_args()
    password = getpass.getpass("New local password (12+ characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    auth = Auth(Store(args.data_root / "launchpad/launchpad.sqlite3"))
    result = auth.enroll(args.username, password, args.role)
    print(
        "Enrolled "
        + result["id"]
        + " as "
        + result["role"]
        + ". Existing sessions revoked."
    )


if __name__ == "__main__":
    main()
