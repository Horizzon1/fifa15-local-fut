"""Blaze redirector and main Blaze server for FIFA 15.

Two TCP listeners:

  * The **redirector** answers `Redirector.getServerInstance` with the address
    of the local main server. FIFA dials it over TLS, so it needs a certificate
    the client's ProtoSSL stack accepts.
  * The **main Blaze server** runs the session bootstrap: preAuth, login,
    postAuth, config fetches, then the notifications that make the client
    consider itself online.

Anything without a handler is logged with its decoded TDF body rather than
dropped, so the client's real contract can be observed instead of guessed.

Protocol shape follows the FIFA 14 Local FUT project
(https://github.com/KyroGeorge2/FIFA-14-Local-FUT).
"""
from __future__ import annotations

import json
import socket
import socketserver
import struct
import threading
from datetime import datetime, timezone
from ipaddress import IPv4Address
from pathlib import Path

from blaze import (
    COMPONENT_AUTHENTICATION,
    COMPONENT_GAME_MANAGER,
    COMPONENT_REDIRECTOR,
    COMPONENT_USER_SESSIONS,
    COMPONENT_UTIL,
    TDF_GROUP,
    TDF_STRING,
    TDF_VAR_INT,
    build_fire_notification,
    build_fire_response,
    decode_tdf,
    describe_frame,
    parse_fire_header,
    recv_fire_frame,
    tdf_bool,
    tdf_blob,
    tdf_empty_list,
    tdf_empty_map,
    tdf_group,
    tdf_list_u32,
    tdf_map_strings,
    tdf_string,
    tdf_tag,
    tdf_u16,
    tdf_u32,
    tdf_varint,
)
from config import BLAZE_SERVICE_NAME, ServerConfig
from tls_certs import create_tls_context

# The local account. One stable persona is enough for an offline server.
LOCAL_PERSONA_ID = 1_000_001
LOCAL_ACCOUNT_ID = 1_000_001
LOCAL_PERSONA_NAME = "LocalFUT"
LOCAL_EMAIL = "local@fifa15.localhost"

# Components FIFA 15 expects to be advertised in the preAuth response. Derived
# from the Blaze::* symbol set present in fifa15.exe.
ADVERTISED_COMPONENTS = (
    0x0001,  # Authentication
    0x0004,  # GameManager
    0x0007,  # Stats
    0x0009,  # Util
    0x000F,  # Messaging
    0x0019,  # AssociationLists
    0x001C,  # GameReporting
    0x0030,  # Census
    0x0064,  # Clubs
    0x07D0,  # Rooms
    0x0801,  # SponsoredEvents
    0x0803,  # EASFC
    0x7800,  # OSDK Settings
    0x7801,
    0x7802,  # UserSessions
    0x7803,
    0x7805,
    0x7806,
    0xF802,
)


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class TraceLog:
    """Line-delimited JSON trace shared by every listener."""

    def __init__(self, path: Path, echo: bool = True):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._echo = echo

    def emit(self, kind: str, **fields) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        line = json.dumps(record, default=str)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()
            if self._echo:
                print(line, flush=True)


# ---------------------------------------------------------------------------
# Response bodies
# ---------------------------------------------------------------------------

def build_redirector_body(host: str, port: int, secure: bool = False) -> bytes:
    """Tell the client where the real Blaze server lives."""
    valu = tdf_group(b"VALU", tdf_u32(b"IP", int(IPv4Address(host))) + tdf_u16(b"PORT", port))
    # ADDR is a tagged union; 0x00 selects the inline-address variant.
    return (
        tdf_tag(b"ADDR", 0x6)
        + b"\x00"
        + valu
        + tdf_bool(b"SECU", secure)
        + tdf_bool(b"XDNS", False)
    )


