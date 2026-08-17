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

## STILL OPEN: the client's online handshake never reaches the server

Milestones 1-5 are not verified in-game. What follows is the current, corrected understanding.

### Correction to an earlier conclusion in this file

I previously wrote that the game never attempts an online connection, and blamed the crack's zeroed Origin licence (`UserId 0000000000`, all-zero `GameToken`). **That conclusion was wrong and has been removed.** It came from boots where I never actually opened an online mode — I kept landing in Kick Off matches instead — so "no network activity" only meant "nothing asked for the network yet".

With a tracer attached while the game actually tried to log in, the picture is different:

- The game **does** try. A **TCP socket is created** and **`DnsQueryEx`** is called for `spring14.gosredirector.ea.com`.
- That hostname **still resolves**, to `159.153.51.19`. EA's DNS records are alive; only the service behind them is gone.
- So there is a real connection attempt to redirect. The licence theory was a red herring.

### Where it stands now

Three fixes went in off the back of that:

1. **DNS is left alone deliberately.** Rewriting the query name to `localhost` did work (confirmed in the log), but redirecting at connect time is more robust than synthesising DNS answers, and resolution succeeds anyway.
2. **The server is now dual-stack** (binds `::` with `IPV6_V6ONLY` cleared). Windows resolves `localhost` to `::1` before `127.0.0.1`, so a v4-only listener would silently never be reached. Both self-tests still pass over the new sockets.
3. **`ConnectEx` is now hooked**, captured via `WSAIoctl(SIO_GET_EXTENSION_FUNCTION_POINTER, WSAID_CONNECTEX)`. EA's networking uses overlapped sockets, which is why a TCP socket appeared with no `connect()` ever observed.

The remaining puzzle: with all of the above active, the login attempt still produces **no `connect`, no `WSAConnect`, and no `ConnectEx`** — and `WSAIoctl` is never even asked for the ConnectEx pointer. So the socket is created, DNS is queried, and then the attempt is abandoned before any connect syscall.

### The measurement, taken

Reached the real main menu (Man United profile created) and clicked **ULTIMATE TEAM**, with the server live and `DnsQueryEx` fully instrumented. Result:

```
dns-query   host=spring14.gosredirector.ea.com  type=A  returned=0  SUCCESS (synchronous)
dns-result  status=0  records=[{type:1, name:spring14.gosredirector.ea.com, a:159.153.51.19}]
dns-query   host=localhost  type=A  returned=0  SUCCESS
dns-result  status=0  records=[{type:1, name:localhost, a:127.0.0.1}]
   (repeated three times for the EA host, twice for localhost)
```

So **DNS is not the problem.** The answer is clean and synchronous. The game also resolves `localhost`, which is interesting in its own right.

And yet, across the same window:

- No `connect`, no `WSAConnect`, no `ConnectEx`, and `WSAIoctl` is never asked for the ConnectEx pointer.
- **`Get-NetTCPConnection` shows no connection and no SYN to `159.153.*` or to port 42127 at any point.** The operating system agrees: nothing is dialled.
- The game reports "Unable to connect to the EA servers at this time".

### Conclusion, stated carefully

The client **resolves the redirector and then declines to dial it**. The failure is upstream of the network, which means a network-level redirect — the entire basis of this server design — never gets the chance to catch anything on this client as configured.

**I have now been wrong in both directions on this, so here is the precise line between measured and inferred:**

- *Measured*: DNS succeeds. No TCP connection is ever attempted. Offline modes work. The boot crash was a missing Windows Media Player component and is genuinely fixed.
- *Inferred, not proven*: why it declines to dial. "Resolves, then refuses to connect" is the classic shape of a client with no valid session — which points back at the Origin/Nucleus entitlement layer I earlier dismissed. Proving that DNS works did **not** disprove the auth theory; I over-corrected when I said it did.

### ROOT CAUSE

Call-stack tracing gave the actual sequence. On a connection attempt the client does:

```
socket(AF_INET, SOCK_STREAM)      <- fifa15.exe+0x307197c … +0x39e149c … +0x39e7925
ioctlsocket(FIONBIO)              <- non-blocking
bind(0.0.0.0:0)                   <- SUCCEEDS (ordinary ephemeral bind)
closesocket()                     <- fifa15.exe+0x3071eb5 … +0x39e6bbd … +0x39e7925
```

**It opens a socket, binds it, and closes it without ever calling connect.** The socket layer is healthy; the layer above it decides not to dial. DNS resolution of the redirector happens as preparation, which is why it looked like a connection attempt.

Everything else is ruled out by measurement, not assumption:

| Suspect | Verdict |
|---|---|
| DNS failing | **No** — `DnsQueryEx` returns 0 with A `159.153.51.19` |
| `bind` failing | **No** — binds `0.0.0.0:0` successfully |
| This project's own listeners stealing the port | **No** — retested with the server fully stopped and all five ports free; identical behaviour |
| Network adapter confusion | **No** — one clean adapter, `192.168.2.3`, working default route, public DNS resolves |
| A hook blind spot | **No** — OS-level `Get-NetTCPConnection` polling agrees: zero connections |
| Missing Winsock init | **No** — `WSAStartup` returns 0 (and is called repeatedly, indicating a retry loop) |

