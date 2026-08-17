"""Drive the Blaze layer the way FIFA 15 would, without the game.

The game cannot boot on this machine yet, so this stands in for it: it performs
the real client sequence over the real sockets — TLS to the redirector, then
FIRE-framed TDF over the main Blaze port — and checks the responses decode back
to what we intended to send.

This is what de-risks milestone 1: if the handshake is wrong here, it would be
wrong for the client too.
"""
from __future__ import annotations

import socket
import ssl
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.stdout.reconfigure(encoding="utf-8")

from blaze import (  # noqa: E402
    COMPONENT_AUTHENTICATION,
    COMPONENT_REDIRECTOR,
    COMPONENT_USER_SESSIONS,
    COMPONENT_UTIL,
    FIRE_TYPE_REQUEST,
    decode_tdf,
    parse_fire_header,
    recv_fire_frame,
    tdf_string,
    tdf_u32,
)
from config import BLAZE_SERVICE_NAME, ServerConfig  # noqa: E402

import struct  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def build_request(component: int, command: int, sequence: int, body: bytes = b"") -> bytes:
    return struct.pack(">HHHHBBH", len(body), component, command, 0,
                       FIRE_TYPE_REQUEST, 0x00, sequence) + body


def exchange(sock, component: int, command: int, sequence: int, body: bytes = b"") -> dict:
    """Send one FIRE request and read frames until the matching response lands.

    Notifications can arrive interleaved, so they are collected separately.
    """
    sock.sendall(build_request(component, command, sequence, body))
    notifications = []
    deadline = time.time() + 10
    while time.time() < deadline:
        frame = recv_fire_frame(sock)
        if len(frame) < 12:
            return {"error": "connection closed", "notifications": notifications}
        header = parse_fire_header(frame)
        payload = decode_tdf(frame[12:])
        if header["type"] == 0x2:  # notification
            notifications.append({"header": header, "body": payload})
            continue
        return {"header": header, "body": payload, "notifications": notifications}
    return {"error": "timeout", "notifications": notifications}