def build_pre_auth_body(service_name: str = BLAZE_SERVICE_NAME) -> bytes:
    client_config = tdf_map_strings(
        b"CONF",
        (
            ("pingPeriod", "30s"),
            ("voipHeadsetUpdateRate", "1000"),
            ("xlspConnectionIdleTimeout", "300"),
        ),
    )
    qos = (
        tdf_group(b"BWPS", tdf_string(b"PSA", "0") + tdf_u16(b"PSP", 0) + tdf_string(b"SNA", "prod-sjc"))
        + tdf_u16(b"LNP", 1)
        + tdf_empty_map(b"LTPS", TDF_STRING, TDF_GROUP)
        + tdf_u32(b"SVID", 0x45410805)
    )
    return (
        tdf_bool(b"ANON", False)
        + tdf_string(b"ASRC", "303107")
        + tdf_list_u32(b"CIDS", ADVERTISED_COMPONENTS)
        + tdf_string(b"CNGN", "")
        + tdf_group(b"CONF", client_config)
        + tdf_string(b"INST", service_name)
        + tdf_bool(b"MINR", False)
        + tdf_string(b"NASP", "cem_ea_id")
        + tdf_string(b"PILD", "")
        + tdf_string(b"PLAT", "pc")
        + tdf_string(b"PTAG", "")
        + tdf_group(b"QOSS", qos)
        + tdf_string(b"RSRC", "303107")
        + tdf_string(b"SVER", "Blaze 3.15 local FIFA 15\n")
    )


def build_post_auth_body(config: ServerConfig) -> bytes:
    """Telemetry, ticker and user-options bootstrap. All targets stay on loopback."""
    telemetry = tdf_group(
        b"TELE",
        tdf_string(b"ADRS", config.host)
        + tdf_blob(b"ANON")
        + tdf_string(b"DISA", "")
        + tdf_string(b"FILT", "")
        + tdf_u32(b"LOC", 1701729619)
        + tdf_string(b"NOOK", "")
        + tdf_u16(b"PORT", config.static_http_port)
        + tdf_u16(b"SDLY", 15000)
        + tdf_string(b"SESS", "localfut")
        + tdf_string(b"SKEY", "")
        + tdf_u16(b"SPCT", 75)
        + tdf_string(b"STIM", ""),
    )
    ticker = tdf_group(
        b"TICK",
        tdf_string(b"ADRS", config.host)
        + tdf_u16(b"PORT", config.static_http_port)
        + tdf_string(b"SKEY", "localfut"),
    )
    user_options = tdf_group(b"UROP", tdf_u16(b"TMOP", 0) + tdf_u32(b"UID", LOCAL_ACCOUNT_ID))
    return telemetry + ticker + user_options


