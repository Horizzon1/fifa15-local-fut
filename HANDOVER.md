# FIFA 15 Local FUT — Handover

Last updated: 2026-08-17

This is the pick-up-and-continue document. It says what the project is, what is
finished, what is blocked, where every file lives, and how to resume. For the
full diagnostic trail see [PROGRESS.md](PROGRESS.md).

---

## One-paragraph summary

A local Ultimate Team server for **FIFA 15 PC**. It runs a localhost Blaze +
FUT HTTP server so FUT (packs, squad building, transfer market, persistent club)
works fully offline against the real game. The **server side is complete and
verified over real sockets and real HTTP.** The game now **boots and plays**
(a boot crash was found and fixed). The **one remaining blocker** is that this
particular game copy never starts an online connection, because the cracked
Origin DLL reports no logged-in user. That last step is **not resolved**, and
the reason it is not resolved is a deliberate line, recorded below.

---

## Status at a glance

| Area | State |
|---|---|
| Archive / DB tooling | Done, verified |
| Player catalog (16,185 players) | Done, verified |
| Blaze protocol server | Done, verified over real sockets |
| FUT HTTP server (packs, squads, market) | Done, verified over real HTTP |
| Identity / club / coin store | Done, self-test passes |
| Game boots to menus and plays | Done, fixed and seen on screen |
| **Client actually connects online** | **Blocked** (see "The open blocker") |
| In-game proof of FUT | Blocked, waiting on the line above |

**Important honesty note:** everything marked "verified" is verified against
real sockets and real HTTP, not yet seen inside the running game, because the
client never connects. Do not upgrade those to "works in-game" without in-game
proof.

---

## Environment facts

- Game install: `F:\Games\FIFA 15` (`fifa15.exe`, 87,268,816 bytes).
- Install is **CPY-cracked** (`CPY.ini`, `ItsAMe_Origin.dll`). No real Origin.
- FUT cards DB: `data/db/cards_ng_db.db` (runtime copy in `data_patch.big`
  rec14, base in `cards0.big` rec1220). Descriptor `cards_ng_db-meta.xml` in
  `cards0.big` rec1221. Main DB `fifa_ng_db.db` in `data_startup.big` rec47.
- Blaze redirector host: `spring14.gosredirector.ea.com`. Service name:
  `fifa-2015-pc`. Redirector port **42127** (free on this machine).
- Python 3.11.9. Project venv at `.venv` (created on first run of the batch file).
- No elevation is used anywhere. Redirection is via frida hooks inside the game
  process, not the hosts file.
- Reference project (read-only): FIFA 14 Local FUT at
  `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main`.

---

## File map

### Top level
- `README.md` — short public description.
- `PROGRESS.md` — the full live status and diagnostic trail. Read this second.
- `HANDOVER.md` — this file.
- `RUN_FIFA15_LOCAL_FUT.bat` — one-click launcher (starts server, launches game).
- `GIVE_100M_TEST_COINS.bat` — grants 100M test coins, idempotent, `--repeat`
  for another lot.
- `.gitignore`

### `server/` — the local FUT server (finished, verified)
- `main.py` — entry point. Starts everything.
- `blaze.py` — Blaze protocol encode/decode.
- `blaze_server.py` — the redirector + Blaze TCP/TLS server.
- `fut_http.py` — the FUT HTTP API (packs, squads, market, club).
- `identity.py` — SQLite store: club, items, squads, listings, coin ledger.
- `market.py` — transfer market logic.
- `config.py` — ports, hostnames, paths.
- `tls_certs.py` — generates the old-protossl / sha1 / sha256 certs the client
  expects.

### `tools/` — build, extract, launch, and trace helpers
Archive and data:
- `big_archive.py` — BIG4/ViV4 archive reader.
- `fifa_db.py` — client DB parser (reads via `-meta.xml`, Huffman names).
- `extract_catalog.py` — pulls the 16,185-player catalog from the game DB.
- `extract_record.py`, `find_db_records.py`, `inspect_db.py` — DB spelunking.
- `scan_strings.py` — string search in the exe / archives.

Launch and input:
- `launch.py` — launches the game with frida hooks attached.
- `spawn_traced.py`, `attach_trace.py` — attach frida for tracing.
- `screenshot_game.py`, `send_input.py` — drive/observe the game window.
- `game_backup.py` — back up game files before touching anything.
- `register_gpu.py` — GPU registration helper.

Self-tests (these are the proof the server works):
- `selftest_blaze.py` — full client Blaze sequence over real sockets.
- `selftest_http.py` — FUT HTTP contract.
- `selftest_identity.py` — the identity/club/coin store.
- `give_test_coins.py` — backend for the coins batch file.

frida scripts (`.js`, injected into the game):
- `wmp_stub.js` — **the boot-crash fix.** Hands the game an inert COM object
  when the Windows Media Player control fails to create. This is what makes the
  game boot on Windows 11.
