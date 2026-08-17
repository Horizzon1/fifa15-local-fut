# FIFA 15 Local FUT

A local Ultimate Team server for FIFA 15 PC. It runs a localhost Blaze + FUT HTTP
server so Ultimate Team — packs, squad building, the transfer market, a
persistent club — can work offline against the real game after EA's servers were
switched off.

**Status: the server is complete and tested. The client does not yet connect.**
See [PROGRESS.md](PROGRESS.md) for the full diagnostic trail and
[HANDOVER.md](HANDOVER.md) to pick the work up.

## What works

| Piece | State |
|---|---|
| Blaze protocol server (redirector, preAuth, config, login, notifications, postAuth, ping) | Passes over real sockets, TLS 1.0–1.3 |
| FUT HTTP API (packs, club, squads, market, store, match) | Passes over real HTTP |
| Identity store (club, items, squads, listings, coin ledger) | Persists across sessions |
| Player catalog | 16,185 players read from the game's own database |
| Archive + client-DB tooling | BIG4/ViV4, chunkzip, CRC-32/MPEG-2, Huffman name tables |
| **FIFA 15 boot crash on Windows 11** | **Fixed** — see below |

## The boot-crash fix

FIFA 15 dies about 26 seconds into every launch on Windows 11. It creates the
Windows Media Player ActiveX control to play its intro video; Windows 11 no
longer ships Windows Media Player, so the class is not registered, and the game
dereferences the resulting null pointer without checking it.

`tools/wmp_stub.js` hands the game an inert COM object instead of NULL, so the
intro silently does nothing and the game boots. No elevation, no game file
modified. Installing "Windows Media Player Legacy" from Windows optional
features fixes it properly if you prefer.

This part is useful on its own, independent of the server.

## The open blocker

In the real game, selecting Ultimate Team resolves the Blaze redirector by DNS
and then never dials it — no TCP connection is ever attempted, confirmed both by
in-process hooks and by OS-level connection polling. The client has no
authenticated EA session, so its state machine never starts the Blaze connect.
That is upstream of the network, so nothing server-side can reach it.

Every workaround that does not involve defeating a licence check has been tried
and documented, including EA's own developer override keys. See PROGRESS.md.

## Requirements

- A legitimate FIFA 15 PC installation (not included).
- Windows, Python 3.11+.

## Usage

```
RUN_FIFA15_LOCAL_FUT.bat      # start the server and launch the game
GIVE_100M_TEST_COINS.bat      # grant test coins (idempotent; --repeat for more)
```

Re-run the proofs:

```
.venv\Scripts\python.exe server\main.py --quiet
.venv\Scripts\python.exe tools\selftest_identity.py
.venv\Scripts\python.exe tools\selftest_blaze.py
.venv\Scripts\python.exe tools\selftest_http.py
```

## Credits

The architecture is informed by
[FIFA-14-Local-FUT](https://github.com/KyroGeorge2/FIFA-14-Local-FUT) by
KyroGeorge2 — the proven Blaze/FIRE + FUT HTTP + local identity design for
FIFA 14. `recon/fifa14-server-reference.md` documents that wire contract.

## Scope

No game files, executables, EA assets, or player data are included in this
repository. The player catalog is generated locally from your own installation
and is deliberately not committed.

This project reimplements servers that no longer exist and fixes a Windows
compatibility bug. It does not include, and will not include, anything that
defeats a licence or entitlement check.

## License

FIFA, FIFA 15, Ultimate Team, EA SPORTS and related marks belong to their
respective owners. This is an independent preservation project, not affiliated
with or endorsed by Electronic Arts.