def build_client_config(group: str, config: ServerConfig, returning_user: bool = False) -> bytes:
    """Config groups FIFA requests during the OSDK login state machine.

    The FUT_* URL keys are what actually point Ultimate Team at the local HTTP
    server, so FUT never needs a DNS redirect of its own.
    """
    fut = config.fut_base_url
    groups: dict[str, tuple[tuple[str, str], ...]] = {
        "OSDK_CORE": (
            ("JOIN_GAME_TIMEOUT", "60000"),
            ("OSDK_DISTBUFFERSIZE_IN", "32768"),
            ("OSDK_DISTBUFFERSIZE_OUT", "32768"),
            ("OSDK_KEEPALIVEINTERVAL", "30000"),
            ("OSDK_MATCHUP_TIMEOUT", "60000"),
            ("OSDK_MAXGAMES", "100"),
            ("OSDK_MAXROOMS", "100"),
            ("OSDK_PEERBUFFERSIZE", "32768"),
            ("OSDK_REGISTER_PRODUCT", "0"),
            ("OSDK_TICKER_COUNT", "0"),
        ),
        "OSDK_CLIENT": (
            ("FUT_URI", fut),
            ("FUT_RS4_BASE_URL", fut),
            ("FUT_RS4_APIURL_PC", fut),
            ("FUT_RS4_URL_PC", fut),
            ("FUT/MODULE_BASEURL_PC", fut),
            ("FUT/SINGLE_BASEURL_PC", fut),
            ("FUTBOOTCFGFILE_URL", config.static_base_url + "futBoot.xml"),
            # CardsDLL appends "/fut/" itself, so this base has no trailing slash.
            ("FUTDYNAMICMESSAGES_URL_BASE", f"http://{config.host}:{config.fut_http_port}"),
            ("FUTDYNAMICMESSAGES_URL_GET_MESSAGES", "/messages"),
            ("FUTDYNAMICMESSAGES_TUTORIAL_MSG_URL", "/tutorials"),
            ("FUTDYNAMICMESSAGES_REQUEST_TIMEOUT", "5000"),
            ("FUTDYNAMICMESSAGES_REFRESH_INTERVAL", "300000"),
            ("FUT/ROSTERUPDATE_URL", ""),
            ("CARDS/DIRECTED_BLAZEENV", "prod"),
            ("FCC/FUT_DEPLOY_LANGUAGE", "en_US"),
            ("FUT_ENABLE_MENU", "1"),
            ("ONLINE/NO_AUTO_SQUAD", "0"),
            ("FUT/FORCE_TUTORIALS", "0" if returning_user else "1"),
            ("FUT/DISABLE_TUTORIALS", "1" if returning_user else "0"),
            ("FUT/ALWAYS_SHOW_SMART_TUTORIALS", "0" if returning_user else "1"),
            ("FUT/IS_RETURNING_USER", "1" if returning_user else "0"),
            ("FUT_SKIP_ICEBREAKER_FLOW", "1" if returning_user else "0"),
            ("OSDK_DDP_UPGRADE_TO_DDR_ENABLED", "0"),
            ("OSDK_REGISTER_PRODUCT", "0"),
            ("OSDK_TOLLBOOTH_DDP_COMMERCE_ENABLED", "0"),
            ("OSDK_TOLLBOOTH_DDR_ONLINE_PASS_ENABLED", "0"),
            ("OSDK_TOLLBOOTH_ONLINE_PASS_ENABLED", "0"),
            ("OSDK_TOLLBOOTH_SEASON_TICKET_ENABLED", "0"),
            ("OSDK_TOLLBOOTH_SHOW_SEASON_TICKET_AT_LOGIN", "0"),
        ),
        "OSDK_NUCLEUS": (
            ("NUCLEUS_ADDED_URL", ""),
            ("NUCLEUS_CREATE_INFO_URL", ""),
            ("NUCLEUS_CREATE_URL", ""),
            ("NUCLEUS_DEACTIVATED_INFO_URL", ""),
            ("NUCLEUS_DUPACCT_INFO_URL", ""),
            ("NUCLEUS_INCOMPLETE_URL", ""),
            ("OSDK_EASW_ALLOWED_LOCALES", "en_US,en_GB"),
            ("OSDK_EASW_CONNECT_RETRY_PERIOD", "5"),
            ("OSDK_REGISTER_PRODUCT", "0"),
        ),
        "OSDK_WEBOFFER": (
            ("FAQ_URL", ""), ("MENU_ESPN_URL", ""), ("MENU_WEBGM0_URL", ""),
            ("MENU_WEBGM1_URL", ""), ("MENU_WEBGM2_URL", ""), ("NEWS_URL", ""),
            ("TOSAC_URL", ""), ("TOSA_URL", ""), ("WEB_OFFER_URL", ""),
        ),
        "OSDK_XMS_ABUSE_REPORTING": (
            ("OSDK_ABUSE_NUM_TYPES", "0"),
            ("OSDK_XMS_DEFAULT_VIEW_URL", ""),
        ),
    }
    return tdf_map_strings(b"CONF", groups.get(group, ()))


