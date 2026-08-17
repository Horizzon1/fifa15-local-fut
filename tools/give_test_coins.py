"""Grant test coins to the local FIFA 15 FUT club.

The grant is idempotent: it is keyed, so re-running it will not stack. Pass a
different --key to grant again.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.stdout.reconfigure(encoding="utf-8")

from config import ServerConfig  # noqa: E402
from identity import IdentityStore, PlayerCatalog  # noqa: E402


def main() -> int:
    config = ServerConfig()
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=config.database)
    parser.add_argument("--catalog", type=Path, default=config.catalog)
    parser.add_argument("--amount", type=int, default=100_000_000)
    parser.add_argument("--key", default="test-coins-100m")
    parser.add_argument("--repeat", action="store_true",
                        help="grant again even if this key was already applied")
    args = parser.parse_args()

    if not args.catalog.exists():
        print(f"!! player catalog missing: {args.catalog}")
        print("   Run: python tools\\extract_catalog.py")
        return 1

    catalog = PlayerCatalog(args.catalog)
    store = IdentityStore(args.database, catalog)

    key = args.key
    if args.repeat:
        import time
        key = f"{args.key}-{int(time.time())}"

    result = store.grant_once(key, args.amount, "developer test coins")

    print(f"Database : {args.database}")
    if result["applied"]:
        print(f"Granted  : {args.amount:,} coins")
    else:
        print(f"Already applied (key '{key}') — balance unchanged.")
        print("Use --repeat to grant another lot.")
    print(f"Balance  : {result['balance']:,} coins")

    summary = store.summary()
    print(f"Club     : {summary['club']}  ({summary['totalItems']} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
