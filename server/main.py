"""Entry point: start every FIFA 15 local FUT listener.

  redirector (TLS)  -> tells the client where Blaze lives
  blaze             -> session bootstrap, config, login
  fut-http          -> the Ultimate Team REST surface
  static-http       -> futBoot.xml and CDN stand-ins
"""
from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from blaze_server import TraceLog, start_blaze_servers  # noqa: E402
from config import ServerConfig  # noqa: E402
from fut_http import start_fut_http  # noqa: E402
from identity import IdentityStore, PlayerCatalog  # noqa: E402
from market import TransferMarket  # noqa: E402

FUT_BOOT_XML = """<?xml version="1.0" encoding="utf-8"?>
<FutCfg>
  <Cfg name="enabled" value="1"/>
  <Cfg name="maintenance" value="0"/>
</FutCfg>
"""


class DualStackHTTPServer(ThreadingHTTPServer):
    """Accepts both 127.0.0.1 and ::1; Windows prefers ::1 for "localhost"."""

    allow_reuse_address = True
    daemon_threads = True
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


class StaticHandler(BaseHTTPRequestHandler):
    """futBoot.xml plus empty stand-ins for the EA CDN."""

    trace = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        return

    def do_GET(self):  # noqa: N802
        lower = self.path.lower()
        if "futboot" in lower:
            body, content_type = FUT_BOOT_XML.encode(), "text/xml; charset=utf-8"
        elif lower.endswith((".png", ".jpg", ".jpeg", ".big")):
            # Never fabricate art; the client handles a 404 cleanly.
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        else:
            body, content_type = b"<MESSAGES></MESSAGES>", "text/xml; charset=utf-8"

        if self.trace:
            self.trace.emit("static-http", path=self.path)
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PUT = do_GET


def main() -> int:
    config = ServerConfig()
    parser = argparse.ArgumentParser(description="FIFA 15 local FUT server")
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--redirector-port", type=int, default=config.redirector_port)
    parser.add_argument("--blaze-port", type=int, default=config.blaze_port)
    parser.add_argument("--fut-http-port", type=int, default=config.fut_http_port)
    parser.add_argument("--static-http-port", type=int, default=config.static_http_port)
    parser.add_argument("--database", type=Path, default=config.database)
    parser.add_argument("--catalog", type=Path, default=config.catalog)
    parser.add_argument("--cert-mode", default=config.cert_mode,
                        choices=["old-protossl", "sha1", "sha256"])
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true", help="do not echo the trace to stdout")
    args = parser.parse_args()

    config.host = args.host
    config.redirector_port = args.redirector_port
    config.blaze_port = args.blaze_port
    config.fut_http_port = args.fut_http_port
    config.static_http_port = args.static_http_port
    config.database = args.database
    config.catalog = args.catalog
    config.cert_mode = args.cert_mode

    log_path = args.log or (config.log_dir / f"trace-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    trace = TraceLog(log_path, echo=not args.quiet)
    trace.emit("startup", game="fifa15", ports=config.all_ports(), log=str(log_path))

    if not config.catalog.exists():
        trace.emit("fatal", error=f"player catalog missing: {config.catalog}",
                   fix="run tools/extract_catalog.py")
        return 1

    catalog = PlayerCatalog(config.catalog)
    store = IdentityStore(config.database, catalog)
    market = TransferMarket(store)
    trace.emit("store-ready", database=str(config.database), summary=store.summary())

    servers = []
    try:
        start_blaze_servers(config, trace)

        fut_server = start_fut_http(config, store, market, trace)
        threading.Thread(target=fut_server.serve_forever, name="fut-http", daemon=True).start()
        servers.append(fut_server)

        static_handler = type("BoundStatic", (StaticHandler,), {"trace": trace})
        static_server = DualStackHTTPServer(("::", config.static_http_port), static_handler)
        threading.Thread(target=static_server.serve_forever, name="static-http", daemon=True).start()
        servers.append(static_server)
        trace.emit("listener-started", role="static-http",
                   address=f"{config.host}:{config.static_http_port}")

    except OSError as exc:
        trace.emit("fatal", error=f"could not bind a listener: {exc}")
        return 1

    trace.emit("ready",
               redirector=f"{config.host}:{config.redirector_port}",
               blaze=f"{config.host}:{config.blaze_port}",
               futHttp=config.fut_base_url,
               staticHttp=config.static_base_url)
    print("READY", flush=True)

    stopping = threading.Event()

    def shutdown(_signum=None, _frame=None):
        stopping.set()

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (AttributeError, ValueError):
        pass

    try:
        while not stopping.is_set():
            stopping.wait(1.0)
    except KeyboardInterrupt:
        pass

    trace.emit("shutdown")
    for server in servers:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
