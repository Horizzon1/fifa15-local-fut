"""Reader for FIFA client `.db` databases (FIFA 15).

A FIFA `.db` is a flat binary store described by a sibling `-meta.xml`
descriptor. The descriptor maps 4-character short names to human-readable table
and field names; the binary carries the table directory, per-table field
layouts, and bit-packed records.

Strings come in three flavours:
  * db_type 0      - fixed-width inline UTF-8
  * db_type 13, 14 - offsets into a per-table Huffman-compressed string block

Format knowledge ported from the FIFA 14 Local FUT project's DB tooling
(https://github.com/KyroGeorge2/FIFA-14-Local-FUT) and re-verified against the
retail FIFA 15 `cards_ng_db.db` / `fifa_ng_db.db`.
"""
from __future__ import annotations

import struct
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

DB_HEADER = b"DB\x00\x08\x00\x00\x00\x00"
NO_COMPRESSED_STRINGS = 0xFFFFFFFF

# Field storage kinds, as encoded in the per-table field directory.
TYPE_INLINE_STRING = 0
TYPE_BITPACKED_INT = 3
TYPE_FLOAT = 4
TYPE_COMPRESSED_STRING_8 = 13
TYPE_COMPRESSED_STRING_16 = 14
COMPRESSED_STRING_TYPES = (TYPE_COMPRESSED_STRING_8, TYPE_COMPRESSED_STRING_16)


# --------------------------------------------------------------------------
# CRC-32/MPEG-2 over the DB header
# --------------------------------------------------------------------------

def crc32_mpeg2(data: bytes) -> int:
    """CRC-32/MPEG-2: poly 0x04C11DB7, init 0xFFFFFFFF, non-reflected, no xorout.

    Every FIFA `.db` header stores this checksum of its first 20 bytes at offset
    20. Resizing a DB without rewriting it makes the game reject the database.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


# --------------------------------------------------------------------------
# Descriptor XML
# --------------------------------------------------------------------------

@dataclass
class XmlField:
    name: str
    short_name: str
    range_low: int = 0
    range_high: int = -1


@dataclass
class XmlTable:
    name: str
    short_name: str
    fields: dict[str, XmlField]
    by_short: dict[str, XmlField]


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower()


def _value(node: ET.Element, keys: tuple[str, ...]) -> str | None:
    lowered = {key.lower() for key in keys}
    for key, value in node.attrib.items():
        if key.lower() in lowered and value is not None:
            return str(value).strip()
    for child in list(node):
        if _tag(child) in lowered and child.text:
            return child.text.strip()
    return None


def _to_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def parse_descriptor(xml_bytes: bytes) -> dict[str, XmlTable]:
    """Parse a `*-meta.xml` descriptor into tables keyed by both name and short name."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"invalid FIFA DB descriptor XML: {exc}") from exc

    tables: dict[str, XmlTable] = {}
    for node in root.iter():
        if "field" in _tag(node):
            continue
        name = _value(node, ("name", "tablename"))
        short = _value(node, ("shortname",))
        if not name or not short or len(short) != 4:
            continue

        fields: dict[str, XmlField] = {}
        by_short: dict[str, XmlField] = {}
        for child in node.iter():
            if "field" not in _tag(child):
                continue
            fname = _value(child, ("name", "fieldname"))
            fshort = _value(child, ("shortname",))
            if not fname or not fshort or len(fshort) != 4:
                continue
            field = XmlField(
                name=fname,
                short_name=fshort,
                range_low=_to_int(_value(child, ("rangelow", "min")), 0),
                range_high=_to_int(_value(child, ("rangehigh", "max")), -1),
            )
            fields[fname.lower()] = field
            by_short[fshort] = field

        if fields:
            table = XmlTable(name=name, short_name=short, fields=fields, by_short=by_short)
            tables[name.lower()] = table
            tables[short] = table

    if not tables:
        raise ValueError("descriptor XML did not expose any tables")
    return tables


# --------------------------------------------------------------------------
# Binary DB
# --------------------------------------------------------------------------

@dataclass
class DbField:
    name: str
    short_name: str
    db_type: int
    bit_offset: int
    depth: int
    range_low: int
    range_high: int


@dataclass
class DbTable:
    name: str
    short_name: str
    table_start: int
    table_end: int
    record_size: int
    records_count: int
    valid_records_count: int
    records_offset: int
    compressed_length: int
    fields: list[DbField]

    @property
    def by_name(self) -> dict[str, DbField]:
        return {field.name.lower(): field for field in self.fields}