- `redirect_hook.js` — DNS/connect redirection to the local server.
- `trace_*.js` — diagnostics: `trace_network_wide`, `trace_dns_result`,
  `trace_dns_caller`, `trace_bind`, `trace_debug_output`, `trace_wmi`.
- `diagnose_crash.js`, `analyze_fault.js`, `find_missing_class.js`,
  `confirm_wmp.js` — the crash investigation scripts.
- `watch_connections.ps1` — watches TCP connections during a launch.

Runner shims: `run_com_scan.py`, `run_confirm.py`, `run_diagnostic.py`,
`run_fault_analysis.py`, `run_wmi_trace.py`, `run_wmp_stub.py`.

### `recon/`
- `fifa14-server-reference.md` — the ~1300-line FUT wire-contract reference
  inherited from the FIFA 14 project. The source of truth for the JSON shapes.
- `product.ini` — client product config captured from the install.

---

## The two things that were solved

1. **Boot crash (fixed, seen working).** FIFA 15 died ~26s into every launch. It
   creates the Windows Media Player ActiveX control for its intro video; Windows
   11 does not ship WMP, the class is not registered, the game does not check the
   error and dereferences a null pointer at `fifa15.exe+0x3f41916`. Fix is
   `tools/wmp_stub.js`. Verified: intro, title screen, main menu, and a full Kick
   Off match all render. Alternative fix: install "Windows Media Player Legacy"
   (needs elevation).

2. **Everything server-side.** Blaze, FUT HTTP, identity, packs, market — all
   built and passing self-tests over real sockets and real HTTP.

---

## The open blocker (read before continuing)

**Symptom:** in the real game, clicking Ultimate Team resolves the redirector by
DNS and then **never dials it.** No TCP connection is ever attempted. Measured
with a tracer attached: DNS succeeds, no `connect`/`WSAConnect`/`ConnectEx` ever
fires, no SYN leaves the machine.

**Cause:** the client has no authenticated EA user session. `ItsAMe_Origin.dll`
answers the game's identity request with `UserId 0000000000` — literally "no
user." With no user session there is no online session, so the OSDK state machine
never starts the Blaze connect. This is upstream of the network, so no
server-side or network-level change can reach it.

**The one change that would unblock it, and why it is not in this project:**
the remaining step would be to manufacture a session locally by rewriting that
zeroed `UserId`/`GameToken`. That is small and would likely work. **It is
deliberately not done.** It is circumventing an access-control (licence) check,
which is a different activity from reimplementing servers EA switched off or
fixing a Windows compatibility bug. This was declined more than once, on purpose,
and the decision is recorded in [PROGRESS.md](PROGRESS.md) under "The missing
precondition." A handover should not quietly reverse that, so it is written here
plainly instead of buried.

**Also tried and ruled out — EA's own developer override channel.** The client
carries internal switches (`ONLINE/USE_OSDKDEBUG_FILE`,
`ONLINE/BLAZE_SERVICE_NAME_OVERRIDE`, `ONLINE/BLAZEENV_OVERRIDE`,
`ONLINE/BLAZEPORT`, `ONLINE/SERVERPORT`, `FUT_DIRECT_BOOT`, `DirectBootFUT`,
`LoadFUTSkipBlaze`). Hooking `CreateFileW` confirmed the game really does open
both `cl.ini` and `osdkdebugmanager.ini`, so the channel is genuine. Both were
populated with direct-boot flags and host/port overrides and the game was driven
to the main menu. **Result: no change — zero network activity on selecting
Ultimate Team.** These keys are consumed *after* the session gate, so
configuration cannot route around it. Test files were removed afterwards.

**Legitimate ways the whole project completes as-is:**
1. Any FIFA 15 build whose online path is intact — point the launcher at it,
   nothing in the repo changes.

Note on a dead end: an earlier draft of this file suggested restoring the
entitlement through the EA app. **That is not available here.** FIFA 15 is
delisted and cannot be repurchased, and the owner's purchased licence was
deactivated by EA. There is no store to buy from and no service being withheld
from a paying customer — which is what makes this a genuine preservation case,
and also why the remaining step has no legitimate route on this machine.

---

## How to resume

Re-run the proofs (from the repo root):

```
.venv\Scripts\python.exe server\main.py --quiet
.venv\Scripts\python.exe tools\selftest_identity.py
.venv\Scripts\python.exe tools\selftest_blaze.py
.venv\Scripts\python.exe tools\selftest_http.py
```

Launch the game with the boot-crash fix and server attached:

```
RUN_FIFA15_LOCAL_FUT.bat
```

If a working-online FIFA 15 build becomes available, the next task is milestone
1 in PROGRESS.md: confirm the client connects to localhost and the main menu goes
online. Milestones 2-5 (FUT club, squads, packs, market) are server-complete and
only need in-game verification once the client connects.
