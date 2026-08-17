# PROGRESS

Last updated: 2026-08-17 (session start)

## Goal

Local FUT server for FIFA 15 PC. Big 3 verified in the real game: packs, squad builders, transfer market. Plus club persistence, coins, match rewards, consumables.

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 0 | Recon: client boot trace (DNS/ports/TLS/Blaze/HTTP) | **done** |
| — | Game boots and reaches the menus at all | **done — was crashing; fixed and seen working** |
| 1 | Client connects to localhost, main menu "online" | **blocked** — this copy never attempts a connection (see below) |
| 2 | FUT mode entered, club loads | blocked on 1 |
| 3 | Squad building works + persists | server side done; blocked on 1 for in-game proof |
| 4 | Packs | server side done; blocked on 1 for in-game proof |
| 5 | Transfer market | server side done; blocked on 1 for in-game proof |
| 6 | Polish + regression pass | not started |

## What works

- **Archive tooling.** BIG4/ViV4 reader. All inherited FIFA 14 format facts re-verified on FIFA 15: djb2 path hash reproduces 2399/2399 `.bh` hashes, both indexes agree on offsets, chunkzip decodes.
- **Client DB parser.** Reads `cards_ng_db.db` through its `-meta.xml` descriptor, including Huffman-compressed name tables. Header CRC-32/MPEG-2 verified against the retail DB.
- **Player catalog.** 16,185 FIFA 15 players extracted from the game's own DB, 21,316 names, zero missing team/nation/league links. Ratings check out (Messi 93, Ronaldo 92, Neuer 90).
- **TLS certificates.** old-protossl / sha1 / sha256. The old-protossl chain is correctly malformed: inner `md5WithRSAEncryption`, outer patched to `rsaEncryption`.
- **Blaze protocol layer, verified over real sockets.** `tools/selftest_blaze.py` performs the full client sequence and passes: TLS to the redirector, `getServerInstance` returning the right loopback address and port, `preAuth` (19 components, correct service name `fifa-2015-pc`), all five OSDK config groups, login with a persona, the three UserSessions notifications that flip the client online, `postAuth`, and `ping`. The redirector accepts **TLS 1.0 / 1.1 / 1.2 / 1.3**, covering whatever ProtoSSL negotiates.
- **Identity store.** SQLite club, items, squads, listings, coin ledger, idempotent grants. Full self-test passes (`tools/selftest_identity.py`).
- **Packs (server-side).** 8 retail-shaped packs. Correct tiering, guaranteed rares, a keeper in every pack, correct FUT `ItemData` wire contract.
- **`GIVE_100M_TEST_COINS.bat`** — double-click, grants 100M coins, idempotent; `--repeat` grants another lot.

**Nothing is verified inside the real game yet, and cannot be until the boot crash below is resolved.** Everything above is proven against real sockets and real HTTP, but that is not the same as seeing it on screen, and I am not claiming otherwise.

### How to re-run the proof yourself

```
.venv\Scripts\python.exe server\main.py --quiet
.venv\Scripts\python.exe tools\selftest_identity.py
.venv\Scripts\python.exe tools\selftest_blaze.py
.venv\Scripts\python.exe tools\selftest_http.py
```

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

## SOLVED: the boot crash (verified in the real game)

**FIFA 15 now boots, reaches the menus, and plays matches.** It previously died ~26 seconds into every launch.

Root cause, established by tracing rather than guessing:

```
CoCreateInstance({6BF52A52-394A-11D3-B153-00C04F79FAA6})   <- Windows Media Player control
  -> 0x80040154 REGDB_E_CLASSNOTREG
fifa15.exe+0x3f41916:  mov rax, qword ptr [rcx]            <- rcx = 0, access violation
```

FIFA 15 creates the Windows Media Player ActiveX control to play its intro video. Windows 11 no longer ships Windows Media Player, so the class is not registered. The game never checks the HRESULT and dereferences the null interface pointer.

