# PROGRESS

Last updated: 2026-08-17 (session start)

## Goal

Local FUT server for FIFA 15 PC. Big 3 verified in the real game: packs, squad builders, transfer market. Plus club persistence, coins, match rewards, consumables.

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 0 | Recon: client boot trace (DNS/ports/TLS/Blaze/HTTP) | in progress |
| 1 | Client connects to localhost, main menu "online" | not started |
| 2 | FUT mode entered, club loads | not started |
| 3 | Squad building works + persists | not started |
| 4 | Packs | not started |
| 5 | Transfer market | not started |
| 6 | Polish + regression pass | not started |

## What works

- Nothing yet. Project just started.

## Environment facts

- FIFA 15 install: `F:\Games\FIFA 15` (fifa15.exe present, 87,268,816 bytes).
- Install is CPY-cracked (CPY.ini, ItsAMe_Origin.dll). No real Origin needed; note the FIFA 14 project's 3DM crack behaved similarly (no Windows Error Reports on crash).
- `cards0.big`/`cards0.bh` exist at game root (like FIFA 14) — likely home of the FUT cards DB.
- FIFA 14 reference project: `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main` (read-only). Branches: `stock` (working release), `legends` (archive/DB tooling).
- Python 3.11.9 on PATH; FIFA 14 project has its own .venv.

## User requests

- [ ] `GIVE_100M_TEST_COINS.bat` — one-click 100M coin grant for testing (modeled on the 14 project's cmd → ps1 → prepare_state --test-coins pipeline). Blocked on the DB/state layer existing.

## Stuck / open questions

- Does FIFA 15 PC respect hosts-file or cl.ini redirect, or does it need an in-process hook like FIFA 14 (frida)? To determine in recon.
- Exact FIFA 15 Blaze hostnames/ports — to extract from fifa15.exe strings + live trace.

## Tried

- (nothing yet)

## Next

1. Repo + structure (this commit).
2. Study FIFA 14 probe.py / local_identity.py / trace launcher.
3. Strings-scan fifa15.exe for hostnames, ports, cl.ini support.
4. Find FIFA 15 FUT cards DB path in archives; extract player catalog.
