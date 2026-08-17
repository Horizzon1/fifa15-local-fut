"""Spawn FIFA 15 under the crash/network diagnostic and record what it does."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

SCRIPT = ROOT / "tools" / "confirm_wmp.js"
GAME_ROOT = Path(r"F:\Games\FIFA 15")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "diagnostic.jsonl")
    args = parser.parse_args()

    import frida

    args.output.parent.mkdir(parents=True, exist_ok=True)
    handle = args.output.open("w", encoding="utf-8")
    counts: dict[str, int] = {}
    interesting: list[dict] = []

    def on_message(message, _data):
        if message.get("type") != "send":
            print(json.dumps({"kind": "script-error", "detail": message.get("description")}), flush=True)
            return
        payload = message.get("payload") or {}
        kind = payload.get("kind", "?")
        counts[kind] = counts.get(kind, 0) + 1
        handle.write(json.dumps(payload) + "\n")
        handle.flush()
        if kind in ("FAILED", "FAULT", "ready"):
            print(json.dumps(payload), flush=True)
            if kind in ("FAILED", "FAULT"):
                interesting.append(payload)

    pid = frida.spawn([str(GAME_ROOT / "fifa15.exe")], cwd=str(GAME_ROOT))
    session = frida.attach(pid)
    script = session.create_script(SCRIPT.read_text(encoding="utf-8"))
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(json.dumps({"kind": "resumed", "pid": pid}), flush=True)

    detached = []
    session.on("detached", lambda *a: detached.append(a))

    deadline = time.time() + args.seconds
    while time.time() < deadline and not detached:
        time.sleep(0.5)

    if detached:
        print(json.dumps({"kind": "process-gone", "after_s": round(args.seconds - (deadline - time.time()), 1)}),
              flush=True)
    else:
        try:
            frida.kill(pid)
        except Exception:
            pass

    # The last file the game touched before dying is often the best clue.
    files = []
    for line in args.output.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "file-open":
            files.append(row["path"])

    print(json.dumps({"kind": "summary", "counts": counts}), flush=True)
    print(json.dumps({"kind": "last-files", "paths": files[-15:]}), flush=True)
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

