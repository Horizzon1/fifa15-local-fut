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

- **Archive tooling.** BIG4/ViV4 reader. All inherited FIFA 14 format facts re-verified on FIFA 15: djb2 path hash reproduces 2399/2399 `.bh` hashes, both indexes agree on offsets, chunkzip decodes.
- **Client DB parser.** Reads `cards_ng_db.db` through its `-meta.xml` descriptor, including Huffman-compressed name tables. Header CRC-32/MPEG-2 verified against the retail DB.
- **Player catalog.** 16,185 FIFA 15 players extracted from the game's own DB, 21,316 names, zero missing team/nation/league links. Ratings check out (Messi 93, Ronaldo 92, Neuer 90).
- **TLS certificates.** old-protossl / sha1 / sha256. The old-protossl chain is correctly malformed: inner `md5WithRSAEncryption`, outer patched to `rsaEncryption`.
- **Blaze protocol layer.** TDF encode/decode, FIRE framing, redirector + bootstrap payloads.
- **Identity store.** SQLite club, items, squads, listings, coin ledger, idempotent grants. Full self-test passes (`tools/selftest_identity.py`).
- **Packs (server-side).** 8 retail-shaped packs. Correct tiering, guaranteed rares, a keeper in every pack, correct FUT `ItemData` wire contract.
- **`GIVE_100M_TEST_COINS.bat`** — double-click, grants 100M coins, idempotent; `--repeat` grants another lot.

Nothing is verified in the real game yet. That is the next milestone.

## Environment facts

- FIFA 15 install: `F:\Games\FIFA 15` (fifa15.exe present, 87,268,816 bytes).
- Install is CPY-cracked (CPY.ini, ItsAMe_Origin.dll). No real Origin needed; note the FIFA 14 project's 3DM crack behaved similarly (no Windows Error Reports on crash).
- `cards0.big`/`cards0.bh` exist at game root (like FIFA 14) — likely home of the FUT cards DB.
- FIFA 14 reference project: `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main` (read-only). Branches: `stock` (working release), `legends` (archive/DB tooling).
- Python 3.11.9 on PATH; FIFA 14 project has its own .venv.

## User requests

- [x] `GIVE_100M_TEST_COINS.bat` — done and tested. Grants 100M, idempotent, `--repeat` for another lot. Bootstraps the venv and catalog on first run.

## Recon findings (from the real client, not assumed)

| Question | Answer |
|---|---|
| Blaze redirector host | `spring14.gosredirector.ea.com` (+ .online/.stest/.scert) |
| Blaze service name | `fifa-2015-pc` (template `fifa-2015-%s`) |
| Redirector port | 42127 — **free on this machine**, so no port remap needed (FIFA 14 had to use 42129) |
| Does FIFA 15 support cl.ini? | Yes; exe exposes `ONLINE/BLAZE_SERVICE_NAME` and `..._OVERRIDE` |
| How does FUT find its server? | Blaze client config keys, not DNS: `FUT_URI`, `FUT_RS4_BASE_URL`, `FUTDYNAMICMESSAGES_URL_BASE` all confirmed present in fifa15.exe |
| OSDK config groups | All five present: CORE, CLIENT, NUCLEUS, WEBOFFER, XMS_ABUSE_REPORTING |
| FUT cards DB | `data/db/cards_ng_db.db` — data_patch.big rec14 (runtime), cards0.big rec1220 (base) |
| Descriptor | `data/db/cards_ng_db-meta.xml` — cards0.big rec1221 |

## Elevation plan (decided)

No elevation anywhere. The hosts file is **not** writable unelevated, so redirection uses frida hooks on `getaddrinfo`/`GetAddrInfoW` and `connect`/`WSAConnect` inside the game process — same approach FIFA 14 landed on, but without its port remap since 42127 is free. Nothing is installed system-wide; `frida` and `cryptography` live in the project `.venv`.

## Stuck / open questions

- Not yet observed: whether FIFA 15's ProtoSSL accepts the old-protossl certificate, or wants the sha1 chain. All three modes are built so this can be settled by trying, not guessing.
- FUT HTTP endpoint paths for FIFA 15 (`/ut/game/fifa15/...`) are assumed to mirror FIFA 14's. The server runs in trace mode and logs every unmatched request so the real contract gets recorded rather than guessed.

## Tried

- Killing the stale elevated FIFA 14 server to reclaim ports 42128/42129/8099/44125/8080 — blocked (can't touch an elevated process from an unelevated shell). Worked around by giving FIFA 15 its own port range: 42127 / 42131 / 8110 / 8111 / 44130.
- `product.ini` inside `data_ini.big` is EASF-compressed, not chunkzip. Not decoded; not needed so far.

## Next

1. Wire the FUT HTTP server onto the identity store.
2. Build the launcher: start servers, launch the game, attach the frida redirect hook.
3. Milestone 1 — see the client reach the main menu "online" in the real game.