def build_login_body() -> bytes:
    """Blaze 3 login response with one stable local persona."""
    session = (
        tdf_string(b"BUID", str(LOCAL_PERSONA_ID))
        + tdf_bool(b"FRST", False)
        + tdf_string(b"KEY", "localfut-session-key")
        + tdf_u32(b"LLOG", _now())
        + tdf_string(b"MAIL", LOCAL_EMAIL)
        + tdf_group(
            b"PDTL",
            tdf_string(b"DSNM", LOCAL_PERSONA_NAME)
            + tdf_u32(b"LAST", _now())
            + tdf_u32(b"PID", LOCAL_PERSONA_ID)
            + tdf_u32(b"STAS", 0)
            + tdf_u32(b"XREF", 0)
            + tdf_u32(b"XTYP", 0),
        )
        + tdf_u32(b"UID", LOCAL_ACCOUNT_ID)
    )
    return (
        tdf_u32(b"AGUP", 0)
        + tdf_string(b"LDHT", "")
        + tdf_u16(b"NTOS", 0)
        + tdf_string(b"PCTK", "localfut-persona-token")
        + tdf_empty_list(b"PLST", TDF_GROUP)
        + tdf_string(b"PRIV", "")
        + tdf_string(b"SKEY", "localfut-session-key")
        + tdf_u16(b"SPAM", 0)
        + tdf_string(b"THST", "")
        + tdf_string(b"TSUI", "")
        + tdf_string(b"TURI", "")
        + tdf_group(b"USER", session)
    )


