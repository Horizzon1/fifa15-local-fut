"""Runtime configuration for the FIFA 15 local FUT server.

Port choices are deliberate: FIFA 15 gets its own range so it can coexist with
the FIFA 14 Local FUT server, which may still hold 42128/42129/8099/44125/8080.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The Blaze redirector hostname compiled into fifa15.exe. The client dials this
# first to discover the address of the real Blaze server.
REDIRECTOR_HOSTNAME = "spring14.gosredirector.ea.com"

# Blaze service name; fifa15.exe carries the template "fifa-2015-%s".
BLAZE_SERVICE_NAME = "fifa-2015-pc"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"

    # 42127 is the standard Blaze redirector port and was free on this machine,
    # so no port remapping is needed (FIFA 14 had to remap to 42129).
    redirector_port: int = 42127
    blaze_port: int = 42131
    fut_http_port: int = 8110
    static_http_port: int = 8111
    gosca_port: int = 44130

    cert_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "certs")
    cert_mode: str = "old-protossl"

    database: Path = field(default_factory=lambda: PROJECT_ROOT / "state" / "fifa15-fut.sqlite3")
    catalog: Path = field(default_factory=lambda: PROJECT_ROOT / "server" / "fifa15-player-catalog.json")

    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")

    # Trace mode logs every request, including ones with no handler yet, so the
    # client's real contract can be discovered instead of guessed.
    trace: bool = True

    @property
    def fut_base_url(self) -> str:
        return f"http://{self.host}:{self.fut_http_port}/"

    @property
    def static_base_url(self) -> str:
        return f"http://{self.host}:{self.static_http_port}/"

    def all_ports(self) -> list[int]:
        return [
            self.redirector_port,
            self.blaze_port,
            self.fut_http_port,
            self.static_http_port,
            self.gosca_port,
        ]