@dataclass
class FifaDb:
    data: bytearray
    start: int
    end: int
    tables: dict[str, DbTable]

    def table(self, name: str) -> DbTable | None:
        lowered = name.lower()
        if lowered in self.tables:
            return self.tables[lowered]
        for table in self.tables.values():
            if table.name.lower() == lowered:
                return table
        return None

    @property
    def table_names(self) -> list[str]:
        return sorted({t.name for t in self.tables.values()})


def _short_name(data: bytes | bytearray, offset: int) -> str:
    return bytes(data[offset : offset + 4]).decode("latin1")


def parse_fifa_db(db_bytes: bytes, descriptor: dict[str, XmlTable]) -> FifaDb:
    header = db_bytes.find(DB_HEADER)
    if header < 0:
        raise ValueError("FIFA DB header not found (payload may still be compressed)")

    data = bytearray(db_bytes)
    declared_size = struct.unpack_from("<I", data, header + 8)[0]
    end = header + declared_size
    if declared_size <= 0 or end > len(data):
        end = len(data)

    cursor = header + 8
    cursor += 4  # declared size
    cursor += 4  # unknown
    table_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    cursor += 4  # unknown

    refs: list[tuple[str, int]] = []
    for _ in range(table_count):
        if cursor + 8 > end:
            raise ValueError("truncated FIFA DB table directory")
        refs.append((_short_name(data, cursor), struct.unpack_from("<I", data, cursor + 4)[0]))
        cursor += 8
    cursor += 4

    tables_start = cursor
    starts = sorted({tables_start + rel for _, rel in refs if tables_start + rel < end})

    tables: dict[str, DbTable] = {}
    for short, rel in refs:
        xdesc = descriptor.get(short)
        if xdesc is None:
            continue
        table_start = tables_start + rel
        if table_start + 32 > end:
            continue

        tcur = table_start + 4
        record_size = struct.unpack_from("<I", data, tcur)[0]
        tcur += 8  # record size + unknown
        compressed_raw = struct.unpack_from("<I", data, tcur)[0]
        tcur += 4
        compressed_length = 0 if compressed_raw == NO_COMPRESSED_STRINGS else compressed_raw
        records_count = struct.unpack_from("<H", data, tcur)[0]
        tcur += 2
        valid_count = struct.unpack_from("<H", data, tcur)[0]
        tcur += 2
        tcur += 4
        fields_count = data[tcur]
        tcur += 12  # count byte + padding

        fields: list[DbField] = []
        for _ in range(fields_count):
            if tcur + 16 > end:
                raise ValueError(f"truncated field directory for {xdesc.name}")
            db_type = struct.unpack_from("<I", data, tcur)[0]
            bit_offset = struct.unpack_from("<I", data, tcur + 4)[0]
            fshort = _short_name(data, tcur + 8)
            depth = struct.unpack_from("<I", data, tcur + 12)[0]
            tcur += 16
            xf = xdesc.by_short.get(fshort)
            fields.append(
                DbField(
                    name=(xf.name if xf else fshort),
                    short_name=fshort,
                    db_type=db_type,
                    bit_offset=bit_offset,
                    depth=depth,
                    range_low=(xf.range_low if xf else 0),
                    range_high=(xf.range_high if xf else -1),
                )
            )
        fields.sort(key=lambda f: f.bit_offset)

        table = DbTable(
            name=xdesc.name,
            short_name=short,
            table_start=table_start,
            table_end=next((s for s in starts if s > table_start), end),
            record_size=record_size,
            records_count=records_count,
            valid_records_count=valid_count,
            records_offset=tcur,
            compressed_length=compressed_length,
            fields=fields,
        )
        tables[xdesc.name.lower()] = table
        tables[short] = table

    if not tables:
        raise ValueError("no descriptor-backed tables could be parsed")
    return FifaDb(data=data, start=header, end=end, tables=tables)


# --------------------------------------------------------------------------
# Field access
# --------------------------------------------------------------------------

def read_bits_le(record: bytes | bytearray, bit_offset: int, depth: int) -> int:
    value = 0
    for bit in range(depth):
        source = bit_offset + bit
        if (record[source >> 3] >> (source & 7)) & 1:
            value |= 1 << bit
    return value


def read_field(record: bytes | bytearray, field: DbField) -> Any:
    if field.db_type == TYPE_BITPACKED_INT:
        return read_bits_le(record, field.bit_offset, field.depth) + field.range_low
    if field.db_type == TYPE_FLOAT:
        return struct.unpack_from("<f", record, field.bit_offset >> 3)[0]
    if field.db_type == TYPE_INLINE_STRING:
        off = field.bit_offset >> 3
        length = (field.depth + 7) // 8
        raw = bytes(record[off : off + length]).split(b"\x00", 1)[0]
        return raw.decode("utf-8", errors="replace")
    if field.db_type in COMPRESSED_STRING_TYPES:
        return struct.unpack_from("<i", record, field.bit_offset >> 3)[0]
    return read_bits_le(record, field.bit_offset, field.depth) if field.depth > 0 else 0


