"""Blaze/FIRE wire protocol for FIFA 15.

EA's Blaze SDK frames every request as a 12-byte FIRE header followed by a TDF
body. TDF is a tagged binary format: each field carries a 3-byte packed tag, a
1-byte type, and a type-dependent payload.

FIFA 15 speaks the same dialect as FIFA 14, so the encoding here is shared;
only the service name, component set and payload contents differ.

Encoding derived from the FIFA 14 Local FUT project
(https://github.com/KyroGeorge2/FIFA-14-Local-FUT), which in turn follows the
PocketRelay `tdf` crate.
"""
from __future__ import annotations

import struct
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# TDF value types
# ---------------------------------------------------------------------------
TDF_VAR_INT = 0x0
TDF_STRING = 0x1
TDF_BLOB = 0x2
TDF_GROUP = 0x3
TDF_LIST = 0x4
TDF_MAP = 0x5
TDF_TAGGED_UNION = 0x6
TDF_VAR_INT_LIST = 0x7
TDF_OBJECT_TYPE = 0x8
TDF_OBJECT_ID = 0x9

# ---------------------------------------------------------------------------
# FIRE frame types
# ---------------------------------------------------------------------------
FIRE_TYPE_REQUEST = 0x00
FIRE_TYPE_RESPONSE = 0x10
FIRE_TYPE_NOTIFICATION = 0x20
FIRE_TYPE_ERROR = 0x30

# ---------------------------------------------------------------------------
# Blaze components FIFA 15 actually talks to. Command numbers are annotated
# where the client's behaviour has been observed.
# ---------------------------------------------------------------------------
COMPONENT_AUTHENTICATION = 0x0001
COMPONENT_GAME_MANAGER = 0x0004
COMPONENT_REDIRECTOR = 0x0005
COMPONENT_STATS = 0x0007
COMPONENT_UTIL = 0x0009
COMPONENT_MESSAGING = 0x000F
COMPONENT_ASSOCIATION_LISTS = 0x0019
COMPONENT_GAME_REPORTING = 0x001C
COMPONENT_USER_SESSIONS = 0x7802

# Util commands
UTIL_PRE_AUTH = 0x0007
UTIL_POST_AUTH = 0x0008
UTIL_PING = 0x0002
UTIL_FETCH_CLIENT_CONFIG = 0x0001
UTIL_USER_SETTINGS_SAVE = 0x000B
UTIL_USER_SETTINGS_LOAD_ALL = 0x000C

# Redirector commands
REDIRECTOR_GET_SERVER_INSTANCE = 0x0001

# UserSessions notifications
USER_SESSIONS_USER_ADDED = 0x0002
USER_SESSIONS_USER_UPDATED = 0x0005
USER_SESSIONS_USER_AUTHENTICATED = 0x0008


# ---------------------------------------------------------------------------
# Tag packing
# ---------------------------------------------------------------------------

def tdf_tag(tag: bytes, value_type: int) -> bytes:
    """Pack a 4-character ASCII tag plus its type into 4 bytes.

    Blaze squeezes four 6-bit characters into three bytes, then appends the
    value type.
    """
    if not tag or len(tag) > 4:
        raise ValueError(f"invalid TDF tag {tag!r}")
    out = [0, 0, 0, value_type & 0xFF]
    length = len(tag)
    if length > 0:
        out[0] |= (tag[0] & 0x40) << 1
        out[0] |= (tag[0] & 0x10) << 2
        out[0] |= (tag[0] & 0x0F) << 2
    if length > 1:
        out[0] |= (tag[1] & 0x40) >> 5
        out[0] |= (tag[1] & 0x10) >> 4
        out[1] |= (tag[1] & 0x0F) << 4
    if length > 2:
        out[1] |= (tag[2] & 0x40) >> 3
        out[1] |= (tag[2] & 0x10) >> 2
        out[1] |= (tag[2] & 0x0C) >> 2
        out[2] |= (tag[2] & 0x03) << 6
    if length > 3:
        out[2] |= (tag[3] & 0x40) >> 1
        out[2] |= tag[3] & 0x1F
    return bytes(out)


def decode_tdf_tag(encoded: bytes) -> str:
    """Unpack the three-byte tag representation back to ASCII."""
    if len(encoded) != 3:
        raise ValueError("a TDF tag must contain exactly three encoded bytes")
    values = (
        (encoded[0] >> 2) & 0x3F,
        ((encoded[0] & 0x03) << 4) | ((encoded[1] >> 4) & 0x0F),
        ((encoded[1] & 0x0F) << 2) | ((encoded[2] >> 6) & 0x03),
        encoded[2] & 0x3F,
    )
    return "".join(chr(value + 0x20) for value in values).rstrip()


# ---------------------------------------------------------------------------
# Varints
# ---------------------------------------------------------------------------

