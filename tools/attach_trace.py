"""Attach a frida script to the already-running FIFA 15 process.

Used to add instrumentation mid-session without restarting the game and losing
the menu state it took a while to reach.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=Path)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--process", default="fifa15.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "attach.jsonl")
    parser.add_argument("--show", default="", help="comma-separated kinds to echo (default all)")
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
        if (not show or kind in show) and counts[kind] <= 30:
            print(json.dumps(payload), flush=True)

    session = frida.attach(args.process)
    script = session.create_script(args.script.read_text(encoding="utf-8"))
    script.on("message", on_message)
    script.load()
    print(json.dumps({"kind": "attached", "process": args.process}), flush=True)

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