def record_bytes(db: FifaDb, table: DbTable, index: int) -> bytes:
    off = table.records_offset + index * table.record_size
    return bytes(db.data[off : off + table.record_size])


def read_row(db: FifaDb, table: DbTable, index: int) -> dict[str, Any]:
    rec = record_bytes(db, table, index)
    return {
        field.name.lower(): read_field(rec, field)
        for field in table.fields
        if field.db_type not in COMPRESSED_STRING_TYPES
    }


def iter_rows(db: FifaDb, table: DbTable):
    for index in range(table.valid_records_count):
        yield read_row(db, table, index)


# --------------------------------------------------------------------------
# Huffman-compressed string tables (player names live here)
# --------------------------------------------------------------------------

def _huffman_tree(db: FifaDb, table: DbTable, text_field: DbField) -> list[tuple[int, int, int, int]]:
    """Recover the per-table Huffman tree that precedes the string block.

    The tree occupies the bytes between the end of the records and the first
    string offset any record references, so the smallest offset marks its size.
    """
    base = table.records_offset + table.records_count * table.record_size
    if table.compressed_length <= 0:
        return []

    minimum: int | None = None
    for index in range(table.valid_records_count):
        rec = record_bytes(db, table, index)
        off = struct.unpack_from("<i", rec, text_field.bit_offset >> 3)[0]
        if off >= 0 and (minimum is None or off < minimum):
            minimum = off

    tree_size = minimum or 0
    if base + tree_size > len(db.data):
        return []
    return [
        (db.data[base + n * 4], db.data[base + n * 4 + 1], db.data[base + n * 4 + 2], db.data[base + n * 4 + 3])
        for n in range(tree_size // 4)
    ]


def decode_huffman_string(
    buffer: bytes | bytearray, offset: int, output_len: int, tree: list[tuple[int, int, int, int]]
) -> str:
    if output_len <= 0:
        return ""
    if not tree:
        return bytes(buffer[offset : offset + output_len]).split(b"\x00", 1)[0].decode("utf-8", "replace")

    out = bytearray()
    node = 0
    cursor = offset
    while len(out) < output_len and cursor < len(buffer):
        byte = buffer[cursor]
        cursor += 1
        for bit in range(7, -1, -1):
            direction = (byte >> bit) & 1
            c0, l0, c1, l1 = tree[node] if 0 <= node < len(tree) else (0, 0, 0, 0)
            child = c1 if direction else c0
            leaf = l1 if direction else l0
            if child == 0:
                out.append(leaf)
                node = 0
                if len(out) >= output_len:
                    break
            else:
                node = child
    return bytes(out).split(b"\x00", 1)[0].decode("utf-8", "replace")


def string_table(db: FifaDb, table_name: str, id_names: tuple[str, ...], text_names: tuple[str, ...]) -> dict[int, str]:
    """Decode an id -> text mapping from a table whose text column is compressed."""
    table = db.table(table_name)
    if table is None:
        return {}
    fields = table.by_name
    id_field = next((fields[n] for n in id_names if n in fields), None)
    text_field = next((fields[n] for n in text_names if n in fields), None)
    if id_field is None or text_field is None:
        return {}

    base = table.records_offset + table.records_count * table.record_size
    tree = _huffman_tree(db, table, text_field) if text_field.db_type in COMPRESSED_STRING_TYPES else []

    result: dict[int, str] = {}
    for index in range(table.valid_records_count):
        rec = record_bytes(db, table, index)
        try:
            key = int(read_field(rec, id_field))
        except Exception:
            continue

        if text_field.db_type == TYPE_INLINE_STRING:
            text = str(read_field(rec, text_field))
        else:
            offset = struct.unpack_from("<i", rec, text_field.bit_offset >> 3)[0]
            if offset < 0 or base + offset >= len(db.data):
                continue
            pos = base + offset
            if text_field.db_type == TYPE_COMPRESSED_STRING_16:
                if pos + 2 > len(db.data):
                    continue
                length = struct.unpack_from(">H", db.data, pos)[0]
                text = decode_huffman_string(db.data, pos + 2, length, tree)
            else:
                length = db.data[pos] if pos < len(db.data) else 0
                text = decode_huffman_string(db.data, pos + 1, length, tree)

        if text:
            result[key] = text
    return result


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in value.casefold() if ch.isalnum())
