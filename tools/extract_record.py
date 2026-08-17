"""Extract one record out of a FIFA archive, by exact path, decoded."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from big_archive import Archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("record", help="exact record path inside the archive")
    parser.add_argument("--output", type=Path, help="write here instead of stdout")
    parser.add_argument("--raw", action="store_true", help="skip chunkzip decoding")
    args = parser.parse_args()

    archive = Archive(args.archive)
    entry = archive.find(args.record)
    if entry is None:
        matches = archive.find_by_basename(Path(args.record).name)
        print(f"!! not found: {args.record}", file=sys.stderr)
        for match in matches[:10]:
            print(f"   did you mean: {match.name}", file=sys.stderr)
        return 1

    payload = archive.raw(entry) if args.raw else archive.read(entry)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
        print(f"# wrote {args.output} ({len(payload):,} bytes) from rec{entry.index}")
    else:
        sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
