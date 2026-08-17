"""BIG4/ViV4 archive reader for FIFA 15 game archives.

FIFA archives ship as a pair:
  * `<name>.big` - a BIG4 container with a plaintext directory at the front.
  * `<name>.bh`  - a ViV4 sidecar index keyed by a hash of the record path.

Both indexes describe the same records, so anything that moves or resizes a
record has to update both or the game reads garbage.

Record payloads are frequently `chunkzip` compressed (deflate chunks with an
8/16-byte alignment grid).

Format knowledge is derived from the FIFA 14 Local FUT project
(https://github.com/KyroGeorge2/FIFA-14-Local-FUT) and re-verified against
retail FIFA 15 archives by `verify_archive_format()`.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

BIG_MAGICS = (b"BIGF", b"BIG4")
BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"


@dataclass(frozen=True)
class BigEntry:
    """A record as described by the plaintext BIG4 directory."""

    index: int
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class BhRecord:
    """A record as described by the hashed ViV4 sidecar index."""

    index: int
    offset: int
    size: int
    reserved: int
    path_hash: int
    table_offset: int


def djb2_path_hash(path: str) -> int:
    """djb2 over the exact record path string, as stored in the .bh index."""
    h = 5381
    for byte in path.encode("utf-8"):
        h = (h * 33 + byte) & 0xFFFFFFFFFFFFFFFF
    return h


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def parse_big(data: bytes) -> list[BigEntry]:
    if len(data) < 16 or data[:4] not in BIG_MAGICS:
        raise ValueError("not a BIGF/BIG4 archive")
    count = struct.unpack_from(">I", data, 8)[0]
    cursor = 16
    entries: list[BigEntry] = []
    for index in range(count):
        if cursor + 8 > len(data):
            raise ValueError("truncated BIG directory")
        offset, size = struct.unpack_from(">II", data, cursor)
        cursor += 8
        end = data.find(b"\x00", cursor)
        if end < 0:
            raise ValueError("unterminated BIG entry name")
        name = data[cursor:end].decode("utf-8", errors="replace")
        cursor = end + 1
        if offset + size > len(data):
            raise ValueError(f"BIG entry outside archive: {name}")
        entries.append(BigEntry(index=index, name=name, offset=offset, size=size))
    return entries


def parse_bh(data: bytes) -> list[BhRecord]:
    if len(data) < 16 or data[:4] != BH_MAGIC:
        raise ValueError("archive BH is not a ViV4 index")
    count = struct.unpack_from(">I", data, 8)[0]
    if 16 + count * 20 > len(data):
        raise ValueError("truncated ViV4 BH record table")
    records: list[BhRecord] = []
    pos = 16
    for index in range(count):
        offset, size, reserved, hi, lo = struct.unpack_from(">IIIII", data, pos)
        records.append(
            BhRecord(
                index=index,
                offset=offset,
                size=size,
                reserved=reserved,
                path_hash=(hi << 32) | lo,
                table_offset=pos,
            )
        )
        pos += 20
    return records


def is_chunkzip(payload: bytes) -> bool:
    return payload[:8] == CHUNKZIP_MAGIC


def decode_chunkzip(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    """Decode a chunkzip payload, returning the bytes and enough metadata to
    re-encode it byte-for-byte."""
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("not chunkzip")
    version, output_size, chunk_size, count, alignment, a, b, c = struct.unpack_from(
        ">IIIIIIII", payload, 8
    )
    if version != 2 or alignment not in (8, 16) or a or b or c or not 0 < count <= 4096:
        raise ValueError("unsupported chunkzip layout")
    pos = 40
    output = bytearray()
    chunks: list[dict[str, int]] = []
    for index in range(count):
        if pos + 8 > len(payload):
            raise ValueError("truncated chunkzip descriptor")
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        start = pos + 8
        end = start + stored_size
        if end > len(payload):
            raise ValueError("truncated chunkzip data")
        raw = payload[start:end]
        if compression_type == 0:
            decoded = raw
        elif compression_type == 1:
            decoded = zlib.decompress(raw, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"unsupported chunkzip compression type {compression_type}")
        output.extend(decoded)
        chunks.append(
            {
                "index": index,
                "stored_size": stored_size,
                "decoded_size": len(decoded),
                "compression_type": compression_type,
            }
        )
        pos = align(end + 8, alignment) - 8
    if len(output) != output_size:
        raise ValueError(f"chunkzip decoded size {len(output)} != declared {output_size}")
    return bytes(output), {
        "version": version,
        "output_size": output_size,
        "chunk_size": chunk_size,
        "chunk_count": count,
        "alignment": alignment,
        "chunks": chunks,
    }


def encode_chunkzip(decoded: bytes, info: dict[str, Any]) -> bytes:
    """Re-encode a payload using the chunk boundaries the archive declared.

    Preserving the original boundaries keeps an unchanged payload byte-identical,
    which makes patch verification meaningful.
    """
    chunk_size = int(info.get("chunk_size") or 262144)
    alignment = int(info.get("alignment") or 16)
    original_chunks = list(info.get("chunks") or [])

    parts: list[bytes] = []
    if len(decoded) == int(info.get("output_size", len(decoded))):
        cursor = 0
        for chunk in original_chunks:
            length = int(chunk.get("decoded_size", 0))
            parts.append(decoded[cursor : cursor + length])
            cursor += length
        if cursor != len(decoded):
            parts = []
    if not parts:
        parts = [decoded[i : i + chunk_size] for i in range(0, len(decoded), chunk_size)] or [b""]

    result = bytearray(
        CHUNKZIP_MAGIC + struct.pack(">IIIIIIII", 2, len(decoded), chunk_size, len(parts), alignment, 0, 0, 0)
    )
    for index, part in enumerate(parts):
        preferred = (
            int(original_chunks[index].get("compression_type", 1)) if index < len(original_chunks) else 1
        )
        if preferred == 0:
            stored, ctype = part, 0
        else:
            comp = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
            stored, ctype = comp.compress(part) + comp.flush(), 1
        result.extend(struct.pack(">II", len(stored), ctype))
        result.extend(stored)
        if index != len(parts) - 1:
            next_pos = align(len(result) + 8, alignment) - 8
            if next_pos > len(result):
                result.extend(b"\x00" * (next_pos - len(result)))
    return bytes(result)


class Archive:
    """A .big archive together with its .bh sidecar."""

    def __init__(self, big_path: Path):
        self.big_path = Path(big_path)
        self.bh_path = self.big_path.with_suffix(".bh")
        self._data: bytes | None = None
        self._bh_data: bytes | None = None
        self._entries: list[BigEntry] | None = None
        self._records: list[BhRecord] | None = None

    @property
    def data(self) -> bytes:
        if self._data is None:
            self._data = self.big_path.read_bytes()
        return self._data

    @property
    def bh_data(self) -> bytes | None:
        if self._bh_data is None and self.bh_path.exists():
            self._bh_data = self.bh_path.read_bytes()
        return self._bh_data

    @property
    def entries(self) -> list[BigEntry]:
        if self._entries is None:
            self._entries = parse_big(self.data)
        return self._entries

    @property
    def records(self) -> list[BhRecord]:
        if self._records is None:
            blob = self.bh_data
            self._records = parse_bh(blob) if blob else []
        return self._records

    def find(self, path: str) -> BigEntry | None:
        """Look up a record by its exact directory path (case-insensitive)."""
        wanted = path.replace("\\", "/").lower()
        for entry in self.entries:
            if entry.name.replace("\\", "/").lower() == wanted:
                return entry
        return None

    def find_by_basename(self, basename: str) -> list[BigEntry]:
        wanted = basename.lower()
        return [e for e in self.entries if e.name.replace("\\", "/").rsplit("/", 1)[-1].lower() == wanted]

    def raw(self, entry: BigEntry) -> bytes:
        return self.data[entry.offset : entry.offset + entry.size]

    def read(self, entry: BigEntry) -> bytes:
        """Return the decoded payload, transparently handling chunkzip."""
        payload = self.raw(entry)
        if is_chunkzip(payload):
            return decode_chunkzip(payload)[0]
        return payload

    def record_for(self, entry: BigEntry) -> BhRecord | None:
        """Find the .bh record that describes the same bytes as a BIG entry."""
        for record in self.records:
            if record.offset == entry.offset:
                return record
        return None

    def physical_capacity(self, offset: int) -> int:
        """Bytes available at `offset` before the next record starts.

        A payload may grow into its slack without relocating anything.
        """
        starts = [e.offset for e in self.entries if e.offset > offset]
        starts += [r.offset for r in self.records if r.offset > offset]
        return (min(starts) if starts else len(self.data)) - offset


def verify_archive_format(big_path: Path) -> dict[str, Any]:
    """Re-verify the inherited FIFA 14 format facts against a real archive.

    Checks: BIG4/ViV4 magics parse, both indexes agree on record count and
    offsets, and the djb2 path hash reproduces the .bh hashes.
    """
    archive = Archive(big_path)
    report: dict[str, Any] = {
        "archive": str(big_path),
        "big_magic": archive.data[:4].decode("ascii", "replace"),
        "big_entries": len(archive.entries),
        "bh_present": archive.bh_data is not None,
        "bh_records": len(archive.records),
    }

    offsets_big = {e.offset for e in archive.entries}
    offsets_bh = {r.offset for r in archive.records}
    report["offsets_match"] = offsets_big == offsets_bh if archive.records else None
    report["offsets_only_in_big"] = len(offsets_big - offsets_bh) if archive.records else None
    report["offsets_only_in_bh"] = len(offsets_bh - offsets_big) if archive.records else None

    # Hash check: does djb2 over the plaintext path reproduce the .bh hash for
    # records both indexes agree on?
    by_offset = {r.offset: r for r in archive.records}
    checked = matched = 0
    sample_mismatch: list[dict[str, Any]] = []
    for entry in archive.entries:
        record = by_offset.get(entry.offset)
        if record is None:
            continue
        checked += 1
        computed = djb2_path_hash(entry.name)
        if computed == record.path_hash:
            matched += 1
        elif len(sample_mismatch) < 3:
            sample_mismatch.append(
                {"name": entry.name, "stored": hex(record.path_hash), "djb2": hex(computed)}
            )
    report["hash_checked"] = checked
    report["hash_matched"] = matched
    report["hash_mismatch_samples"] = sample_mismatch

    compressed = sum(1 for e in archive.entries if is_chunkzip(archive.raw(e)))
    report["chunkzip_records"] = compressed
    return report


def iter_archives(game_root: Path) -> Iterator[Path]:
    for path in sorted(Path(game_root).glob("*.big")):
        yield path


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Inspect FIFA BIG4/ViV4 archives")
    parser.add_argument("--game-root", type=Path, default=Path(r"F:\Games\FIFA 15"))
    parser.add_argument("--archive", type=Path, help="a single .big to inspect")
    parser.add_argument("--verify", action="store_true", help="verify format facts")
    parser.add_argument("--list", action="store_true", help="list record paths")
    parser.add_argument("--grep", help="only list paths matching this substring")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    targets = [args.archive] if args.archive else list(iter_archives(args.game_root))

    for target in targets:
        if args.verify:
            print(json.dumps(verify_archive_format(target), indent=2))
        if args.list:
            archive = Archive(target)
            shown = 0
            for entry in archive.entries:
                if args.grep and args.grep.lower() not in entry.name.lower():
                    continue
                print(f"{target.name}\t{entry.index}\t{entry.offset}\t{entry.size}\t{entry.name}")
                shown += 1
                if args.limit and shown >= args.limit:
                    break
