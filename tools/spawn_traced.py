"""Spawn FIFA 15 with several frida scripts loaded before it runs.

Used to watch the whole boot, including the online handshake, with the WMP stub
already in place so the game survives long enough to get there.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")
GAME_ROOT = Path(r"F:\Games\FIFA 15")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scripts", nargs="+", type=Path)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "spawn.jsonl")
    parser.add_argument("--show", default="", help="comma-separated kinds to echo")
    args = parser.parse_args()

    import frida

    show = {k.strip() for k in args.show.split(",") if k.strip()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    handle = args.output.open("w", encoding="utf-8")
    counts: dict[str, int] = {}

    def on_message(message, _data):
        if message.get("type") != "send":
            print(json.dumps({"kind": "script-error", "detail": message.get("description")}), flush=True)
            return
        payload = message.get("payload") or {}
        kind = payload.get("kind", "?")
        counts[kind] = counts.get(kind, 0) + 1
        handle.write(json.dumps(payload) + "\n")
        handle.flush()
        if (not show or kind in show) and counts[kind] <= 25:
            print(json.dumps(payload), flush=True)

    pid = frida.spawn([str(GAME_ROOT / "fifa15.exe")], cwd=str(GAME_ROOT))
    session = frida.attach(pid)
    for path in args.scripts:
        script = session.create_script(path.read_text(encoding="utf-8"))
        script.on("message", on_message)
        script.load()
    frida.resume(pid)
    print(json.dumps({"kind": "spawned", "pid": pid}), flush=True)

    detached = []
    session.on("detached", lambda *a: detached.append(a))
    deadline = time.time() + args.seconds
    while time.time() < deadline and not detached:
        time.sleep(0.5)

    print(json.dumps({"kind": "summary", "counts": counts}), flush=True)
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