def tdf_varint(value: int) -> bytes:
    """Blaze varint: 6 payload bits in the first byte, 7 in each continuation."""
    if value < 0:
        raise ValueError("negative TDF varints are not used by this protocol")
    if value < 0x40:
        return bytes([value])
    out = bytearray()
    out.append((value & 0x3F) | 0x80)
    value >>= 6
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_tdf_varint(payload: bytes, offset: int = 0) -> tuple[int, int]:
    if offset >= len(payload):
        raise ValueError("missing TDF varint")
    first = payload[offset]
    offset += 1
    value = first & 0x3F
    shift = 6
    current = first
    while current & 0x80:
        if offset >= len(payload):
            raise ValueError("truncated TDF varint")
        current = payload[offset]
        offset += 1
        value |= (current & 0x7F) << shift
        shift += 7
    return value, offset


# ---------------------------------------------------------------------------
# Field encoders
# ---------------------------------------------------------------------------

def tdf_u32(tag: bytes, value: int) -> bytes:
    return tdf_tag(tag, TDF_VAR_INT) + tdf_varint(value)


tdf_u16 = tdf_u32
tdf_u64 = tdf_u32


def tdf_bool(tag: bytes, value: bool) -> bytes:
    return tdf_tag(tag, TDF_VAR_INT) + tdf_varint(1 if value else 0)


def tdf_raw_string(value: str) -> bytes:
    """A TDF string is a NUL-terminated UTF-8 blob prefixed with its length."""
    encoded = value.encode("utf-8") + b"\x00"
    return tdf_varint(len(encoded)) + encoded


def tdf_string(tag: bytes, value: str) -> bytes:
    return tdf_tag(tag, TDF_STRING) + tdf_raw_string(value)


def tdf_blob(tag: bytes, value: bytes = b"") -> bytes:
    return tdf_tag(tag, TDF_BLOB) + tdf_varint(len(value)) + value


def tdf_group(tag: bytes, body: bytes) -> bytes:
    """Groups are terminated by a 0x00 byte."""
    return tdf_tag(tag, TDF_GROUP) + body + b"\x00"


def tdf_list_u32(tag: bytes, values: Sequence[int]) -> bytes:
    out = tdf_tag(tag, TDF_LIST) + bytes([TDF_VAR_INT]) + tdf_varint(len(values))
    return out + b"".join(tdf_varint(v) for v in values)


def tdf_list_strings(tag: bytes, values: Sequence[str]) -> bytes:
    out = tdf_tag(tag, TDF_LIST) + bytes([TDF_STRING]) + tdf_varint(len(values))
    return out + b"".join(tdf_raw_string(v) for v in values)


def tdf_list_groups(tag: bytes, values: Sequence[bytes]) -> bytes:
    out = tdf_tag(tag, TDF_LIST) + bytes([TDF_GROUP]) + tdf_varint(len(values))
    return out + b"".join(body + b"\x00" for body in values)


def tdf_empty_list(tag: bytes, value_type: int) -> bytes:
    return tdf_tag(tag, TDF_LIST) + bytes([value_type]) + tdf_varint(0)


def tdf_map_strings(tag: bytes, pairs: Sequence[tuple[str, str]]) -> bytes:
    out = tdf_tag(tag, TDF_MAP) + bytes([TDF_STRING, TDF_STRING]) + tdf_varint(len(pairs))
    for key, value in pairs:
        out += tdf_raw_string(key) + tdf_raw_string(value)
    return out


def tdf_map_u32(tag: bytes, pairs: Sequence[tuple[int, int]]) -> bytes:
    out = tdf_tag(tag, TDF_MAP) + bytes([TDF_VAR_INT, TDF_VAR_INT]) + tdf_varint(len(pairs))
    for key, value in pairs:
        out += tdf_varint(key) + tdf_varint(value)
    return out


def tdf_empty_map(tag: bytes, key_type: int, value_type: int) -> bytes:
    return tdf_tag(tag, TDF_MAP) + bytes([key_type, value_type]) + tdf_varint(0)


# ---------------------------------------------------------------------------
# Decoding (for tracing what the client actually sends)
# ---------------------------------------------------------------------------