The fix is `tools/wmp_stub.js`: when that class fails to create, hand the game an inert COM object instead of NULL. `QueryInterface` returns itself, `AddRef`/`Release` are safe, every other vtable slot returns `E_NOTIMPL`. The intro video silently does nothing and boot continues. **No elevation, nothing registered system-wide, no game file modified.**

Verified in-game by screenshot: intro videos play, the Messi title screen appears, the main menu loads, and a full Kick Off match at Anfield (Liverpool vs Man City) renders with correct squads and player ratings. Offline modes are intact, as required.

The supported alternative, if you would rather not run the stub, is to install **Windows Media Player Legacy** (Settings → System → Optional features). That needs elevation, which is why the stub exists.

## BLOCKER: this copy of FIFA 15 never attempts an online connection

Milestones 2-5 (FUT club, squads, packs, market) cannot be verified in-game on this install, and the reason is not the server.

Evidence from a full traced boot with hooks on `socket`, `connect`, `WSAConnect`, `sendto`, `WSASendTo`, `send`, `getaddrinfo`, `GetAddrInfoW`, `GetAddrInfoExW`, `gethostbyname`, `DnsQuery_*`, `InternetConnect*` and `WinHttpConnect`:

- **Zero TCP connections. Zero DNS queries.** The only network traffic is UPnP/SSDP multicast to `239.255.255.250:1900`.
- The game shows "Unable to connect to the EA servers at this time" without ever touching the network.

The cause is the crack. `ItsAMe_Origin.dll` is a local Origin emulator that returns a hardcoded licence:

```xml
<License><CipherKey>000…000</CipherKey><MachineHash>000…000</MachineHash>
  <ContentId>1024871</ContentId><UserId>0000000000</UserId>
  <GameToken>000…000</GameToken>…</License>
```

`UserId` is zero and `GameToken` is all zeros. That is enough to satisfy the offline DRM check so the game launches, but FIFA's online subsystem needs a real Nucleus identity and auth token before it will even begin the Blaze handshake. With zeros it concludes it has no online entitlement and short-circuits.

This is why the FIFA 14 project worked and this does not: its build still attempted the online handshake, so a network redirect had something to catch. Here there is nothing to redirect.

### What would unblock it

1. **A FIFA 15 copy whose online path is intact** — the most reliable route. Everything else in this project is finished and waiting.
2. Hooking the entitlement/Nucleus check in-process to report a valid session, the same way `wmp_stub.js` handles the media control. Tractable with the tooling now in the repo, but it means reverse-engineering the crack's licence path, which is a substantial piece of work on its own.

## Earlier investigation of the boot crash (now solved)

**This is a pre-existing fault in the game install. It is not caused by this project** — it happens identically with the server off, the hook off, and the game launched by hand.

Evidence (from `tools/run_diagnostic.py`, which spawns the game under a frida crash handler):

```
access-violation, read at 0x0
  fifa15.exe+0x3f41916   <- faults here (module base 0x140000000)
  fifa15.exe+0x3f41b6d
  fifa15.exe+0x3f41b13
  fifa15.exe+0x2f43ea7   <- worker thread entry
  MSVCR110.dll+0x23fdf   <- _beginthreadex trampoline
  KERNEL32!BaseThreadInitThunk
```

- Reproducible: dies at **~26-28 seconds**, every launch, same fault offset. Confirmed across 6+ runs.
- Windows *does* log it (unlike the FIFA 14 3DM crack): `Application Error`, `fifa15.exe 1.8.0.0`, exception `0xc0000005`, fault offset `0x3f41916`.
- It is a **null-pointer read on a data-loading worker thread**, not on the main thread.
- **Zero network activity before the crash.** Hooks on `getaddrinfo`, `GetAddrInfoW`, `GetAddrInfoExW`, `gethostbyname`, `WSAAsyncGetHostByName`, `WSAConnectByNameA/W`, `connect` and `WSAConnect` recorded *nothing*. The game never reaches online code, so no server-side change can affect it.
- The last ~15 files opened are all audio (`commentary_contextdata.bin`, `speechgas.dat`, `BE_SFX.sbs`, `EATrax_*.sbr`, `chants_*.sbr`) followed by `data/common.cbac` and `data/ant.cbac`.

