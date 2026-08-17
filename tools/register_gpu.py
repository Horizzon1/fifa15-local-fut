"""Register this machine's GPU in FIFA 15's video-card table.

FIFA 15 ships a 2014 list of known graphics cards in
`fifasetup/VideoCards.xml`, keyed by `<pciVendor><pciDevice>` in hex. On boot it
looks the installed adapter up in that table. A card newer than the list is not
found, the lookup yields a null, and the game dereferences it without checking:

    mov rax, qword ptr [rcx]     ; rcx = 0  ->  access violation

That is the ~26-second boot crash. Adding the adapter to the table fixes it at
the source.

Always run `tools/game_backup.py backup` on the file first; this tool refuses to
write unless a verified backup already exists.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_CARDS = Path(r"F:\Games\FIFA 15\fifasetup\VideoCards.xml")
BACKUP_KEY = "fifasetup/VideoCards.xml"

# Level 3 is the highest tier the game defines: 1920x1080, quality 3, MSAA 2.
TOP_LEVEL = 3


def detect_adapters() -> list[dict[str, str]]:
    """Read installed display adapters and their PCI ids via WMI."""
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, PNPDeviceID | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not query display adapters: {result.stderr.strip()}")

    parsed = json.loads(result.stdout or "[]")
    if isinstance(parsed, dict):
        parsed = [parsed]

    adapters = []
    for entry in parsed:
        pnp = str(entry.get("PNPDeviceID") or "")
        match = re.search(r"VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})", pnp)
        if not match:
            continue
        adapters.append({
            "name": str(entry.get("Name") or "Unknown adapter"),
            "id": (match.group(1) + match.group(2)).upper(),
        })
    return adapters


def has_backup() -> bool:
    manifest = ROOT / "backups" / "manifest.json"
    if not manifest.exists():
        return False
    entries = json.loads(manifest.read_text(encoding="utf-8")).get("entries", {})
    return BACKUP_KEY in entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=VIDEO_CARDS)
    parser.add_argument("--level", type=int, default=TOP_LEVEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"!! not found: {args.file}")
        return 1

    if not args.dry_run and not has_backup():
        print("!! refusing to modify a game file with no verified backup.")
        print(f'   Run: python tools\\game_backup.py backup "{args.file}"')
        return 1

    adapters = detect_adapters()
    if not adapters:
        print("!! no PCI display adapter could be identified")
        return 1

    text = args.file.read_text(encoding="utf-8")
    additions = []
    for adapter in adapters:
        if re.search(rf'id="{adapter["id"]}"', text, re.IGNORECASE):
            print(f"== already listed: {adapter['id']}  {adapter['name']}")
            continue
        additions.append(adapter)
        print(f"++ will add: {adapter['id']}  {adapter['name']}  (level {args.level})")

    if not additions:
        print("\nNothing to do; every adapter is already in the table.")
        return 0

    if args.dry_run:
        print("\nDry run; nothing written.")
        return 0

    lines = "".join(
        f'  <vd id="{a["id"]}" level="{args.level}" name="{a["name"]}"/>\n'
        for a in additions
    )
    marker = "</CardGroupPC>"
    if marker not in text:
        print(f"!! could not find {marker} in the file; refusing to guess")
        return 1

    # Insert immediately before the closing tag, matching the file's style.
    patched = text.replace(marker, lines + marker)
    args.file.write_text(patched, encoding="utf-8")

    verify = args.file.read_text(encoding="utf-8")
    for adapter in additions:
        if f'id="{adapter["id"]}"' not in verify:
            print(f"!! write verification failed for {adapter['id']}")
            return 1

    print(f"\nWrote {len(additions)} entr{'y' if len(additions) == 1 else 'ies'} to {args.file.name}.")
    print("Restore any time with: python tools\\game_backup.py restore")
    return 0


if __name__ == "__main__":
    sys.exit(main())