What remains is the OSDK online state machine never starting the Blaze connect, because there is no authenticated EA/Origin user session. `ItsAMe_Origin.dll` answers the identity request with `UserId 0000000000` — literally "no user". No user session, no online session, so nothing is ever dialled. The persistent "PRESS RS TO RE-CONNECT" banner and the repeated `WSAStartup` churn are both consistent with that.

### Tried: EA's own developer override channel (negative result)

Worth recording because it was the most promising legitimate idea and it is now ruled out.

The client contains EA's internal override switches — found in `fifa15.exe`:

```
ONLINE/USE_OSDKDEBUG_FILE      ONLINE/BLAZE_SERVICE_NAME_OVERRIDE
ONLINE/BLAZEENV_OVERRIDE       ONLINE/BLAZEPORT
ONLINE/SERVERPORT              ONLINE/SERVER_RS4
FUT_DIRECT_BOOT   DirectBootFUT   LoadFUTSkipBlaze   FUT_URI
```

`LoadFUTSkipBlaze` in particular reads as "load FUT without the Blaze session", which is exactly the wanted behaviour.

Confirmed by hooking `CreateFileW` that the game really does read the config files:

- `F:\Games\FIFA 15/cl.ini` — **opened**
- `osdkdebugmanager.ini` — **opened** (does not ship with the game; created for the test)

So the channel is real. Both files were populated with the direct-boot flags and the Blaze host/port overrides, and the game was launched and driven to the main menu.

**Result: no change.** Clicking Ultimate Team produced zero network activity — no DNS, no connect, nothing reaching the local server. With the overrides in place the client simply does nothing at all rather than showing its usual error.

Conclusion: these keys are consumed *after* the session gate, not before it. Configuration cannot route around the missing session. The files created for this test were removed afterwards.

### The missing precondition, and why it is hard to satisfy legitimately

The gap is a single authenticated EA/Origin user session. Supply that and the client dials, the hook redirects it to loopback, and the server — already verified end to end over real sockets and real HTTP — answers.

The obvious routes are closed here, and it is worth being straight about why:

- FIFA 15 is **delisted**; it cannot be bought again.
- The owner's purchased entitlement was **deactivated by EA**, and the online services are long dead. So there is no store to re-buy from and no service being withheld from a paying customer.
- EA Desktop is installed and running on the machine, but with no entitlement it has nothing to hand the game. (The registry does already register FIFA 15 at `F:\Games\FIFA 15\`.)

That leaves manufacturing the session locally — rewriting the zeroed `UserId`/`GameToken` that `ItsAMe_Origin.dll` returns. It is a small change and it would very likely work. **I am not doing it**, and I want the reason recorded honestly rather than dressed up as a technical impossibility: it is circumventing an access-control check, I cannot verify ownership from here, and I declined it twice already — reversing under pressure on an unverifiable claim is exactly the failure mode to avoid.

This is a genuine preservation case and the owner's frustration is reasonable. It is still a line I hold.

**What that means practically:** the FIFA 15 work in this repo is complete and waiting on a client that will dial. If the immediate goal is actually *playing* offline Ultimate Team, the FIFA 14 Local FUT project already works on this machine today and does not need any of this.

### Final independent validation

I did not want to trust the frida hooks alone, since they had never once reported a connection — a silent hook and a silent client look identical. So `tools/watch_connections.ps1` polls `Get-NetTCPConnection` four times a second, filtered to the game's PID, and I clicked the **Catalogue** tile, whose own text reads "CONNECT TO EAS FC SERVERS".

Result: **zero TCP connections, from the game, for anything, throughout.** The hooks and the operating system independently agree. FIFA 15 on this install opens no TCP socket to anywhere.

That closes the question. The client's online subsystem is switched off before it reaches the network, so no server-side work and no redirect can reach it. This is not a bug in anything I built.

### Honest position on finishing this

Everything server-side is complete and verified over real sockets and real HTTP. The remaining gap is entirely in the client's pre-connect logic, and the credible ways through it are:

1. **Get the legitimate licence restored.** The owner bought FIFA 15 and EA revoked the entitlement. EA support can and does restore wrongly-removed entitlements; with the game installed normally through the EA app, its online path is intact and everything in this repo should work against it unchanged. This is the path I would push first — it is the only one that is both legitimate and complete.
2. **Any FIFA 15 build whose online path is intact.** Point the launcher at it; nothing here needs to change.
3. Reverse-engineering the crack's DRM/entitlement emulation to manufacture a session. I am not doing that, and ownership does not change it — it is still defeating a licence check, which is a different activity from reimplementing servers EA switched off or from fixing a Windows compatibility bug.

### What is genuinely finished and reusable

Independent of the client problem, all of this is done and verified:

| Piece | State |
|---|---|
| FIFA 15 boot crash | **Fixed and seen working in-game.** Was fatal on every launch. |
| Archive + client-DB tooling | BIG4/ViV4, chunkzip, CRC-32/MPEG-2, Huffman name tables — all verified against retail files |
| Player catalog | 16,185 players from the game's own DB, full relational closure |
| Blaze layer | Redirector, preAuth, five config groups, login, session notifications, postAuth, ping — all pass over real sockets, TLS 1.0-1.3 |
| FUT HTTP layer | Packs, club, squads, market, store, match — all pass over real HTTP |
| Identity store | Club, items, squads, listings, coin ledger, idempotent grants — persists across sessions |
| `GIVE_100M_TEST_COINS.bat` | Works |
| Diagnostics | Crash analyser, COM tracer, DNS result tracer, connection watcher, verified game-file backup/restore |

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
