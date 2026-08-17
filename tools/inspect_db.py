"""Open a FIFA 15 client DB straight out of the archives and describe it."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from big_archive import Archive
from fifa_db import crc32_mpeg2, parse_descriptor, parse_fifa_db, read_row, string_table

# Where each FIFA 15 client DB lives. Resolved by EXACT archive path: the FIFA 14
# project lost weeks to content heuristics picking the wrong database, because
# the main game DB and the FUT cards DB carry the same star player IDs.
DB_LOCATIONS = {
    "cards": {
        # The patch archive wins at runtime; the base copy is the fallback.
        "db": [("data_patch.big", "data/db/cards_ng_db.db"), ("cards0.big", "data/db/cards_ng_db.db")],
        "meta": [("cards0.big", "data/db/cards_ng_db-meta.xml")],
    },
    "game": {
        "db": [("data_startup.big", "data/db/fifa_ng_db.db")],
        "meta": [("data_startup.big", "data/db/fifa_ng_db-meta.xml")],
    },
}


def load(game_root: Path, which: str):
    spec = DB_LOCATIONS[which]

    def fetch(candidates):
        for archive_name, record_path in candidates:
            archive = Archive(game_root / archive_name)
            entry = archive.find(record_path)
            if entry is not None:
                return archive.read(entry), f"{archive_name}:rec{entry.index}:{record_path}"
        raise FileNotFoundError(f"no candidate found for {which}: {candidates}")

    db_bytes, db_source = fetch(spec["db"])
    meta_bytes, meta_source = fetch(spec["meta"])
    descriptor = parse_descriptor(meta_bytes)
    db = parse_fifa_db(db_bytes, descriptor)
    return db, descriptor, db_source, meta_source, db_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", type=Path, default=Path(r"F:\Games\FIFA 15"))
    parser.add_argument("--which", choices=sorted(DB_LOCATIONS), default="cards")
    parser.add_argument("--tables", action="store_true", help="list tables with row counts")
    parser.add_argument("--table", help="dump a table's field layout")
    parser.add_argument("--rows", type=int, default=0, help="print this many rows of --table")
    parser.add_argument("--names", action="store_true", help="decode the playernames string table")
    args = parser.parse_args()

    db, descriptor, db_source, meta_source, raw = load(args.game_root, args.which)

    print(f"# db   : {db_source}  ({len(raw):,} bytes decoded)")
    print(f"# meta : {meta_source}  ({len(descriptor)} descriptor keys)")

    # Header CRC check: the checksum of the first 20 bytes is stored at offset 20.
    stored_crc = int.from_bytes(raw[db.start + 20 : db.start + 24], "little")
    computed = crc32_mpeg2(raw[db.start : db.start + 20])
    print(f"# header CRC-32/MPEG-2 stored={stored_crc:#010x} computed={computed:#010x} "
          f"{'MATCH' if stored_crc == computed else 'MISMATCH'}")

    if args.tables:
        seen = set()
        rows = []
        for table in db.tables.values():
            if table.name in seen:
                continue
            seen.add(table.name)
            rows.append((table.name, table.short_name, table.valid_records_count,
                         table.records_count, table.record_size, len(table.fields)))
        rows.sort(key=lambda r: -r[2])
        print(f"\n{'table':<28}{'short':<7}{'valid':>8}{'cap':>8}{'recsz':>7}{'fields':>7}")
        for name, short, valid, cap, size, nfields in rows:
            print(f"{name:<28}{short:<7}{valid:>8}{cap:>8}{size:>7}{nfields:>7}")

    if args.table:
        table = db.table(args.table)
        if table is None:
            print(f"!! table not found: {args.table}")
            return 1
        print(f"\n== {table.name} ({table.short_name}) "
              f"{table.valid_records_count}/{table.records_count} rows, {table.record_size}B records")
        for field in table.fields:
            print(f"  {field.name:<32}{field.short_name:<6}type={field.db_type:<3}"
                  f"bit={field.bit_offset:<6}depth={field.depth:<4}range=[{field.range_low},{field.range_high}]")
        for index in range(min(args.rows, table.valid_records_count)):
            print(f"  row[{index}] = {json.dumps(read_row(db, table, index), default=str)}")

    if args.names:
        names = string_table(db, "playernames", ("nameid", "playernameid", "id"), ("name", "playername"))
        print(f"\n# playernames decoded: {len(names)}")
        for key in list(names)[:15]:
            print(f"  {key} = {names[key]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