def main() -> int:
    config = ServerConfig()

    print("-- redirector (TLS) --")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1
    try:
        context.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        pass

    try:
        raw = socket.create_connection((config.host, config.redirector_port), timeout=10)
        tls = context.wrap_socket(raw, server_hostname="spring14.gosredirector.ea.com")
    except Exception as exc:
        check("TLS handshake with the redirector", False, repr(exc))
        print("\nThe redirector could not be reached. Is the server running?")
        return 1

    check("TLS handshake with the redirector", True, f"cipher {tls.cipher()[0]}")
    peer = tls.getpeercert(binary_form=True)
    check("redirector presents a certificate", bool(peer), f"{len(peer or b'')} DER bytes")

    # The real client asks for its server instance by service name.
    body = tdf_string(b"BSDK", "3.15.0.0") + tdf_string(b"BTIM", "Jan 01 2015") \
        + tdf_string(b"CLNT", "fifa-2015-pc") + tdf_string(b"NAME", BLAZE_SERVICE_NAME)
    result = exchange(tls, COMPONENT_REDIRECTOR, 0x0001, 1, body)
    check("redirector answers getServerInstance", "body" in result, str(result.get("error", "")))

    if "body" in result:
        payload = result["body"]
        addr = payload.get("ADDR") or {}
        valu = addr.get("value", addr).get("VALU") if isinstance(addr, dict) else {}
        valu = valu or {}
        ip = valu.get("IP")
        port = valu.get("PORT")
        check("redirector returns an address", ip is not None and port is not None,
              f"IP={ip} PORT={port}")
        check("it points at our Blaze port", port == config.blaze_port,
              f"got {port}, expected {config.blaze_port}")
        expected_ip = int.from_bytes(bytes(int(o) for o in config.host.split(".")), "big")
        check("it points at loopback", ip == expected_ip, f"got {ip}, expected {expected_ip}")
    tls.close()

    print("\n-- main Blaze session --")
    try:
        blaze = socket.create_connection((config.host, config.blaze_port), timeout=10)
    except Exception as exc:
        check("connect to the Blaze port", False, repr(exc))
        return 1
    check("connect to the Blaze port", True)

    # preAuth: the client's first real call. It reads the component list and
    # service name out of this.
    pre = exchange(blaze, COMPONENT_UTIL, 0x0007, 2)
    check("preAuth responds", "body" in pre, str(pre.get("error", "")))
    if "body" in pre:
        payload = pre["body"]
        check("preAuth carries the service name", payload.get("INST") == BLAZE_SERVICE_NAME,
              str(payload.get("INST")))
        check("preAuth advertises the platform", payload.get("PLAT") == "pc",
              str(payload.get("PLAT")))
        components = payload.get("CIDS") or []
        check("preAuth advertises components", len(components) >= 15, f"{len(components)} components")
        check("component list includes Authentication", COMPONENT_AUTHENTICATION in components)
        check("component list includes UserSessions", COMPONENT_USER_SESSIONS in components)
        check("preAuth includes QoS block", "QOSS" in payload)

    # fetchClientConfig for each group the client asks for.
    print("\n-- client config --")
    for sequence, group in enumerate(
        ("OSDK_CORE", "OSDK_CLIENT", "OSDK_NUCLEUS", "OSDK_WEBOFFER", "OSDK_XMS_ABUSE_REPORTING"),
        start=3,
    ):
        response = exchange(blaze, COMPONENT_UTIL, 0x0001, sequence, tdf_string(b"CFID", group))
        config_map = (response.get("body") or {}).get("CONF") or {}
        check(f"{group} returns settings", len(config_map) > 0, f"{len(config_map)} keys")

        if group == "OSDK_CLIENT":
            # These are what actually point Ultimate Team at the local server.
            check("FUT_URI points at our HTTP server",
                  config_map.get("FUT_URI") == config.fut_base_url,
                  str(config_map.get("FUT_URI")))
            check("FUT_RS4_BASE_URL points at our HTTP server",
                  config_map.get("FUT_RS4_BASE_URL") == config.fut_base_url,
                  str(config_map.get("FUT_RS4_BASE_URL")))
            check("dynamic messages base has no trailing slash",
                  str(config_map.get("FUTDYNAMICMESSAGES_URL_BASE", "")).endswith(
                      str(config.fut_http_port)),
                  str(config_map.get("FUTDYNAMICMESSAGES_URL_BASE")))
            check("FUT menu is enabled", config_map.get("FUT_ENABLE_MENU") == "1")

    print("\n-- login --")
    login_body = tdf_string(b"MAIL", "local@fifa15.localhost") + tdf_string(b"PASS", "local")
    login = exchange(blaze, COMPONENT_AUTHENTICATION, 0x0028, 20, login_body)
    check("login responds", "body" in login, str(login.get("error", "")))
    if "body" in login:
        payload = login["body"]
        user = payload.get("USER") or {}
        check("login returns a session key", bool(payload.get("SKEY")), str(payload.get("SKEY")))
        check("login returns a persona", bool(user.get("PDTL")), str(list(user)[:6]))
        details = user.get("PDTL") or {}
        check("persona has a display name", bool(details.get("DSNM")), str(details.get("DSNM")))
        check("persona has an id", details.get("PID", 0) > 0, str(details.get("PID")))

    # The client considers itself online once UserSessions notifications land.
    notifications = login.get("notifications", [])
    if not notifications:
        # They may arrive just after the response; drain briefly.
        blaze.settimeout(3)
        try:
            while True:
                frame = recv_fire_frame(blaze)
                if len(frame) < 12:
                    break
                header = parse_fire_header(frame)
                if header["type"] == 0x2:
                    notifications.append({"header": header, "body": decode_tdf(frame[12:])})
                if len(notifications) >= 3:
                    break
        except (socket.timeout, OSError):
            pass

    print("\n-- session notifications --")
    commands = {n["header"]["command"] for n in notifications}
    check("UserAuthenticated notification sent", 0x0008 in commands, f"got {sorted(commands)}")
    check("UserAdded notification sent", 0x0002 in commands)
    check("all notifications are on UserSessions",
          all(n["header"]["component"] == COMPONENT_USER_SESSIONS for n in notifications),
          f"{len(notifications)} notifications")

    print("\n-- postAuth --")
    post = exchange(blaze, COMPONENT_UTIL, 0x0008, 30)
    check("postAuth responds", "body" in post, str(post.get("error", "")))
    if "body" in post:
        check("postAuth carries telemetry block", "TELE" in post["body"], str(list(post["body"])))

    print("\n-- ping --")
    ping = exchange(blaze, COMPONENT_UTIL, 0x0002, 40)
    stim = (ping.get("body") or {}).get("STIM", 0)
    check("ping returns server time", abs(stim - int(time.time())) < 120, str(stim))

    blaze.close()

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S):"))
    for failure in failures:
        print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
