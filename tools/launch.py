"""Launch FIFA 15 against the local FUT server.

Sequence:
  1. Start the local servers and wait for READY.
  2. Spawn fifa15.exe **suspended** via frida, so the redirect hook is installed
     before the game resolves a single hostname.
  3. Resume the game and stream the hook's decisions into the trace log.

No elevation anywhere: the redirect happens inside the process, so the hosts
file is never touched.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.stdout.reconfigure(encoding="utf-8")

from config import ServerConfig  # noqa: E402

HOOK_SCRIPT = ROOT / "tools" / "redirect_hook.js"
# Windows 11 does not ship Windows Media Player, and FIFA 15 creates its ActiveX
# control for the intro video without checking whether it succeeded. Without this
# stub the game null-dereferences and dies ~26s into boot.
WMP_STUB_SCRIPT = ROOT / "tools" / "wmp_stub.js"
DEFAULT_GAME_ROOT = Path(r"F:\Games\FIFA 15")


def log(kind: str, **fields) -> None:
    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}),
          flush=True)


def wait_for_ready(process: subprocess.Popen, log_path: Path, timeout: int = 60) -> bool:
    """Wait until the server prints READY (or dies)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            log("server-exited", code=process.returncode)
            return False
        if log_path.exists():
            try:
                for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if '"kind": "ready"' in line or '"kind":"ready"' in line:
                        return True
            except OSError:
                pass
        time.sleep(0.4)
    return False


def main() -> int:
    config = ServerConfig()
    parser = argparse.ArgumentParser(description="Launch FIFA 15 with the local FUT server")
    parser.add_argument("--game-root", type=Path, default=DEFAULT_GAME_ROOT)
    parser.add_argument("--cert-mode", default="old-protossl",
                        choices=["old-protossl", "sha1", "sha256"])
    parser.add_argument("--no-game", action="store_true",
                        help="start the servers only, do not launch FIFA")
    parser.add_argument("--timeout", type=int, default=0,
                        help="seconds to run before shutting down (0 = until the game exits)")
    args = parser.parse_args()

    game_exe = args.game_root / "fifa15.exe"
    if not game_exe.exists():
        log("fatal", error=f"fifa15.exe not found at {game_exe}")
        return 1

    if not config.catalog.exists():
        log("fatal", error="player catalog missing", fix="python tools/extract_catalog.py")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    server_log = config.log_dir / f"trace-{stamp}.jsonl"
    hook_log = config.log_dir / f"hook-{stamp}.jsonl"
    config.log_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. servers -----------------------------------------------------
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)

    server = subprocess.Popen(
        [str(python), str(ROOT / "server" / "main.py"),
         "--quiet", "--log", str(server_log), "--cert-mode", args.cert_mode],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    log("server-starting", pid=server.pid, log=str(server_log))

    if not wait_for_ready(server, server_log):
        stderr = server.stderr.read().decode("utf-8", "replace") if server.stderr else ""
        log("fatal", error="server did not become ready", stderr=stderr[-2000:])
        server.kill()
        return 1
    log("server-ready", ports=config.all_ports())

    if args.no_game:
        log("servers-only", note="FIFA not launched; press Ctrl+C to stop")
        try:
            while server.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            server.terminate()
        return 0

    # ---- 2. game + hook -------------------------------------------------
    import frida

    hook_handle = hook_log.open("a", encoding="utf-8")
    hook_counts: dict[str, int] = {}

    def on_message(message, _data):
        if message.get("type") == "send":
            payload = dict(message.get("payload") or {})
            kind = payload.pop("kind", "?")
            payload.pop("time", None)
            hook_counts[kind] = hook_counts.get(kind, 0) + 1
            hook_handle.write(json.dumps({"kind": kind, **payload}) + "\n")
            hook_handle.flush()
            # Keep the console readable: show each kind only a few times.
            if kind in ("hook-ready", "hook-installed", "hook-missing", "ports-configured",
                        "dns-lookup-failed") or hook_counts[kind] <= 6:
                log(f"hook:{kind}", **payload)
        elif message.get("type") == "error":
            log("hook-error", detail=message.get("description"), stack=message.get("stack"))

    log("game-spawning", exe=str(game_exe))
    try:
        pid = frida.spawn([str(game_exe)], cwd=str(args.game_root))
    except Exception as exc:
        log("fatal", error=f"could not spawn the game: {exc!r}")
        server.terminate()
        return 1

    try:
        session = frida.attach(pid)

        # The WMP stub must be in place before the game reaches its intro video.
        stub = session.create_script(WMP_STUB_SCRIPT.read_text(encoding="utf-8"))
        stub.on("message", on_message)
        stub.load()

        script = session.create_script(HOOK_SCRIPT.read_text(encoding="utf-8"))
        script.on("message", on_message)
        script.load()

        # Tell the hook which ports we own. No remap is needed because the
        # standard redirector port was free.
        script.exports_sync.ports(config.all_ports(), {})

        frida.resume(pid)
        log("game-resumed", pid=pid)
    except Exception as exc:
        log("fatal", error=f"could not install the redirect hook: {exc!r}")
        try:
            frida.kill(pid)
        except Exception:
            pass
        server.terminate()
        return 1

    # ---- 3. run ---------------------------------------------------------
    log("running", note="FIFA 15 is live against the local server",
        serverLog=str(server_log), hookLog=str(hook_log))

    # The session detaches when the game process goes away; that is a far more
    # reliable liveness signal than polling the device's process list.
    game_alive = threading.Event()
    game_alive.set()
    session.on("detached", lambda *_: game_alive.clear())

    deadline = time.time() + args.timeout if args.timeout else None
    try:
        while True:
            time.sleep(2)
            if not game_alive.is_set():
                log("game-exited", pid=pid)
                break
            if deadline and time.time() > deadline:
                log("timeout-reached", seconds=args.timeout)
                try:
                    frida.kill(pid)
                except Exception:
                    pass
                break
    except KeyboardInterrupt:
        log("interrupted")
        try:
            frida.kill(pid)
        except Exception:
            pass

    log("hook-summary", counts=hook_counts)
    hook_handle.close()
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    log("done", serverLog=str(server_log), hookLog=str(hook_log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
