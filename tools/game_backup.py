"""Byte-for-byte backup and restore for FIFA 15 game files.

Rule for this project: never modify a game file without a verified backup and a
tested restore. This tool enforces that — it refuses to report success unless
the SHA-256 of the copy matches the original, and `verify` re-checks every
backup on disk against its recorded hash.

Backups live in the project, never in the game folder, so the install stays
exactly as the game shipped it apart from the file being changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backups"
MANIFEST = BACKUP_DIR / "manifest.json"
GAME_ROOT = Path(r"F:\Games\FIFA 15")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"entries": {}}


def save_manifest(manifest: dict) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def relative_key(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(GAME_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def backup(paths: list[Path]) -> int:
    manifest = load_manifest()
    for path in paths:
        path = path.resolve()
        if not path.is_file():
            print(f"!! not a file: {path}")
            return 1

        key = relative_key(path)
        target = BACKUP_DIR / key
        target.parent.mkdir(parents=True, exist_ok=True)

        original = sha256(path)
        if key in manifest["entries"] and target.exists():
            existing = manifest["entries"][key]
            if existing["sha256"] == original and sha256(target) == original:
                print(f"== already backed up, unchanged: {key}")
                continue
            print(f"!! {key} already has a backup with a different hash.")
            print("   Refusing to overwrite it — that would lose the pristine copy.")
            print("   Restore first, then re-run.")
            return 1

        shutil.copy2(path, target)
        copied = sha256(target)
        if copied != original:
            print(f"!! backup verification FAILED for {key}")
            return 1

        manifest["entries"][key] = {
            "source": str(path),
            "backup": str(target),
            "sha256": original,
            "size": path.stat().st_size,
            "backed_up_at": int(time.time()),
        }
        print(f"++ backed up {key}  ({path.stat().st_size:,} bytes, sha256 {original[:16]}…)")

    save_manifest(manifest)
    return 0


def restore(keys: list[str] | None = None) -> int:
    manifest = load_manifest()
    entries = manifest.get("entries", {})
    if not entries:
        print("== nothing to restore; no backups recorded")
        return 0

    targets = keys if keys else list(entries)
    failures = 0
    for key in targets:
        entry = entries.get(key)
        if entry is None:
            print(f"!! no backup recorded for {key}")
            failures += 1
            continue

        backup_path = Path(entry["backup"])
        source_path = Path(entry["source"])
        if not backup_path.exists():
            print(f"!! backup file missing: {backup_path}")
            failures += 1
            continue

        if sha256(backup_path) != entry["sha256"]:
            print(f"!! backup for {key} is corrupt; refusing to restore it")
            failures += 1
            continue

        if source_path.exists() and sha256(source_path) == entry["sha256"]:
            print(f"== already pristine: {key}")
            continue

        shutil.copy2(backup_path, source_path)
        if sha256(source_path) != entry["sha256"]:
            print(f"!! restore verification FAILED for {key}")
            failures += 1
            continue
        print(f"<< restored {key}")

    return 1 if failures else 0


def verify() -> int:
    manifest = load_manifest()
    entries = manifest.get("entries", {})
    if not entries:
        print("no backups recorded — no game files have been modified")
        return 0

    failures = 0
    for key, entry in entries.items():
        backup_path = Path(entry["backup"])
        source_path = Path(entry["source"])
        backup_ok = backup_path.exists() and sha256(backup_path) == entry["sha256"]
        live_hash = sha256(source_path) if source_path.exists() else None
        pristine = live_hash == entry["sha256"]

        print(f"{key}")
        print(f"   backup intact : {'yes' if backup_ok else 'NO'}")
        print(f"   game file     : {'pristine' if pristine else 'MODIFIED'}")
        if not backup_ok:
            failures += 1
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up and restore FIFA 15 game files")
    sub = parser.add_subparsers(dest="command", required=True)

    backup_cmd = sub.add_parser("backup", help="back up files before modifying them")
    backup_cmd.add_argument("paths", nargs="+", type=Path)

    restore_cmd = sub.add_parser("restore", help="restore game files from backup")
    restore_cmd.add_argument("keys", nargs="*", help="specific keys, or all if omitted")

    sub.add_parser("verify", help="check every backup and whether the game files are pristine")
    sub.add_parser("list", help="show recorded backups")

    args = parser.parse_args()

    if args.command == "backup":
        return backup(args.paths)
    if args.command == "restore":
        return restore(args.keys or None)
    if args.command == "verify":
        return verify()
    if args.command == "list":
        manifest = load_manifest()
        for key, entry in manifest.get("entries", {}).items():
            print(f"{key}\t{entry['size']:,} bytes\t{entry['sha256'][:16]}…")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
