"""Extract printable strings from a binary and filter them by regex.

Used for recon on fifa15.exe and extracted archive records: find hostnames,
ports, URL templates and config keys the client actually references.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ASCII_RUN = re.compile(rb"[\x20-\x7e]{%d,}")
UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}")


def iter_strings(data: bytes, min_length: int, utf16: bool):
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % min_length, data):
        yield match.start(), match.group().decode("ascii")
    if utf16:
        for match in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_length, data):
            yield match.start(), match.group().decode("utf-16-le")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--pattern", default=None, help="case-insensitive regex filter")
    parser.add_argument("--min-length", type=int, default=5)
    parser.add_argument("--utf16", action="store_true")
    parser.add_argument("--offsets", action="store_true", help="print file offsets")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    data = args.path.read_bytes()
    needle = re.compile(args.pattern, re.IGNORECASE) if args.pattern else None

    seen: set[str] = set()
    shown = 0
    for offset, text in iter_strings(data, args.min_length, args.utf16):
        if needle and not needle.search(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        print(f"{offset:#010x}\t{text}" if args.offsets else text)
        shown += 1
        if args.limit and shown >= args.limit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