def build_user_session_body() -> bytes:
    """UserSessions notification payload: the client treats this as 'we are online'."""
    data = (
        tdf_group(
            b"ADDR",
            tdf_group(
                b"VALU",
                tdf_group(b"EXIP", tdf_u32(b"IP", 0) + tdf_u16(b"PORT", 0))
                + tdf_group(b"INIP", tdf_u32(b"IP", 0) + tdf_u16(b"PORT", 0)),
            ),
        )
        + tdf_string(b"BPS", "prod-sjc")
        + tdf_string(b"CTY", "US")
        + tdf_empty_map(b"CVAR", TDF_VAR_INT, TDF_VAR_INT)
        + tdf_empty_map(b"DMAP", TDF_VAR_INT, TDF_VAR_INT)
        + tdf_u16(b"HWFG", 0)
        + tdf_u32(b"ULMP", 0)
        + tdf_u32(b"USID", LOCAL_PERSONA_ID)
    )
    return (
        tdf_group(b"DATA", data)
        + tdf_u32(b"USID", LOCAL_PERSONA_ID)
        + tdf_group(
            b"USER",
            tdf_u32(b"AID", LOCAL_ACCOUNT_ID)
            + tdf_u32(b"ALOC", 1701729619)
            + tdf_blob(b"EXBB")
            + tdf_u32(b"EXID", 0)
            + tdf_u32(b"ID", LOCAL_PERSONA_ID)
            + tdf_string(b"NAME", LOCAL_PERSONA_NAME),
        )
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

class BlazeConnectionHandler(socketserver.BaseRequestHandler):
    """Serves one Blaze TCP connection, framing FIRE messages."""

    config: ServerConfig
    trace: TraceLog
    role: str = "blaze"

    def handle(self) -> None:
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        self.trace.emit("connection-open", role=self.role, peer=peer)
        sent_session = False
        try:
            while True:
                frame = recv_fire_frame(self.request)
                if len(frame) < 12:
                    break

                header = parse_fire_header(frame)
                component = header["component"]
                command = header["command"]
                self.trace.emit(
                    "request", role=self.role, peer=peer,
                    component=f"{component:#06x}", command=f"{command:#06x}",
                    seq=header["sequence"], detail=describe_frame(frame),
                )

                body = self.dispatch(component, command, decode_tdf(frame[12:]))
                if body is None:
                    # No handler yet: an empty success keeps the client's state
                    # machine moving and the trace records what was missed.
                    self.trace.emit(
                        "unhandled", role=self.role,
                        component=f"{component:#06x}", command=f"{command:#06x}",
                    )
                    body = b""

                self.request.sendall(build_fire_response(frame, body))

                # Once the client has authenticated, push the notifications that
                # flip it into the online state.
                if component == COMPONENT_AUTHENTICATION and not sent_session:
                    sent_session = True
                    self.push_session_notifications()

        except (ConnectionResetError, ConnectionAbortedError, OSError) as exc:
            self.trace.emit("connection-error", role=self.role, peer=peer, error=str(exc))
        finally:
            self.trace.emit("connection-close", role=self.role, peer=peer)

    def returning_user(self) -> bool:
        """A club that already exists must not be sent back through the tutorial.

        Resolved lazily so the Blaze layer stays decoupled from the store.
        """
        resolver = getattr(self, "club_exists", None)
        return bool(resolver()) if callable(resolver) else False

    def push_session_notifications(self) -> None:
        body = build_user_session_body()
        for command in (0x0002, 0x0005, 0x0008):  # UserAdded, UserUpdated, UserAuthenticated
            try:
                self.request.sendall(
                    build_fire_notification(COMPONENT_USER_SESSIONS, command, body)
                )
                self.trace.emit("notification", component="0x7802", command=f"{command:#06x}")
            except OSError as exc:
                self.trace.emit("notification-failed", command=f"{command:#06x}", error=str(exc))
                return

    def dispatch(self, component: int, command: int, request: dict) -> bytes | None:
        config = self.config

        if component == COMPONENT_REDIRECTOR and command == 0x0001:
            self.trace.emit(
                "redirector-reply", host=config.host, port=config.blaze_port
            )
            return build_redirector_body(config.host, config.blaze_port)

        if component == COMPONENT_UTIL:
            if command == 0x0007:  # preAuth
                return build_pre_auth_body()
            if command == 0x0008:  # postAuth
                return build_post_auth_body(config)
            if command == 0x0002:  # ping
                return tdf_u32(b"STIM", _now())
            if command == 0x0001:  # fetchClientConfig
                # CFID names the config group the client wants.
                group = str(request.get("CFID", "") or "")
                self.trace.emit("fetch-client-config", group=group)
                return build_client_config(group, config, returning_user=self.returning_user())
            if command in (0x000B, 0x000C):  # user settings save / load all
                return b""

        if component == COMPONENT_AUTHENTICATION:
            # Every login variant the client may try resolves to the same local
            # persona: login, originLogin, listUserEntitlements, getPersona...
            if command in (0x0028, 0x0032, 0x003C, 0x0014, 0x0015, 0x001D):
                return build_login_body()
            return build_login_body()

        if component == COMPONENT_USER_SESSIONS:
            return build_user_session_body()

        if component == COMPONENT_GAME_MANAGER:
            return b""

        return None


class RedirectorHandler(BlazeConnectionHandler):
    role = "redirector"


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class TlsThreadingTCPServer(ReusableThreadingTCPServer):
    """Wraps each accepted socket in TLS before the handler sees it."""

    ssl_context = None

    def get_request(self):
        sock, addr = super().get_request()
        try:
            return self.ssl_context.wrap_socket(sock, server_side=True), addr
        except OSError:
            sock.close()
            raise


def make_handler(base, config: ServerConfig, trace: TraceLog):
    return type(
        f"Bound{base.__name__}",
        (base,),
        {"config": config, "trace": trace},
    )


def start_blaze_servers(config: ServerConfig, trace: TraceLog) -> list[threading.Thread]:
    """Start the redirector (TLS) and the main Blaze listener (plain)."""
    threads: list[threading.Thread] = []

    context, ca_path = create_tls_context(
        "spring14.gosredirector.ea.com", config.cert_dir, config.cert_mode
    )
    trace.emit("tls-ready", mode=config.cert_mode, ca=str(ca_path))

    redirector_server = TlsThreadingTCPServer(
        (config.host, config.redirector_port),
        make_handler(RedirectorHandler, config, trace),
        bind_and_activate=False,
    )
    redirector_server.ssl_context = context
    redirector_server.server_bind()
    redirector_server.server_activate()

    blaze_server = ReusableThreadingTCPServer(
        (config.host, config.blaze_port),
        make_handler(BlazeConnectionHandler, config, trace),
    )

    for name, server in (("redirector", redirector_server), ("blaze", blaze_server)):
        thread = threading.Thread(target=server.serve_forever, name=name, daemon=True)
        thread.start()
        threads.append(thread)
        trace.emit("listener-started", role=name, address=f"{server.server_address[0]}:{server.server_address[1]}")

    return threads