def _decode_value(payload: bytes, offset: int, value_type: int) -> tuple[object, int]:
    if value_type == TDF_VAR_INT:
        return read_tdf_varint(payload, offset)
    if value_type == TDF_STRING:
        length, offset = read_tdf_varint(payload, offset)
        raw = payload[offset : offset + length]
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace"), offset + length
    if value_type == TDF_BLOB:
        length, offset = read_tdf_varint(payload, offset)
        return payload[offset : offset + length], offset + length
    if value_type == TDF_GROUP:
        fields, offset = _decode_fields(payload, offset, stop_on_terminator=True)
        return fields, offset
    if value_type == TDF_LIST:
        item_type = payload[offset]
        offset += 1
        count, offset = read_tdf_varint(payload, offset)
        items = []
        for _ in range(count):
            item, offset = _decode_value(payload, offset, item_type)
            items.append(item)
        return items, offset
    if value_type == TDF_MAP:
        key_type, value_type_inner = payload[offset], payload[offset + 1]
        offset += 2
        count, offset = read_tdf_varint(payload, offset)
        pairs = {}
        for _ in range(count):
            key, offset = _decode_value(payload, offset, key_type)
            value, offset = _decode_value(payload, offset, value_type_inner)
            pairs[key if isinstance(key, (int, str)) else str(key)] = value
        return pairs, offset
    if value_type == TDF_VAR_INT_LIST:
        count, offset = read_tdf_varint(payload, offset)
        items = []
        for _ in range(count):
            item, offset = read_tdf_varint(payload, offset)
            items.append(item)
        return items, offset
    if value_type == TDF_OBJECT_TYPE:
        first, offset = read_tdf_varint(payload, offset)
        second, offset = read_tdf_varint(payload, offset)
        return (first, second), offset
    if value_type == TDF_OBJECT_ID:
        first, offset = read_tdf_varint(payload, offset)
        second, offset = read_tdf_varint(payload, offset)
        third, offset = read_tdf_varint(payload, offset)
        return (first, second, third), offset
    if value_type == TDF_TAGGED_UNION:
        key = payload[offset]
        offset += 1
        if key == 0x7F:  # unset
            return None, offset
        value, offset = _decode_fields(payload, offset, stop_on_terminator=True)
        return {"union": key, "value": value}, offset
    raise ValueError(f"unsupported TDF value type {value_type:#x}")


def _decode_fields(payload: bytes, offset: int, stop_on_terminator: bool) -> tuple[dict, int]:
    fields: dict[str, object] = {}
    while offset < len(payload):
        if stop_on_terminator and payload[offset] == 0x00:
            return fields, offset + 1
        if offset + 4 > len(payload):
            break
        tag = decode_tdf_tag(payload[offset : offset + 3])
        value_type = payload[offset + 3]
        offset += 4
        try:
            value, offset = _decode_value(payload, offset, value_type)
        except (ValueError, IndexError):
            break
        fields[tag] = value
    return fields, offset


def decode_tdf(payload: bytes) -> dict:
    """Best-effort decode of a TDF body, for logging what the client sent."""
    try:
        return _decode_fields(payload, 0, stop_on_terminator=False)[0]
    except Exception as exc:
        return {"_decode_error": str(exc), "_bytes": payload[:64].hex()}


# ---------------------------------------------------------------------------
# FIRE framing
# ---------------------------------------------------------------------------

def parse_fire_header(frame: bytes) -> dict:
    if len(frame) < 12:
        return {}
    length, component, command, error, type_options, options, sequence = struct.unpack_from(
        ">HHHHBBH", frame, 0
    )
    return {
        "length": length,
        "component": component,
        "command": command,
        "error": error,
        "type": type_options >> 4,
        "options": options >> 4,
        "sequence": sequence,
    }


def build_fire_response(request_frame: bytes, body: bytes) -> bytes:
    header = parse_fire_header(request_frame)
    return (
        struct.pack(
            ">HHHHBBH",
            len(body),
            header.get("component", 0),
            header.get("command", 0),
            0,
            FIRE_TYPE_RESPONSE,
            0x00,
            header.get("sequence", 0),
        )
        + body
    )


def build_fire_error(request_frame: bytes, error: int, body: bytes = b"") -> bytes:
    header = parse_fire_header(request_frame)
    return (
        struct.pack(
            ">HHHHBBH",
            len(body),
            header.get("component", 0),
            header.get("command", 0),
            error & 0xFFFF,
            FIRE_TYPE_ERROR,
            0x00,
            header.get("sequence", 0),
        )
        + body
    )


def build_fire_notification(component: int, command: int, body: bytes) -> bytes:
    return struct.pack(">HHHHBBH", len(body), component, command, 0, FIRE_TYPE_NOTIFICATION, 0x00, 0) + body


def recv_exact(sock, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_fire_frame(sock) -> bytes:
    """Read one complete FIRE frame: 12-byte header plus its declared body."""
    header = recv_exact(sock, 12)
    if len(header) < 12:
        return header
    length = int.from_bytes(header[0:2], "big")
    return header + recv_exact(sock, length)


def describe_frame(frame: bytes) -> dict:
    """Header fields plus a decoded body, for the trace log."""
    header = parse_fire_header(frame)
    if not header:
        return {"raw": frame[:32].hex()}
    header["body"] = decode_tdf(frame[12:])
    return header
