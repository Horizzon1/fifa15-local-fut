"""Locate FIFA 15 client database records inside the game archives, by path.

The FIFA 14 project learned the hard way that identifying the FUT cards DB by
content heuristics picks the wrong database (the main game DB carries the same
star player IDs). So this tool reports the EXACT archive path of every record,
and only then annotates what the payload looks like.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from big_archive import Archive, iter_archives, is_chunkzip

# FIFA database payloads start with this magic; the XML descriptor that
# describes their table layout is a sibling record.
DB_MAGIC = b"DB"


def classify(payload: bytes) -> str:
    if payload[:5].lower() == b"<?xml":
        return "xml"
    if payload[:2] == DB_MAGIC:
        return "fifa-db"
    if payload[:4] == b"BIG4" or payload[:4] == b"BIGF":
        return "nested-big"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", type=Path, default=Path(r"F:\Games\FIFA 15"))
    parser.add_argument("--suffix", default=".db", help="record path suffix to report")
    parser.add_argument("--contains", default=None, help="substring filter on the record path")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--decode",
        action="store_true",
        help="decode payloads to classify them (slower, reads whole archives)",
    )
    args = parser.parse_args()

    findings = []
    for big_path in iter_archives(args.game_root):
        try:
            archive = Archive(big_path)
            entries = archive.entries
        except Exception as exc:  # a non-archive or unreadable file must not stop the scan
            print(f"# skip {big_path.name}: {exc}", file=sys.stderr)
            continue

        for entry in entries:
            name = entry.name.replace("\\", "/")
            if args.suffix and not name.lower().endswith(args.suffix.lower()):
                continue
            if args.contains and args.contains.lower() not in name.lower():
                continue

            row = {
                "archive": big_path.name,
                "record_index": entry.index,
                "path": entry.name,
                "offset": entry.offset,
                "stored_size": entry.size,
                "compressed": is_chunkzip(archive.raw(entry)),
            }
            if args.decode:
                try:
                    decoded = archive.read(entry)
                    row["decoded_size"] = len(decoded)
                    row["kind"] = classify(decoded)
                except Exception as exc:
                    row["decode_error"] = str(exc)
            findings.append(row)

        # free the archive bytes before moving to the next multi-GB file
        del archive

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        for row in findings:
            extra = f"\t{row.get('kind','')}\t{row.get('decoded_size','')}" if args.decode else ""
            print(
                f"{row['archive']}\trec{row['record_index']}\t{row['stored_size']}"
                f"{extra}\t{row['path']}"
            )
    print(f"# {len(findings)} record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