Ruled out:

| Theory | Result |
|---|---|
| Missing `fifasetup.ini` (game had never completed a first run) | Created a valid one. Still crashes. Worth keeping regardless. |
| `install.ini` `LIMITED_MODE=1` streaming-install flag | Set to 0, retested, still crashes. **Reverted, verified byte-for-byte pristine.** |
| Missing Visual C++ runtime | VC++ 2012 x86+x64 installed; `msvcr110.dll` present in System32 and SysWOW64. |
| Corrupt shader/data cache | `Documents\FIFA 15\cache0` is empty (0 files); not the cause. |
| Incomplete install | 9.5 GB / 197 files; `common.cbac` and `ant.cbac` both present and large. |
| Windows 11 incompatibility | Applied a `WIN7RTM` per-app compatibility shim, retested, still crashes at 28s. **Shim removed, registry back to empty.** |

Remaining theories, in order of my confidence:

1. **Audio subsystem incompatibility.** The crash follows a long run of audio-asset loads and nothing else. Old EA titles commonly fault on modern audio endpoints running at 24-bit/192 kHz. The fix is a system-wide sound setting, so I left it for you.
2. **Bad repack / damaged audio content.** This is a CPY release; a truncated audio archive produces exactly this null-resource fault on a loader thread.

### What I need from you (only these need a human)

1. Open Windows Sound settings → your **Headphones** device → Properties → Advanced → set Default Format to **16 bit, 44100 Hz (CD Quality)** → Apply. Then double-click `RUN_FIFA15_LOCAL_FUT.bat`.
2. If it still dies at ~26 seconds, the install's audio content is most likely damaged, and reinstalling FIFA 15 from a different source would settle it. Everything else in the chain is verified good.

Tell me which you did and what happened, and I will carry on from there. If the game boots at all, `RUN_FIFA15_LOCAL_FUT.bat` will capture its full endpoint contract into `logs/` automatically.

## Stuck / open questions

- Not yet observed: whether FIFA 15's ProtoSSL accepts the old-protossl certificate, or wants the sha1 chain. All three modes are built so this can be settled by trying, not guessing.
- FUT HTTP endpoint paths for FIFA 15 (`/ut/game/fifa15/...`) are assumed to mirror FIFA 14's. The server runs in trace mode and logs every unmatched request so the real contract gets recorded rather than guessed.

## Tried

- Killing the stale elevated FIFA 14 server to reclaim ports 42128/42129/8099/44125/8080 — blocked (can't touch an elevated process from an unelevated shell). Worked around by giving FIFA 15 its own port range: 42127 / 42131 / 8110 / 8111 / 44130.
- `product.ini` inside `data_ini.big` is EASF-compressed, not chunkzip. Not decoded; not needed so far.

## Next

Blocked on the boot crash for anything in-game. While that waits on you, the work that still moves:

1. Harden the server against the real client contract (done: `tools/selftest_http.py`).
2. Keep the trace-mode logging so the first successful boot captures FIFA 15's actual endpoint set rather than my FIFA 14-derived guesses.
3. Milestone 1 — client reaches the main menu "online" — the moment the game boots.

## Safety / machine state

- **No game file is currently modified.** `python tools\game_backup.py verify` reports `install.ini` pristine; it is the only file ever touched, and it was restored.
- The backup tool refuses to overwrite an existing pristine backup and verifies SHA-256 on both copy and restore. The restore path was tested by deliberately corrupting the file and confirming a byte-for-byte recovery.
- One file was *created* that the game itself owns and had never written: `Documents\FIFA 15\fifasetup.ini` (graphics settings, windowed 1600x900). Deleting it returns you to the previous state.
- Nothing was installed system-wide. `frida` and `cryptography` live only in the project `.venv`.
- No elevation was used or required at any point.
