# FIFA 14 Local FUT Server — Implementation Reference

Recon notes for rebuilding the FIFA 14 local FUT server for FIFA 15.
Everything below is derived by reading the source; nothing is speculated. Where the
code is ambiguous or a contract is explicitly marked "unverified", it says so.

Source tree read (all read-only):

| File | Lines | Role |
|---|---:|---|
| `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main\server\probe.py` | 4,987 | Blaze/FIRE TCP servers, redirector, TLS cert generation, FUT HTTP REST server (`HttpProbe`), CLI |
| `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main\server\local_identity.py` | 4,945 | `LocalIdentityStore` — SQLite schema, ItemData model, packs, market, squads |
| `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main\server\beta_identity.py` | 2,670 | `BetaIdentityStore(LocalIdentityStore)` — wallet ledger, match settlement, cosmetics, seasons/tournaments |
| `F:\FIFA-14-Local-FUT-Beta2\FIFA-14-Local-FUT-main\server\fifa14_ids.py` | 78 | resourceId/definitionId math |
| `pack-catalog.v237.json` / `pack-weights.v237.json` | — | pack SKUs and draw tuning |
| `fifa14-player-catalog.v237.json` | 10,330 players | base card identities |
| `fifa14-special-catalog.v240.json` | — | IF/TOTY/MOTM/blue/green variants |
| `fifa14-legend-catalog.v24013.json` | 42 cards | Legends (disabled on PC) |
| `fifa14-consumable-catalog.v2412.json` | 151 items | consumables |
| `manager-catalog.v237.json` | — | manager reference (live emission disabled) |
| `icebreakerpacklist.v27.json` | 4 captain packs | Icebreaker fixture |

Build string reported by the server: `2.41.1-beta2.25.9` (`probe.py:2414`, `probe.py:4917`).

---

## 1. FUT HTTP endpoint map

### 1.1 Dispatch mechanics

All HTTP verbs land on one method: `HttpProbe._handle` (`probe.py:2371`), aliased to
`do_GET/do_POST/do_PUT/do_DELETE` (`probe.py:4278-4281`). Routing is a long `elif`
chain — **first match wins**, so ordering matters.

Key dispatch facts:

- **Listener identity gates every route.** `self.server.probe_name` is one of
  `fut-http`, `bootstrap-http`, `dynamic-http`, `redirector`, `main-blaze`,
  `origin-lsx`. Almost every `/ut/...` route requires `probe_name == "fut-http"`.
- **Method override header.** `effective_method = headers["X-HTTP-Method-Override"] or self.command`,
  uppercased (`probe.py:2386-2389`). FIFA tunnels DELETE/PUT through POST.
- **Query is stripped before matching**: `path_without_query = self.path.partition("?")[0]`
  (`probe.py:2384`).
- **Body cap** 1 MiB (`probe.py:2373`).
- **Every JSON response** is produced by `build_fut_json_payload` (`probe.py:68`):
  `json.dumps(doc, separators=(",",":")) + "\n"`, UTF-8. Compact separators, trailing newline.
- Standard headers: `content-type: application/json; charset=utf-8`, `cache-control: no-store`,
  `content-length`. Some routes add `connection: close`.
- **Catch-all**: any unmatched `/ut/*` on `fut-http` returns `200 {}` (`probe.py:4249-4269`).
  Anything else returns `501 {"error":"research probe only"}`.

### 1.2 Auth / session

| Method | Path | Request | Response shape |
|---|---|---|---|
| any | `/ut/auth` | opaque JSON body (logged, stored as `sessions.client_payload`) | `{sid: str, serverTime: ISO8601Z, lastOnlineTime: "1970-01-01T00:00:00Z"}`. Also sets header `x-ut-sid: <sid>`. `probe.py:2715-2756`, `build_fut_auth_response` `probe.py:180` |
| any | `/ut/delete/auth` | — | `{}` (`probe.py:4145-4167`) |
| any | `/ut/game/fifa14/phishing`, `/ut/game/fifa14/phishing/question` | — | untrusted: `{question:int, attempts:5, recoverAttempts:20}`; trusted: `{debug:str, token:str}`. Sets `set-cookie: FUTWebPhishing=...` when `token` present. `probe.py:2784-2814`, `local_identity.py:608` |
| any | `/ut/game/fifa14/phishing/validate` | — | `{debug:str, string:"OK", code:"200", reason:str, token:str}`; flips `identity.trusted=1`. `local_identity.py:628` |
| any | `/ut/game/fifa14/phishing/trusteddevice` | — | `{trusted:bool, changed:bool, exists:bool, locked:bool, deviceId:str}`. `local_identity.py:615` |
| any | `/ut/game/fifa14/settings` | — | `{maximumTradePileSize:30, getOperationTimeoutSec:300, clubCreateThreshold:0, fifaPointsCancelTransactionFix:1, tokenRedemptionEnabled:0, enableWorldCupMode:0}`. `probe.py:196` |

### 1.3 User / club

| Method | Path | Request | Response shape |
|---|---|---|---|
| GET | `/ut/game/fifa14/user/accountinfo` | — | `{userAccountInfo:{personas:[{personaId:int, personaName:str, returningUser:0\|1, onlineAccess:bool, trial:false, userState:null, userClubList:[club], trialFree:false}]}}`. `local_identity.py:471` |
| GET/POST/PUT | `/ut/game/fifa14/user` | POST/PUT with `{clubName, clubAbbr}` persists the club | `{personaId, personaName, userId, created:int, returningUser:0\|1, clubName:str, clubAbbr:str, badgeId:int, teamId:int, activeSquadId:int\|null, userClubList:[club], <COMPLETED_ACTION_NAME>:true ...}`. `local_identity.py:537` |
| GET | `/ut/game/fifa14/user/list` | — | `{userInfo:[ <same as /user> ]}` (`probe.py:3895-3914`) |
| GET | `/ut/game/fifa14/user/credits` | — | `{credits:int, fifaPoints:int, bidTokens:{count:int,updateTime:int}, currencies:[{name:"coins",funds,finalFunds},{name:"points",...}], unopenedPacks:{preOrderPacks:0,recoveredPacks:0}}`. `local_identity.py:3518` |
| GET | `/ut/game/fifa14/user/action?actionType=NAME` | — | **Object whose keys are completed action names, all `true`.** New persona → `{}`. `probe.py:3141-3190` |
| any | `/ut/game/fifa14/user/action/<ACTION_NAME>` | `DELETE` clears | `{}` (200) or `{code:"400", reason:str}`. `probe.py:3191-3243` |
| POST | `/ut/game/fifa14/user/club` | `{clubName, clubAbbr, badgeId, teamId}` | `{club:{...club document...}}`. `probe.py:3244-3263` |
| GET | `/ut/game/fifa14/hub` | — | `{auctionCount:int, clubPlayers:int, tradePileCount:int, tradePileItems:int, transferListCount:int, selling:int, sold:int}`. `local_identity.py:3597` |
| GET | `/ut/game/fifa14/userdata` | — | `{userData:[]}` (`probe.py:220`) |

**Club document** (`local_identity.py:446`):
```
{year:"2014", assetId:int(club_id), teamId:int, lastAccessTime:int(epoch), platform:"pc",
 clubName:str, clubAbbr:str, established:int, divisionOnline:int, badgeId:int,
 skuAccessList:{"FFA14PC": int(epoch)}}
```

### 1.4 Club stats / consumable stats

| Method | Path | Response |
|---|---|---|
| GET | `/ut/game/fifa14/club/stats/year` | `club_stats(context_id=2, context_value=2014)` |
| GET | `/ut/game/fifa14/club/stats/newcards` | `club_stats(context_id=5, context_value=0)` |
| GET | `/ut/game/fifa14/club/stats/consumables` | `consumable_stats()` — **context 6**, different shape |
| GET | `/ut/game/fifa14/club/stats/country/<n>` | `club_stats(context_id=3, context_value=n, nation=n)` |
| GET | `/ut/game/fifa14/club/stats/league/<n>` | `club_stats(context_id=4, context_value=n, league=n)` |
| GET | `/ut/game/fifa14/club/stats/staff` | `{itemData:[]}` |
| GET | `/ut/game/fifa14/club/consumables?...` | forces `type=consumable`, then `club_items(query)` |

`club_stats` returns (`local_identity.py:3733`):
```
{stat:[{contextId:int, contextValue:int, type:str, typeValue:int}...],
 entries:<same array>,
 playerCount, totalPlayers, players, rarePlayers,
 playersBronze, playersSilver, playersGold,
 staff, stadia, balls, kits, badges, trophies}
```
`type` values emitted: `players, playersBronze, playersSilver, playersGold, rarePlayers,
staff, stadia, balls, kits, badges, trophies`. Gold ≥75, silver 65–74, bronze 1–64.

`consumable_stats` returns (`local_identity.py:3644`) named scalar members **plus** the
same values as context-6 stat rows:
```
{consumablesContractPlayer, consumablesContractManager, consumablesFitnessPlayer,
 consumablesFitnessTeam, consumablesHealing, consumablesTrainingPlayer,
 consumablesTrainingGk, consumablesTrainingPlayerPlayStyle, consumablesTrainingGkPlayStyle,
 consumablesPosition, consumablesTrainingManager, consumablesTrainingManagerLeagueModifier,
 consumablesFormationManager,
 consumablesContract, consumablesFitness, consumablesTraining, consumables,
 stat:[{contextId:6, contextValue:0, type:<memberName>, typeValue:int}...],
 entries:<same>}
```

### 1.5 Items / My Club

Handled by one block (`probe.py:3264-3489`) covering
`/ut/game/fifa14/club`, `/ut/game/fifa14/clubUser`, `/ut/game/fifa14/item`,
`/ut/game/fifa14/item/resource`, `/ut/game/fifa14/item/<id>`,
`/ut/game/fifa14/item/resource/<id>`, `/ut/delete/game/fifa14/item`.

| Effective method + path | Behaviour | Response |
|---|---|---|
| `DELETE /item`, `DELETE /item/resource`, `POST /ut/delete/game/fifa14/item` | quick-sell. IDs from query keys `itemIds\|itemId\|ids\|id` (comma-split) or body `itemId\|itemIds\|itemData` | `{items:[{id,itemId,discardValue}], itemData:<same>, totalCredits:int, credits:int}` (`local_identity.py:3370`) |
| `POST /item/resource/<resourceId>` | apply consumable; body `{apply:[{id\|itemId}...]}` | `{}` on success (success-by-status), or `{code:"400", reason:str}` |
| `POST\|PUT /item`, `/item/<id>`, `/item/resource`, `/item/resource/<id>` | pile move / activation. Body `{itemData:[{id,pile,itemState}...]}`, or root-level `{itemState}` when addressing `/item/<id>` | `{itemData:[{id,itemId,success:bool,reason:str,errorCode:int,pile:int}...]}` (`local_identity.py:3266`) |
| `GET /item/<id>` or `GET /item?idList=`/`itemIds=`/`ids=`/`id=` | view specific cards | `{itemData:[ItemData...], total:int, count:int}` (`beta_identity.py:1064`) |
| `GET /club`, `GET /clubUser`, `GET /item` (no ids) | paged collection | `{itemData:[ItemData...], total:int, count:int, start:int}` (`local_identity.py:3802`) |

`club_items` query filter fields (`local_identity.py:3814-3831`):

| Param (aliases) | Meaning |
|---|---|
| `start` | offset, default 0 |
| `count` | page size, default 50, clamped 1..200 |
| `type` | `player` (default) / `consumable` / `development` / `training` / `kit` / `stadium` / `custom` / `ball` / `trophy` |
| `level` (`lev`) | `any` / `bronze` / `silver` / `gold` / `sp`; numeric aliases `1=bronze 2=silver 3=gold 4=sp 10=any` |
| `position` (`pos`) | e.g. `ST`, matched against `preferredPosition` |
| `team` (`club`) | teamId, `-1` = any |
| `nation` (`nat`) | nation id, `-1` = any |
| `league` (`leag`) | leagueId, `-1` = any |
| `cat` (`category`) | consumable family: `contract fitness healing training gktraining position playstyle managerleague` |

Sort order: rating desc, rareflag desc, assetId asc, itemId asc.

`/clubUser` with **no explicit `type`** additionally appends every owned consumable to
page 0 (`include_consumables_default=True`, `probe.py:3452`, `local_identity.py:3877`).

### 1.6 Squad

Block at `probe.py:3490-3541`. All under `identity_store` guard.

| Method | Path | Behaviour | Response |
|---|---|---|---|
| PUT/POST | `/ut/game/fifa14/squad` or `/squad/<id>` | `save_squad(body, requested_id)` then return detail | full SquadDetails |
| GET | `/ut/game/fifa14/squad/list` | `squad_list_compact()` | `{activeSquadId:int, squad:[compact...]}` |
| GET | `/ut/game/fifa14/squad/active` | `active_squad_document()` | full SquadDetails |
| GET | `/ut/game/fifa14/squad/<id>` | `squad_detail(id)` | full SquadDetails |
| GET | `/ut/game/fifa14/squad` | `squad_list_compact()` | list |

Full **SquadDetails** record (`local_identity.py:4835-4858`):
```
{id:int, squadId:int, personaId:int, squadName:str, formation:str,
 active:bool, changed:bool, captain:int(itemId), chemistry:int, starRating:int,
 rating:int, valid:true, newsquad:0, kicktakers:[], tactics:[],
 dreamSquad:false, custom:"", manager:[], actives:[],
 players:[{index:0..22, itemData:<ItemData or {"id":0}>, kitNumber:int} × 23]}
```
`squad_list()` wraps as `{squadList:[...], squad:[...]}` (both aliases).

**Compact** record (`local_identity.py:4862`):
`{id, personaId, squadName, formation, active, changed, chemistry, starRating, rating, valid, newsquad}`

### 1.7 Packs / store

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/ut/game/fifa14/store*`, `/ut/v2/game/fifa14/store*` (no `/transaction`) | — | `store_pack_types()` (below) |
| GET | same, when `"quantity" in path.lower()` | — | `{packList:[{packType:int, quantity:int}...]}` |
| POST/PUT | `/ut/game/fifa14/store*`, `/ut/v2/game/fifa14/store*` (no `/transaction`) | pack selector | pack purchase document |
| POST/PUT | **`/ut/game/fifa14/purchased/items`** ← the real retail coin-purchase route | `{"packId":N,"useCredits":1,"usePreOrder":0,"currency":"COINS"}` | pack purchase document |
| GET | `/ut/game/fifa14/purchased`, `/ut/game/fifa14/purchased/items` | — | `purchased_items()` |
| GET | `/ut/v2/game/fifa14/store/transaction*` | — | `{transactionId:0, state:"NOTRANSACTION"}` |
| POST/PUT | `/ut/v2/game/fifa14/store/transaction/<id>` | `{state, transactionId, ...}` | see below |

`_store_pack_selector` (`probe.py:2327`) reads the first numeric of:
`packId, id, packType, purchasePackTypeId, serverId, serverID, serverid, assetId`, then
falls back to a numeric `purchasePackType`.
`_decode_store_purchase` (`probe.py:2300`) accepts JSON or form-encoded, and flattens a
single-object/array wrapper named `purchase`, `purchaseItem`, `item` or `offer`.

Currency resolution everywhere: `request.currency or request.currencyId or ("COINS" if request.useCredits else "FIFA_POINTS")`.

**Transaction POST states** (`probe.py:2882-2949`):
- `state == "TRANSACTIONCANCEL"` → `{state:"TRANSACTIONCANCEL", transactionId:int}`, never charges.
- transaction id already exists → replay `purchase_transaction(id)` idempotently.
- numeric pack selector present → `purchase_pack(...)`.
- otherwise → `{state:<requested or "NOTRANSACTION">, transactionId:int}`.

**`store_pack_types()`** (`local_identity.py:2861`) — native contract is only `purchase` + `timestamp`:
```
{purchase:[offer...], timestamp:int,
 packList:[offer + aliases...], packTypes:<same>, total:int,
 unopenedPacks:int, credits:int, fifaPoints:int}
```
Each **offer**:
```
{actionType:"CREATEPACK", assetId:int, bonus:0,
 currencies:[{name:"coins",funds:int,finalFunds:int},{name:"points",...}],
 name:"LOCAL_PACK_NAME_<packType>",         # localization key, not prose
 description:"FUT_STORE_PACK_<packId>_DESC", nameToken:..., descriptionToken:...,
 displayGroup:{priority:int, value:"bronze"|"silver"|"gold"},
 displayGroupAssetId:int, displayGroupUseDefaultImage:true,
 end:int, firstPartyStoreId:"0", id:int(packType), isPremium:bool,
 isSeasonTicketDiscount:false, points:int, priority:int,
 packId:int, packType:int, purchasePackType:"CARDPACK",
 purchaseCount:0, purchaseLimit:999999, quantity:int(totalCards),
 saleType:"NONE", sortPriority:int, start:int, state:"active",
 unopened:false, useDefaultImage:true, visible:true,
 dealType:"promo"}                          # only when category == PROMO
```

**Pack purchase document** (`_pack_purchase_response_document`, `local_identity.py:3093`):
```
{packId:int, firstPartyStoreId:0, purchasePackType:"CARDPACK",
 state:"PURCHASECOMPLETE", transactionId:int, useAuth:0, useCount:1, useTime:0,
 # compat/diagnostic:
 purchasedPackId:int, purchasePackTypeId:int, packType:int,
 credits:int, fifaPoints:int, itemData:[ItemData...],
 duplicateItemIdList:[{itemId:int, duplicateItemId:int}...],
 createPackResponse:{duplicateItemIdList:[...], itemList:[ItemData...],
                     numberItems:int, purchasedPackId:int,
                     itemData:[...], packId:int, packType:int},
 unopenedPacks:int}
```

`purchased_items()` (`local_identity.py:3421`):
```
{duplicateItemIdList:[{itemId,duplicateItemId}...],
 itemData:[flattened ItemData from all unopened packs + pile='pending' market wins],
 unopenedPacks:[{packId,packType,packName,itemData:[...]}...],
 packList:<same>}
```

### 1.8 Transfer market

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/ut/game/fifa14/transfermarket?...` | search filters (§5) | `{auctionInfo:[Auction...], duplicateItemIdList:[], total:int, credits:int, totalCredits:int, coins:int}` |
| GET | `/ut/game/fifa14/tradePile` \| `/tradepile` | — | trade pile document (§5.7) |
| any | `/ut/game/fifa14/watchList` \| `/watchlist` | — | `{auctionInfo:[], duplicateItemIdList:[], total:0}` |
| POST | `/ut/game/fifa14/auctionhouse` | `{itemData:{id}\|itemId, startingBid, buyNowPrice, duration}` | one Auction + `marketValue`, `cheapestMarketPrice` |
| POST | `/ut/game/fifa14/trade` | same as auctionhouse | same |
| DELETE | `/ut/game/fifa14/auctionhouse/<tradeId>` | — | `{id:int, tradeId:int}` |
| DELETE | `/ut/game/fifa14/trade/<tradeId>` | — | `{id, tradeId}` |
| GET/DELETE/POST | `/ut/delete/game/fifa14/trade/<tradeId>` | — | `{id, tradeId}` (retail "clear sold" route) |
| GET | `/ut/game/fifa14/trade/status?tradeIds=1,2,3` | — | `{auctionInfo:[...], duplicateItemIdList:[], total, credits, totalCredits, coins}` |
| PUT/POST | `/ut/game/fifa14/trade/<id>/bid` or `/offer` | `{bid\|buyNowPrice\|amount}` | Auction + `{currentBid, offers, bidState, tradeState, credits, totalCredits, coins}`; on failure `{reason, tradeId, ...}` |
| GET | `/ut/game/fifa14/trade/<id>/offer` | — | `market_status([id])` — deliberately not the 54-byte empty document |
| any | any other `/ut/game/fifa14/trade*` | — | `{auctionInfo:[], duplicateItemIdList:[], total:0}` |

**Auction record** (`_market_auction`, `local_identity.py:4109`):
```
{tradeId:int, tradeState:"active"|"closed"|"inactive",
 expires:int, EXPIRE_TIME:int, expireTime:int,   # three aliases, same seconds value
 startTime:0, endtime:2147483647,
 buyNowPrice:int, startingBid:int, currentBid:int, offers:int,
 watched:false, bidState:"none", tradeOwner:bool,
 sellerName:str, sellerEstablished:2013, sellerId:int, confidenceValue:100,
 itemData:<ItemData>}
```

### 1.9 Match

| Method | Path | Request | Response |
|---|---|---|---|
| POST/PUT | `/ut/game/fifa14/match` with `squadId` and no result markers | create match | `{squad:<SquadDetails with actives>, startDateTime:int}` |
| PUT | `/ut/game/fifa14/match` with `items:[...]` and no `squadId` | MatchReady; persists starting XI item ids | `{squad:<SquadDetails>, startDateTime:int}` |
| POST/PUT | `/ut/game/fifa14/match` otherwise | `settle_match(body)` | settlement document |
| GET | `/ut/game/fifa14/match` | — | `{match:null, credits:int}` |
| any | `/ut/game/fifa14/match/end` | DestroyMatch body | see below |
| any | `/ut/game/fifa14/match/reset` | optional body | empty body → `{status:"reset", stadium:str}`; with body → `{matchId, status:"active", stadium, mode}` |

Result-marker detection set (`probe.py:4099-4104`): `minutesPlayed, minutes, matchMinutes,
goals, goalsScored, goalsFor, homeGoals, goalsAgainst, goalsConceded, awayGoals, result,
completed, matchCompleted, finished, dnf, didNotFinish, quit, abandoned, shotsOnTarget,
passAccuracy, possession, successfulTackles`.

`/match/end` response (`beta_identity.py:2241` → 2375+):
```
{endReason:"WIN"|"DRAW"|"LOSS"|"QUIT"|"DNF"|"NO_CONTEST",
 secondsPlayed:int, matchDifficulty:int,
 items:[{id, contract, fitness, injuryGames, injuryType, ...}],
 matchData:str,
 # only for WIN/DRAW/LOSS:
 completionAward:int, skillAward:int, rewardCoins:int, totalCoins:int,
 credits:int, coins:int, dnfModifier:float,
 tournamentPrize?:int, tournamentId?:int, tournamentRound?:int,
 # for everything except QUIT/DNF:
 myMatchStats:{goals, shotsOnTarget, successfulTackles, corners, cleansheets,
               passingPercentage, possessionPercentage, manOfTheMatch, fouls,
               yellowCards, redCards, offsides},
 opponentMatchStats:<same keys>}
```
Fallback when no `settle_match_end` method exists (`probe.py:4045-4051`):
`{endReason, secondsPlayed:0, matchDifficulty:0, items:[], matchData:str}`.

### 1.10 Seasons / tournaments

| Method | Path | Response |
|---|---|---|
| GET | `/ut/game/fifa14/season` and `/season/*` | `{seasons:[record...]}` |
| GET | `/ut/game/fifa14/season/user` | `{seasonId:1, divisionId:10, round:int}` |
| GET | `/ut/game/fifa14/tournament` (and unknown subpaths) | `{tournament:[record...]}` |
| GET | `/ut/game/fifa14/tournament/teams?count=N` | `{teamId:[int, ...]}` — **array only, nothing else** |
| GET | `/ut/game/fifa14/tournament/user/list` | `{tournamentId:[int...]}` |
| GET | `/ut/game/fifa14/tournament/user/<id>` | tournament progress record |
| PUT/POST | `/ut/game/fifa14/tournament/user/<id>` | `{tournamentId, round, ...}` |
| GET | `/ut/game/fifa14/friendlyseason/user` | `{userInfo:[]}` |

### 1.11 Client data / misc

| Method | Path | Response |
|---|---|---|
| GET | `/ut/game/fifa14/clientdata/pileSize` | `{entries:[{key:2,value:20000},{key:3,value:20000},{key:4,value:20000}]}` |
| GET/PUT/POST | `/ut/game/fifa14/clientdata/tutorialpopups` \| `/userHubData` \| `/managerquest` | persisted blob echo, `{}` when absent |
| any | `/ut/game/fifa14/managerquest` | `{}` |
| any | `/ut/game/fifa14/clientdata`, `/activeMessage`, `/leaderboards`, `/leaderboards/*` | `{}` |
| any | `/ut/game/fifa14`, `/ut/game/fifa14/`, `/utStats`, `/ut/delete/auth` | `{}` |
| GET | `/__fifa14_local_fut_health` | `{ok, buildVersion, pid, instanceToken, probe, identityDb, fullItemDataRequired:[...], samplePlayer:{...}}` |
| GET | `/health` (any listener) | `{"ok":true}` |
| GET | `/local/beta/metrics`, `/local/beta/profile`, `/local/beta/wallet` | BETA diagnostics |
| GET | `/local/managers`, `/local/identity` | catalog / snapshot |
| POST | `/local/onboarding/club` | `{club:{...}}` |

### 1.12 Static / CDN replacement routes (listeners `fut-http` and `dynamic-http`)

| Match | Response |
|---|---|
| `*/fut/packs/icebreaker/icebreakerpacklist.json` or `*/packs/icebreaker/icebreakerpacklist.json` | validated fixture; 500 on invalid fixture |
| `*/loc/pc/icebreaker.eng_us.xml` | captain-names locstrings XML |
| `*/loc/pc/leaderboards.eng_us.xml` | league/nation locstrings XML |
| `*packs/loc/storepackdescriptions.*` | store pack description locstrings XML |
| `*/fut/loc/*.xml` | local UI locstrings XML |
| `*/2014/fut/items/web/*.json` | player metadata (`players.json` = full list; `<assetId>.json` = one record); 404 if unknown |
| `*/fut/items/images/*.png|jpg|jpeg` | **404, deliberately** (no fabricated art) |
| `*/fut/items/images/*.big` | 404, except `/fut/items/images/trophies/pc/*.big` which returns an empty BIGF container (env `FIFA14_TROPHY_ARCHIVE_MODE`) |
| `/fut/items/(pc\|ps3\|xbox360)/<n>.json` | cosmetic resource, or `{}` 200 for id 0/-1 (env `FIFA14_SEASON_ITEM0_MODE`), else `{}` 404 |
| `/fut`, `/fut/*`, `/fifa/fltonlineassets/**/fut/**` | empty `<MESSAGES>` XML |
| `/futBoot.xml` on `bootstrap-http` only | minimal `<FutCfg>` document (`probe.py:224`) |

Player metadata record (`probe.py:2169`):
`{assetId, id, rating, position, teamId, leagueId, nation, name, commonName, resourceId, rareFlag}`

### Gotchas — HTTP layer

- **`/ut/game/fifa14/user/action` branches on key *presence*, not value.** Emitting
  `INTRO_DONE:false` is worse than omitting it. `CHARITY_MATCH_PLAYED` and
  `ICEBREAKER_ENGLISH_CAPTAIN_SELECTED` are **never** returned — exposing them on a later
  login re-enters the broken charity-match path (`local_identity.py:527-534`, `575-592`).
- **`/hub` ≠ `/clientdata/userHubData`.** `/hub` is native `FutGetHubDataServerResponse`
  with scalars `auctionCount` + `clubPlayers`; `userHubData` is a persisted client blob.
  Confusing them was a real bug (`probe.py:3876-3879`, `probe.py:2975-2978`).
- **`/club/stats/consumables` is not a StickerBook player-stat response.** Returning player
  counters there makes the squad screen conclude zero consumables exist (`local_identity.py:3645-3651`).
- **The Store's real purchase route is `POST /ut/game/fifa14/purchased/items`, not `/store`.**
  Older builds treated every method on that endpoint as a read, so the client advanced to
  New Items while charging nothing and returning zero cards (`probe.py:3696-3706`).
- **`actionType` must be `CREATEPACK`.** A local invention (`BUY_PACK`) stopped the frontend
  reaching the native PurchasePack wrapper (`local_identity.py:2929-2935`).
- **Store offer currency names are lowercase `coins`/`points`** — compared against legacy
  literals; `/user/credits`'s top-level scalars are separate (`local_identity.py:2940-2947`).
- **Store offer `name`/`description` are localization keys**, not prose. Literal prose renders
  as the frontend's `*` missing-text marker (`local_identity.py:2949-2955`).
- **`duplicateItemIdList` entries are pairs**, `{itemId, duplicateItemId}`, not bare ids. A
  bare id rendered as a normal item and made "Send All to Club" hit error 472 (`local_identity.py:3048-3054`).
- **Never return a closed auction with `itemData:{}`** — the trade-pile parser dereferences
  the item even for a closed auction; that caused an access violation (`local_identity.py:4558-4560`).
- **`offers` must be 0 for a completed bot Buy Now.** Reporting `offers:1` surfaced the retail
  "offer received" action and routed into `/trade/<id>/offer` (`local_identity.py:4139-4143`).
- **Empty pileSize response leaves the Transfer List at 0/0** and every add is treated as
  full (`probe.py:214-217`).
- **`tournament/teams` must return only a `teamId` array.** Returning the tournament
  catalogue caused a crash immediately after the request (`beta_identity.py:2473-2479`).
- **`season/user` `round` is decremented by the client**; wire `0` becomes `0xFFFF` sentinel,
  so wire round 1 = first match (`beta_identity.py:2432-2450`).
- Missing card art is answered **404, not XML** — preserving FIFA's normal missing-art
  fallback and giving a precise trace path (`probe.py:2531-2533`).

---

## 2. The canonical ItemData payload

Built by `LocalIdentityStore._canonical_player_payload` (`local_identity.py:1654`).
**Key order is deliberate**: native-critical members first, compatibility aliases after,
so an older parser that stops early still has position/type/identity.

### 2.1 Player ItemData

| Field | Type | Meaning / notes |
|---|---|---|
| `id` | int | Item instance id (unique per owned card) |
| `assetId` | int | The footballer. Catalogue key. |
| `resourceId` | int | Card revision. **v1 → equals `assetId`**; special → `definition_id_for(assetId, version)`. Forced to the expected value if it disagrees. |
| `rating` | int | 1–99, clamped |
| `preferredPosition` | str | `"ST"`, `"CM"`, `"GK"`… Drives **card face layout**. `SUB`/`RES`/blank collapse to the slot position, else `CM`. |
| `teamid` | int | **lowercase `id`** — the native-critical spelling. Chemistry input. |
| `leagueId` | int | Chemistry input |
| `nation` | int | Chemistry input |
| `itemType` | str | Always `"player"` (`PLAYER_ITEM_TYPE`) |
| `itemState` | str | `"free"`, `"forSale"`, `"sold"`, `"new"`; kit/badge activations use `activeHomeKit` / `activeAwayKit` / `activeStadium` / `activeBadge` |
| `formation` | str | `"f442"` default |
| `contract` | int | 0–99, matches remaining |
| `fitness` | int | 0–99 |
| `injuryGames` | int | matches remaining injured |
| `injuryType` | str | `"none"` or an injury name |
| `suspension` | int | |
| `training` | int | last-applied training subtype marker |
| `playStyle` | int | Chemistry style id |
| `discardValue` | int | Quick-sell coins. 0 for untradeable starters. |
| `lastSalePrice` | int | |
| `timestamp` | int | epoch seconds, ≥1 |
| `untradeable` | bool | |
| `rareflag` | int | **lowercase** — 0–255. 0 = common, 1 = rare, >1 = special variant |
| `cardsubtypeid` | int | **`1` if `rareflag` else `0`.** See gotcha below. |
| `assists` | int | |
| `lifetimeAssists` | int | |
| `attributeList` | list | `[{index:0..5, value:0..99}]`, 6 entries |
| `statsList` | list | `[{index:0..4, value:int}]`, 5 entries |
| `lifetimeStats` | list | `[{index:0..4, value:int}]`, 5 entries |
| `itemId` | int | alias of `id` |
| `teamId` | int | alias of `teamid` |
| `name` | str | |
| `commonName` | str | |
| `owners` | int | ≥1 |
| `morale` | int | 0–99 |
| `playerId` | int | alias of `assetId` |
| `rareFlag` | int | alias of `rareflag` |
| `loyaltyBonus` | int | 0 or 1 |
| `pile` | int | 0–99. **0 = market, 5 = transfer list, 6 = purchased/new items, 7 = club.** |
| `resourceGameYear` | int | `2014` |
| `attributeArray` | list[int] | 6 raw values (mirror of `attributeList`) |
| `statsArray` | list[int] | 5 raw values |
| `lifetimeStatsArray` | list[int] | 5 raw values |
| `definitionId` | int | **Always `assetId`**, even for special cards. |
| `specialCard` | bool | backend-only marker, present only when special |
| `cardType` | str | backend-only, e.g. `goldif`, `toty`, `motm`, `green`, `goldblue` |
| `version` | int | backend-only, ≥1 |
| `tradeable` | bool | added by pack/market/move paths (not in the base builder) |
| `localPackSchema` | str | backend-only pack-fidelity marker |

Attribute index order (from the training-consumable index map, `local_identity.py:2670-2674`):
- Outfield: `0=PAC 1=SHO 2=PAS 3=DRI 4=DEF 5=PHY/HEA`
- Goalkeeper: `0=DIV 1=HAN 2=KIC 3=REF 4=SPD 5=POS`

Constants: `PLAYER_ATTRIBUTE_COUNT = 6`, `PLAYER_STAT_COUNT = 5` (`local_identity.py:121-122`).

### 2.2 Which values matter for what

| Purpose | Fields |
|---|---|
| **Card art / face template** | `preferredPosition` (GK vs outfield stat template), `rareflag` + `cardsubtypeid` (common vs rare vs special), `resourceId` (revision art), `definitionId` (static name/position lookup), `rating`, `resourceGameYear` |
| **Chemistry** (computed client-side) | `teamid`/`teamId`, `leagueId`, `nation`, `playStyle`, `loyaltyBonus`, `preferredPosition` vs slot |
| **Pile placement** | `pile` (int) plus the DB column `items.pile` (`club`/`squad`/`trade`/`pending`), and `itemState` |
| **Economy** | `discardValue`, `lastSalePrice`, `untradeable`/`tradeable`, `owners` |
| **Match consumption** | `contract`, `fitness`, `injuryGames`, `injuryType`, `training` |

`items.pile` (TEXT) ↔ wire `pile` (int) mapping used by `move_items` (`local_identity.py:3287-3299`):
`trade/transfer/transferpile/tradepile → 5` (DB `trade`), `purchased/new → 6`,
`club/owned → 7` (DB `club`). Squad members use DB pile `squad`; market wins use DB pile `pending`.

### 2.3 Consumable ItemData

`_local_consumable_payload` (`local_identity.py:2408`) emits a **deliberately narrow** wire set:
```
resourceId, cardassetid, cardsubtypeid, rating, rareFlag, rareflag,
bronze, silver, gold, amount, itemType, resourceGameYear, discardValue,
id, itemId, timestamp, lastSalePrice:0, owners:1,
untradeable:false, tradeable:true, itemState:"free", pile:6
```
`itemType` is `"development"` (or `"training"`). Catalogue-only descriptive fields
(`class`, `category`, `kind`, `sourceMember`, `sourceTable`, `packEligible`,
`applicationSupported`, `weightrare`, `dataSource`) are **never sent**.

### 2.4 resourceId / definitionId math (`fifa14_ids.py`)

```
FIFA14_RESOURCE_BASE   = 0x60000000
FIFA14_VERSION_CONST   = 0x02000000
FIFA14_VERSION_STEP    = 0x01000000

resource_id_for(asset, v)   = 0x60000000 + asset + (0 if v==1 else 0x02000000 + 0x01000000*(v-1))
definition_id_for(asset, v) = resource_id_for(asset, v) - 0x60000000
```
The catalogue keeps the calculator's `0x60000000`-prefixed value as
`calculatorResourceId` for diagnostics only.

### Gotchas — ItemData

- **`cardsubtypeid` is a rare/common discriminator, not a positional band.** An earlier
  build treated it as GK/DEF/MID/ATT; mixing the two contracts sent outfield cards down the
  goalkeeper face-stat layout (`local_identity.py:108-120`).
- **Base cards use `resourceId == assetId` on FIFA 14 PC.** The fut-calculator
  `0x60000000` value is *not* what CardsDLL serializes for base owned cards
  (`local_identity.py:1580-1588`, `1672-1676`).
- **`definitionId` is always the base `assetId`.** Sending the versioned revision as
  `definitionId` made IF 91 Ibrahimović resolve as a goalkeeper (`local_identity.py:1809-1817`).
- **Field order matters.** Placing `preferredPosition` behind local aliases caused outfield
  cards to render with the GK DIV/HAN/KIC/REF/SPD/POS template (`local_identity.py:1756-1760`).
- **`teamid` (lowercase) is the native-critical spelling**; `teamId` is a compatibility alias.
  Same for `rareflag` vs `rareFlag`.
- **Consumable wire payloads must not carry local descriptive fields.** A `Positioning`
  row whose `kind` contained a Unicode arrow (`RW→RF`) froze the CardsDLL purchased-items
  parser (`local_identity.py:2409-2415`).
- Special/Legend cards keep the **verified base identity/team/league/nation** and take only
  rating, rarity, position, attributes from the variant row (`local_identity.py:1693-1709`).
- Persisted ItemData stores attributes as `attributeArray`/`attributeList`, not the
  catalogue-only `attributes` key — repair passes must read both (`local_identity.py:1703-1707`).

---

## 3. SQLite schema

Connection setup: `sqlite3.connect(db, timeout=10)`, `row_factory = sqlite3.Row`,
`PRAGMA foreign_keys = ON` (`local_identity.py:150-154`). All writes are wrapped in
`with self._lock, closing(self._connect()) as connection, connection:` — a `threading.RLock`
plus an implicit transaction. **No `CREATE INDEX` statements exist anywhere.**

### 3.1 Core tables (`local_identity.py:160-316`)

| Table | Columns | Purpose |
|---|---|---|
| `identity` | `singleton INTEGER PK CHECK(=1)`, `nucleus_id INT NN`, `persona_id INT NN`, `persona_name TEXT NN`, `platform TEXT NN`, `online_access INT NN`, `trusted INT NN`, `phishing_question INT NN`, `phishing_token TEXT NN`, `created_at INT NN` | Single-row local account identity |
| `sessions` | `sid TEXT PK`, `persona_id INT NN`, `client_payload TEXT NN`, `created_at INT NN`, `last_seen INT NN` | `/ut/auth` sessions (always one row, `DEFAULT_SID`) |
| `clubs` | `club_id INTEGER PK`, `persona_id INT NN UNIQUE`, `club_name TEXT NN`, `club_abbr TEXT NN`, `badge_id INT NN`, `team_id INT NN`, `established INT NN`, `division_online INT NN`, `coins INT NN`, `fifa_points INT NN` | The FUT club + wallet |
| `fut_users` | `persona_id INTEGER PK`, `created_at INT NN`, `active_squad_id INT`, `starter_pack_claimed INT NN DEFAULT 0` | FUT-side user record |
| `squads` | `squad_id INTEGER PK AUTOINCREMENT`, `persona_id INT NN`, `squad_name TEXT NN`, `formation TEXT NN`, `active INT NN DEFAULT 0`, `chemistry INT NN DEFAULT 0`, `star_rating INT NN DEFAULT 0` | Squad metadata |
| `squad_players` | `squad_id INT NN`, `slot_index INT NN`, `item_id INT NN`, `asset_id INT NN`, `resource_id INT NN`, `team_id INT NN`, `rating INT NN`, `rare_flag INT NN`, `play_style INT NN`, `preferred_position TEXT NN`, `attributes_json TEXT NN`, `kit_number INT NN DEFAULT 0`, **PK (squad_id, slot_index)** | Exactly 23 rows per squad; `item_id = 0` marks an empty slot |
| `items` | `item_id INTEGER PK`, `persona_id INT NN`, `asset_id INT NN`, `item_type TEXT NN`, `pile TEXT NN`, `tradeable INT NN DEFAULT 0`, `payload TEXT NN DEFAULT '{}'` | **Every owned card.** `payload` is the full ItemData JSON. `pile` ∈ `club\|squad\|trade\|pending` |
| `consumable_effects` | `persona_id INT NN`, `item_id INT NN`, `effect_type TEXT NN`, `resource_id INT NN`, `base_payload_json TEXT NN DEFAULT '{}'`, `created_at INT NN`, **PK (persona_id, item_id, effect_type)** | Stores the pre-training baseline so a second training card does not stack |
| `packs` | `pack_id INTEGER PK AUTOINCREMENT`, `persona_id INT NN`, `pack_type INT NN`, `pack_name TEXT NN`, `unopened INT NN DEFAULT 1`, `created_at INT NN` | Purchased packs. `pack_id` doubles as the store transaction id |
| `pack_contents` | `pack_id INT NN`, `ordinal INT NN`, `payload TEXT NN`, **PK (pack_id, ordinal)** | Generated pack items, deleted as each is resolved |
| `fut_actions` | `persona_id INT NN`, `action_name TEXT NN`, `completed INT NN DEFAULT 0`, `updated_at INT NN`, **PK (persona_id, action_name)** | Onboarding action flags |
| `catalog_items` | `resource_id INTEGER PK`, `name TEXT NN`, `rating INT NN`, `position TEXT NN`, `rarity TEXT NN`, `nation TEXT NN`, `base_price INT NN`, `stats_json TEXT NN` | **Created but never written or read** in the code paths inspected — appears vestigial |
| `client_data` | `persona_id INT NN`, `data_key TEXT NN`, `payload TEXT NN`, `updated_at INT NN`, **PK (persona_id, data_key)** | `/clientdata/*` blobs |
| `schema_meta` | `meta_key TEXT PK`, `meta_value TEXT NN` | Migration markers (`backend_schema`, `beta_schema`, `local_test_balance_seeded_v24011`) |
| `manager_reference` | `manager_key INTEGER PK AUTOINCREMENT`, `name TEXT NN UNIQUE`, `quality TEXT NN`, `rare INT NN`, `rating INT NN`, `contract_boost INT NN`, `resource_id INT` | Manager metadata; `resource_id` is NULL because manager IDs are unverified |
| `market_listings` | `trade_id INTEGER PK`, `persona_id INT NN`, `item_id INT NN UNIQUE`, `starting_bid INT NN`, `buy_now_price INT NN`, `duration INT NN`, `created_at INT NN`, `trade_state TEXT NN DEFAULT 'active'` + migrated: `item_payload TEXT NN DEFAULT '{}'`, `sold_price INT NN DEFAULT 0`, `sold_at INT NN DEFAULT 0`, `auto_sell_after INT NN DEFAULT 0`, `market_value_at_list INT NN DEFAULT 0` | The user's own auctions |
| `market_trends` | `resource_id INTEGER PK`, `pressure REAL NN DEFAULT 0.0`, `updated_at INT NN`, `last_price INT NN DEFAULT 0`, `total_buys INT NN DEFAULT 0`, `total_sales INT NN DEFAULT 0` | Per-card demand pressure |
| `market_synthetic_sales` | `trade_id INTEGER PK`, `resource_id INT NN`, `sold_price INT NN`, `sold_at INT NN` | Bot listings the user just bought — suppresses relisting for 15 minutes |

**Migrations** are done with `PRAGMA table_info(...)` + `ALTER TABLE ADD COLUMN`, because
`CREATE TABLE IF NOT EXISTS` will not add columns to an existing table
(`local_identity.py:318-340`). Added columns: `squads.chemistry`, `squads.star_rating`,
`squad_players.kit_number`, and the five `market_listings` columns above.

### 3.2 BETA tables (`beta_identity.py:320-418`)

| Table | Columns | Purpose |
|---|---|---|
| `beta_accounts` | `persona_id INTEGER PK`, `account_uuid TEXT NN UNIQUE`, `discord_user_id TEXT UNIQUE`, `discord_username TEXT`, `auth_state TEXT NN DEFAULT 'local-unlinked'`, `created_at INT NN`, `last_seen INT NN`, `dnf_modifier REAL NN DEFAULT 1.25` | Local BETA account, Discord-link ready |
| `wallet_transactions` | `transaction_id INTEGER PK AUTOINCREMENT`, `persona_id INT NN`, `created_at INT NN`, `currency TEXT NN`, `amount INT NN`, `balance_before INT NN`, `balance_after INT NN`, `reason TEXT NN`, `reference_type TEXT`, `reference_id TEXT`, `metadata_json TEXT NN DEFAULT '{}'`, **UNIQUE(persona_id, currency, reason, reference_type, reference_id)** | Idempotent coin ledger — the UNIQUE constraint is the idempotency key |
| `beta_match_sessions` | `match_id TEXT PK`, `persona_id INT NN`, `mode TEXT NN`, `difficulty TEXT`, `stadium_name TEXT`, `status TEXT NN`, `created_at INT NN`, `started_at INT`, `completed_at INT`, `result TEXT`, `home_goals INT`, `away_goals INT`, `minutes_played INT`, `reward_coins INT NN DEFAULT 0`, `reward_breakdown_json TEXT NN DEFAULT '{}'`, `raw_result_json TEXT NN DEFAULT '{}'`, `easfc_signal INT`, `settled INT NN DEFAULT 0` | Match lifecycle |
| `beta_counters` | `counter_key TEXT PK`, `counter_value INT NN DEFAULT 0` | Lifetime counters |
| `beta_daily_counters` | `day TEXT NN`, `counter_key TEXT NN`, `counter_value INT NN DEFAULT 0`, **PK(day, counter_key)** | Per-UTC-day counters |
| `beta_club_settings` | `persona_id INTEGER PK`, `stadium_name TEXT NN`, `home_kit_resource_id INT`, `away_kit_resource_id INT`, `badge_resource_id INT`, `updated_at INT NN` | Active cosmetics |
| `beta_offline_seasons` | `persona_id INT NN`, `season_id INT NN`, `division INT NN`, `matches_played`, `points`, `won`, `draw`, `lost`, `trophies_won`, `active INT NN DEFAULT 1`, `updated_at INT NN`, **PK(persona_id, season_id)** | Offline Seasons progress |
| `beta_offline_tournaments` | `persona_id INT NN`, `tournament_id INT NN`, `current_round INT NN DEFAULT 0`, `won INT NN DEFAULT 0`, `active INT NN DEFAULT 1`, `updated_at INT NN`, **PK(persona_id, tournament_id)** | Cup progress |
| `beta_tournament_progress` | `persona_id INT NN`, `tournament_id INT NN`, `round_value INT NN DEFAULT 1`, `data_version INT NN DEFAULT 1`, `tournament_data TEXT NN DEFAULT ''`, `progress_data_version INT NN DEFAULT 1`, `progress_data TEXT NN DEFAULT ''`, `updated_at INT NN`, **PK(persona_id, tournament_id)** | Opaque base64 bracket blobs from the client |

### Gotchas — schema

- **No indices at all.** Every lookup is a table scan. With ~10k catalogue players and a
  full club this is fine at localhost scale but is the obvious first optimisation for FIFA 15.
- **`market_listings.item_id` is UNIQUE**, so a card can hold only one auction row.
- **A closed listing keeps its `item_payload` snapshot on purpose.** Deleting the backing
  item on sale caused a CardsDLL access violation when the trade pile rendered
  (`local_identity.py:4239-4246`). `withdraw_listing` deletes the item only for `closed`
  state (= "clear sold"); `active` state returns the card to `pile='club'`.
- **Legacy `item_payload='{}'` rows are repaired at startup** and old active listings are
  re-aged so an upgrade does not instantly auto-sell everything (`local_identity.py:343-379`).
- `_repair_owned_items_locked` and `_repair_active_squad_locked` run on **every** store init
  (`local_identity.py:419-420`) and again inside `squad_list`, `club_items`, `club_stats`.

---

## 4. Pack generation algorithm

Entry: `LocalIdentityStore._generate_pack_contents_locked` (`local_identity.py:2710`),
called from `purchase_pack` (`local_identity.py:3142`).

### 4.1 Pack catalogue (`pack-catalog.v237.json`)

Per pack: `packType`, `packId`, `name`, `category` (`BRONZE|SILVER|GOLD|PROMO`),
`regular` (bool), `priceCoins`, `pricePoints`, `totalCards`, `rareCards`, `minQuality`,
`playerSlots`, optional `managerSlots`, `safeLivePool`, `description`.

| packType | Name | Coins | Pts | Total | Rares | Players | Quality | Regular |
|---:|---|---:|---:|---:|---:|---:|---|---|
| 1 | Bronze Pack | 400 | 0 | 12 | 1 | 3 | bronze | yes |
| 2 | Premium Bronze | 750 | 0 | 12 | 3 | 3 | bronze | yes |
| 3 | Silver Pack | 2,500 | 50 | 12 | 1 | 3 | silver | yes |
| 4 | Premium Silver | 3,750 | 75 | 12 | 3 | 3 | silver | yes |
| 5 | Gold Pack | 5,000 | 100 | 12 | 1 | 3 | gold | yes |
| 6 | Premium Gold | 7,500 | 150 | 12 | 3 | 3 | gold | yes |
| 101 | Jumbo Premium Gold | 15,000 | 300 | 24 | 7 | 8 | gold | no (PROMO) |
| 102 | Premium Gold Players | 25,000 | 350 | 12 | 3 | 12 | gold | no |
| 103 | Mega Pack | 35,000 | 700 | 30 | 18 | 10 | gold | no |
| 104 | Rare Players Pack | 50,000 | 1,000 | 12 | 12 | 12 | gold | no |
| 105 | Jumbo Rare Players | 100,000 | 2,000 | 24 | 24 | 24 | gold | no |
| 106 | Silver Upgrade | 15,000 | 50 | 12 | 0 | 11 (+1 mgr) | silver | no |
| 107 | Gold Upgrade | 30,000 | 75 | 12 | 0 | 11 (+1 mgr) | gold | no |

Note: `managerSlots` is present in the JSON for 106/107 but `_generate_pack_contents_locked`
**does not read it** — those slots become consumables. Manager live emission is disabled
(`manager-catalog.v237.json: liveEmissionEnabled: false`).

### 4.2 Weights file format (`pack-weights.v237.json`)

| Key | Type | Meaning |
|---|---|---|
| `ratingBands` | `[{min,max,weight}]` | Gold-only base draw bands. Current: 75-79→74.5, 80-83→19.5, 84-86→4.7, 87-89→1.15, 90-99→0.15 |
| `rareSlotMultiplier` | `{"84-86":1.15, "87-89":1.3, "90+":1.55}` | Applied when the slot is a rare slot. Key is `"min-max"`, or `"90+"` when `min >= 90`. |
| `promoSlotMultiplier` | `{"84-86":1.2, "87-89":1.45, "90+":1.85}` | Applied when the pack is `regular: false` |
| `localTestStartingCoins` | int (100,000,000) | Seeded once on club creation |
| `seed` | str (`"FIFA14-LOCAL-FUT-V24012"`) | RNG seed prefix |
| `specialChancePerPack` | `{packType: prob}` | Per-pack special jackpot. 1→0.004 … 105→0.55, 106/107→0.0 |
| `secondSpecialChanceGivenFirst` | `{packType: prob}` | Conditional second special. 1-3→0, 4/5→0.005, 6→0.01, 101→0.015 … 105→0.04 |
| `maxSpecialsPerPack` | int (2) | Hard cap, further clamped to 0..2 in code |
| `legendChancePerPack` | `{packType: 0.0}` | **Force-zeroed in code regardless of the file** (`local_identity.py:2762-2765`) |
| `specialChancePerPlayer`, `promoSpecialChanceMultiplier`, `legendChancePerGoldPlayer`, `promoLegendChanceMultiplier` | float | Legacy from the old per-player roll; **no longer read** by `_generate_pack_contents_locked` |
| `regularPackMax90Plus` | int (1) | Elite (90+) cap for regular packs; promos use 2 |
| `specialTypeWeights` | `{quality: {family: weight}}` | Special family shares; see below |

`specialTypeWeights` currently:
- gold: `goldif 42, goldblue 28, motm 14, toty 8, green 6, special 2`
- silver: `silverif 70, silverblue 25, motm 4, green 1`
- bronze: `bronzeif 75, bronzeblue 24, motm 1`

### 4.3 Algorithm, step by step

1. Read `totalCards` (`count`), `rareCards` (clamped to `count`), `minQuality`,
   `promo = not regular`, `playerSlots` (clamped 0..count).
2. **Deterministic RNG**: `random.Random(f"{seed}:{pack_id}:{packType}")`. The same
   `pack_id` always yields the same pack.
3. `rare_indices = rng.sample(range(count), rare_count)` — **rares apply to the entire
   pack, not just player slots**.
4. `slot_kinds = ["player"]*playerSlots + ["consumable"]*(count-playerSlots)`, then `rng.shuffle`.
5. **Jackpot roll** (`local_identity.py:2755-2788`), once per pack, not per player:
   - Legend: forced off.
   - `if rng.random() < specialChancePerPack[packType]` → `ensure_rare_player_slot()`.
     If no player slot already carries a rare marker, one rare marker is **moved** from a
     consumable slot onto a player slot, preserving the advertised rare count.
   - If a first special landed and `rng.random() < secondSpecialChanceGivenFirst[packType]`,
     pick a second player slot; it is only admitted if it can carry a rare marker without
     increasing the pack's advertised rare total.
6. **Elite cap.** `max_elites = max(1, regularPackMax90Plus)` for regular packs, `2` for
   promos. Base draws pass `max_rating=89` once `elite_count >= base_elite_limit`, where
   `base_elite_limit` is reduced by one while a special/legend jackpot slot is still pending.
7. For each slot, `item_id = 180_000_000_000 + pack_id*100 + ordinal + 1` (`PACK_ITEM_BASE`).
8. **Player slots**:
   - `_weighted_special_player` (`local_identity.py:2264`) if this slot is a special target:
     pick a **card family** by `specialTypeWeights`, then a card inside the family with a
     rating weight of `1.00 (≤79) / 0.80 (≤83) / 0.60 (≤86) / 0.38 (≤89) / 0.18 (≤92) / 0.09`.
   - Otherwise `_weighted_player` (`local_identity.py:2199`): filter `PLAYER_CATALOG` by
     quality and `max_rating`, exclude already-used assets, then filter to `rareFlag > 0`
     for a rare slot / `rareFlag == 0` otherwise (falls back if the filter empties the pool).
     For **non-gold qualities it is a plain `rng.choice`** — bands only apply to gold.
   - De-duplication: retry `_weighted_player` up to 20 times if the resourceId is already
     used in this pack.
   - `_local_pack_player_payload` (`local_identity.py:2337`) sets
     `untradeable:false, tradeable:true, contract:7, fitness:99, morale:99, formation:"f442",
     pile:6, discardValue:<computed>, localPackSchema` and raises `RuntimeError` if the
     drawn card's quality does not match the pack tier.
9. **Consumable slots**: `_weighted_consumable` (`local_identity.py:2390`) filters
   `CONSUMABLE_CATALOG` by `quality` and `packEligible`, then by rare/common, then picks a
   **category** by weight and a card inside it.
   - Gold category weights: `Contract 27, Fitness 18, Healing 17, Training 8, GK Training 8,
     Positioning 10, Chemistry Style 9, Manager League 3`
   - Non-gold: `Contract 30, Fitness 22, Healing 22, Training 13, GK Training 13`
10. **Reveal ordering** (`reveal_key`, `local_identity.py:2845`): players first, then rating
    desc, then rareflag desc, then item id. The highest-rated player becomes the hero card.
11. Persist to `pack_contents` in reveal order.

### 4.4 Discard value

`_player_discard_value` (`local_identity.py:2184`) — `round(rating * factor)`:

| Card | gold | silver | bronze |
|---|---:|---:|---:|
| special (`rareFlag > 1` or `specialCard`) | 122.0 | 70.0 | 20.0 |
| rare (`rareFlag > 0`) | 8.0 | 3.5 | 0.75 |
| common | 4.0 | 1.5 | 0.30 |

Quality bands: bronze ≤64, silver 65–74, gold ≥75 (`_quality_for_rating`, `local_identity.py:2180`).

### Gotchas — packs

- **Only one unopened pack at a time.** `purchase_pack` raises if any `pack_contents` rows
  remain for an unopened pack: older builds returned 24+ flattened items and hung the reveal
  animation (`local_identity.py:3154-3163`).
- **Specials are a per-pack jackpot, not a per-player roll.** The old per-player
  implementation made a Premium Gold special ~0.56% per pack even though the configured
  gold value looked like 0.75% per player (`local_identity.py:2730-2734`).
- **Legends are hard-disabled on PC** regardless of the weights file, until Legend client
  identity/art rendering is proven (`local_identity.py:2762-2765`).
- Pack contents are **generated at purchase time and stored**, then deleted item-by-item as
  the client resolves each card. `_finish_pack_if_resolved_locked` marks `unopened = 0` only
  when every remaining content row also exists in `items` (`local_identity.py:3247`).
- EA never published FIFA 14 per-rating odds — the bands are explicitly tunable
  approximations, not historical probabilities (`local_identity.py:2204-2209`).

---

## 5. Transfer market model

### 5.1 Two supplies

1. **Synthetic bot listings** — computed on the fly from `MARKET_PLAYER_CATALOG`
   (= `PLAYER_CATALOG` + `NORMAL_SPECIAL_PLAYER_CATALOG`; World Cup and Legend card types
   excluded, `local_identity.py:55-73`). **Never stored in the DB.** Trade ids and prices
   are pure functions of resourceId, copy index and the clock.
2. **User listings** — rows in `market_listings`, sold to lazy bot buyers by `_market_tick_locked`.

Copies per bot card, `_market_listing_copies_for_card` (`local_identity.py:82`):

| Condition | Copies |
|---|---:|
| special and rating ≥ 90 | 3 |
| special otherwise | 4 |
| rating ≤ 64 | 3 |
| 65–74 | 4 |
| 75–82 | 5 |
| 83–87 | 6 |
| 88+ | 7 |

### 5.2 ID spaces (`local_identity.py:74-80`)

```
MARKET_ITEM_ID_BASE           = 181_000_000_000   # synthetic auction item ids
MARKET_TRADE_ID_BASE          = 1_900_000_000     # bot trade ids
USER_TRADE_ID_BASE            = 2_000_000_000     # user listing trade ids
MARKET_MAX_COPIES             = 8                 # stride per card in both spaces
TRANSFER_LIST_CAPACITY        = 30
MARKET_SYNTHETIC_RELIST_SECONDS = 900             # 15 min
MARKET_SELL_TAX_RATE          = 0.05              # 5% EA tax
```
`bot trade_id = 1_900_000_000 + catalogue_index*8 + copy_index`, invertible via
`_market_from_trade_id` (`local_identity.py:4074`).
`synthetic item_id = 181_000_000_000 + catalogue_index*8 + copy_index`.
User trade ids are `max(USER_TRADE_ID_BASE, max(existing)) + 1`.

Other item-id bases (`local_identity.py:126-131`):
`FULL_CLUB_ITEM_BASE 171e9`, `FULL_SPECIAL_ITEM_BASE 172e9`, `FULL_LEGEND_ITEM_BASE 173e9`,
`PACK_ITEM_BASE 180e9`, `LEGACY_INTRO_ITEM_BASE 170e9`.

### 5.3 Price model

**Reference value** — `_market_price_for` (`local_identity.py:3973`). Rating anchor table
(deliberately old-era):

```
64:400  65:500  70:900  74:1500  75:1600  76:2200  77:3000  78:4500  79:6500
80:9000 81:13000 82:20000 83:30000 84:45000 85:70000 86:110000 87:175000
88:275000 89:425000 90:650000 91:850000 92:1000000 93:1250000 94:1500000
95:1800000 96:2200000 97:2700000 98:3300000 99:4000000
```
Rating ≤40 → 150; 41–63 → `150 + (rating-40)*10`; ≥64 → nearest anchor at or below.

Multipliers: common (`rareFlag == 0`) ×0.82. Special/`rareFlag > 1` by `cardType`:
`goldif/silverif/bronzeif 1.35, motm 1.60, green 1.55, goldblue 1.80,
silverblue/bronzeblue 1.70, toty 2.25, special 1.50`, unknown 1.45.

Two hand-calibrated overrides: assetId `20801` v1 (Ronaldo NIF 92) → 1,200,000;
assetId `158023` v1 (Messi NIF 94) → 1,550,000.

Deterministic per-card jitter: `0.94 + ((resource*1103515245 + 12345) & 0xFFFF)/65535 * 0.12`
(±6%).

**Rounding** — `_market_round_price` (`local_identity.py:3967`): floor 150, step 50 (<1k),
100 (<10k), 250 (<50k), 500 (<100k), 1000 (≥100k).

**Current value** — `_market_current_value_for` (`local_identity.py:4026`):
```
pressure     = stored_pressure * 0.965^hours_since_update      # exponential decay
epoch        = now // 1800                                     # 30-minute snapshot
seed         = (resource % 997)/997 * 2π
global_wave  = 0.020 * sin(epoch/3)
card_wave    = 0.025 * sin(epoch/2 + seed)
multiplier   = clamp(1 + global_wave + card_wave + pressure, 0.72, 1.35)
value        = round_price(reference * multiplier)
```
30-minute snapshots keep prices stable while browsing but move over a session.

**Per-copy spread** — `_market_listing_price_for` (`local_identity.py:4046`), indexed by copy count:
```
3: (-0.040,  0.000, +0.045)
4: (-0.050, -0.015, +0.025, +0.065)
5: (-0.060, -0.030,  0.000, +0.032, +0.070)
6: (-0.065, -0.040, -0.015, +0.015, +0.045, +0.080)
7: (-0.070, -0.045, -0.020,  0.000, +0.025, +0.055, +0.090)
```
Starting bid is always `round_price(buyNow * 0.82)`.

**Duration** — `_market_listing_duration` (`local_identity.py:4062`) picks from
`(3600, 10800, 21600, 43200, 86400)` by `(resource + copy_index*3) % 5`.

**Trend pressure** — `_market_adjust_trend_locked` (`local_identity.py:4154`): clamped to
±0.18, decayed at `0.965^hours`. A user buy adds `+0.010`; a bot buying a user listing
adds `-0.006`.

### 5.4 Search filters the client sends

`market_search` (`local_identity.py:4252`). Reads `parse_qs` output; `_market_first` /
`_market_int` accept several aliases each:

| Alias(es) | Meaning | Notes |
|---|---|---|
| `type` | item type | anything other than `""`/`player`/`1` returns an empty auction list |
| `definitionId`, `maskedDefId` | exact card | matched against `assetId` **or** `resourceId` |
| `lev`, `level` | quality | numeric map `1=bronze 2=silver 3=gold 4=special`, plus `sp`; `any`/`-1`/`10` = no filter |
| `pos`, `position` | preferred position | uppercase |
| `nat`, `nation` | nation id | `-1`/`0` = any |
| `leag`, `league` | league id | `-1`/`0` = any |
| `team`, `club` | team id | `-1`/`0` = any |
| `rare`, `rarity` | rarity | `1/true/rare`, `0/false/common`, `sp/special` |
| `micr`, `minBuyNow` | min buy-now | |
| `macr`, `maxBuyNow` | max buy-now | |
| `minb`, `minBid` | min starting bid | |
| `maxb`, `maxBid` | max starting bid | |
| `start`, `offset`, `skip` | page offset | ≥0 |
| `num`, `count` | page size | default 20, clamped 1..100 |

Result order: rating desc, price asc, name asc, copy index asc.

### 5.5 Bid vs Buy Now

`market_bid(trade_id, amount)` (`local_identity.py:4386`) handles both `/bid` and `/offer`:

1. Decode `trade_id`; not a bot listing → `{reason:"INVALID_REQUEST", tradeId}`.
2. Recently sold (within 15 min) → `{reason:"AUCTION_EXPIRED", tradeId}`.
3. `amount <= 0` is treated as a Buy Now (`amount = buy_now`).
4. `amount > coins` → `{reason:"INSUFFICIENT_COINS", tradeId, credits, totalCredits, coins}`.
5. `won = amount >= buy_now`. If won:
   - **Duplicate guard**: if any owned player outside `trade`/`pending` piles has the same
     resourceId → `{reason:"Duplicate Item Type", errorCode:472, duplicateItemId, tradeId, credits, ...}`.
   - Deduct `buy_now` (not `amount`), mint a new item at
     `max(item_id, PACK_ITEM_BASE)+1` with `pile='pending'`, `itemState:"new"`,
     `lastSalePrice = buy_now`, `contract:7`, `fitness:99`.
   - Record `market_synthetic_sales` and add `+0.010` trend pressure.
6. Return the auction with `{currentBid: amount, offers: 0 if won else 1,
   bidState:"highest", tradeState:"closed" if won else "active", credits/totalCredits/coins}`.
   On a win, `itemData` is the newly minted card.

**There is no real bidding.** A sub-buy-now amount just returns `offers:1` and leaves the
listing active; nothing is escrowed and no timer runs.

### 5.6 Listing (selling)

`list_for_sale(document)` (`local_identity.py:4449`):
- Item id from `itemData[0].id|itemId` or root `itemId|id`; missing → `ValueError`.
- `startingBid` floor 150; `buyNowPrice` floor `max(startingBid, 200, startingBid*2)`;
  `duration` floor 60, default 3600.
- Runs a market tick first, then rejects with `"transfer list full"` at
  `TRANSFER_LIST_CAPACITY = 30` active listings.
- Rejects untradeable items.
- Sets item `pile=5`, `itemState="forSale"`, DB `pile='trade'`, `tradeable=1`.
- Computes `auto_sell_after` from the ask vs market value (see below) and stores
  `market_value_at_list`.
- Returns the auction plus `marketValue` and `cheapestMarketPrice`.

### 5.7 Bot demand for user listings

`_market_tick_locked` (`local_identity.py:4176`) runs lazily on every `market_search`,
`market_status`, `trade_pile`, `list_for_sale` and `hub_data` call.

For each `active` user listing:
- Skip if `ask > round_price(current_value * 1.10)` — over-priced cards never sell.
- Delay bands (seconds, from listing creation):

| Ask relative to market | base delay | random span |
|---|---:|---:|
| ≤ cheapest bot listing | 18 | 28 |
| ≤ current market value | 40 | 55 |
| ≤ 110% of value | 75 | 100 |
| > 110% | never sells | — |

- The jitter is deterministic: `(trade_id*1103515245 + resource*12345) & 0x7FFFFFFF % span`.
- On sale: credit `round(sold_price * 0.95)` (5% tax), set `trade_state='closed'`,
  `sold_price`, `sold_at`, snapshot `item_payload` with `pile=5, itemState="sold",
  lastSalePrice=sold_price`, keep the item at DB `pile='trade'`, apply `-0.006` trend.

`trade_pile()` (`local_identity.py:4522`) returns:
```
{auctionInfo:[Auction...], duplicateItemIdList:[], total:int,
 selling:int, sold:int, available:int, unlisted:int,
 tradePileCount, tradePileItems, transferListCount, activeCount, soldCount,
 credits, totalCredits, coins}
```
Cards sitting at `items.pile='trade'` with **no** `market_listings` row are also rendered,
with `tradeId: 0` and `tradeState: "inactive"` (the native no-auction sentinel) and all
price fields zeroed.

### 5.8 The "piles" concept

| Wire `pile` | DB `items.pile` | Meaning |
|---:|---|---|
| 0 | (n/a) | synthetic market card, not owned |
| 5 | `trade` | Transfer List (listed or merely moved there) |
| 6 | `pending` / pack contents | Purchased / New Items — unresolved pack pulls and market wins |
| 7 | `club` or `squad` | My Club (squad members carry DB pile `squad`) |

### Gotchas — market

- **A card can sit on the Transfer List without an auction.** `move_items` writes
  `items.pile='trade'`; an auction row appears only after "List on Market". Older builds
  rendered only `market_listings`, so moving a card to pile 5 made it vanish from both
  My Club and the Transfer List (`local_identity.py:4574-4578`).
- **My Club cannot hold two cards with the same resourceId; the transfer pile can.**
  That is how a duplicate pack pull escapes New Items (`local_identity.py:3321-3323`).
  Error code for a duplicate is `472`.
- **Clearing Sold deletes the item; withdrawing an active listing returns it to the club.**
  A sold card is never resurrected (`local_identity.py:4641-4650`).
- Bot buyers charge `buy_now`, not the submitted `amount`.

---

## 6. Squad + chemistry

### 6.1 Storage

- `squads` holds metadata; `squad_players` holds **exactly 23 rows** keyed
  `(squad_id, slot_index)`, slot 0..22. `item_id = 0` marks an empty slot.
- `squad_players` duplicates a denormalised copy of the card (`asset_id`, `resource_id`,
  `team_id`, `rating`, `rare_flag`, `play_style`, `preferred_position`, `attributes_json`)
  kept in sync by `_write_squad_slot_locked` (`local_identity.py:1886`) and
  `_save_player_payload_locked` (`local_identity.py:2491`).
- `fut_users.active_squad_id` points at the active squad; `squads.active` is also flipped.

### 6.2 Formation and position indices

`formation` is stored as a free-form retail token string, default `"f442"`. The validated
token set (from the Icebreaker fixture validator, `probe.py:87-93`) is:
```
f3412 f3421 f343 f352 f41212 f4231 f4222 f4312 f4321 f433 f4411 f442 f451
f5212 f5221 f532 f541 f41212a f4141 f4231a f433a f433b f433c f433d f442a f451a
```

Slot→position mapping is a **fixed 4-4-2 table**, `_v27_positions` (`local_identity.py:1612`):
```
index  0   1   2   3   4   5   6   7   8   9   10
       ST  ST  LM  CM  CM  RM  LB  CB  CB  RB  GK
index 11..17 = SUB          index 18..22 = RES
```
This table does **not** vary by formation — a known simplification. `_slot_position(i)`
returns `RES` for out-of-range indices.

The BETA starter-squad requirement list (`beta_identity.py:477`) uses the same 4-4-2
ordering for slots 0-10 and then `CB CB LB RB CM LM RM ST CB CM RW ST` for 11-22.

### 6.3 Chemistry — computed client-side

**The server never computes chemistry or star rating.** `save_squad`
(`local_identity.py:4659`) reads `chemistry` (clamped 0..100) and
`starRating`/`rating` (clamped 0..100) straight from the client's PUT body and stores them.
`squad_list` echoes them back. Nothing in `local_identity.py` or `beta_identity.py`
derives chemistry from position/league/nation/loyalty.

The server's contribution to chemistry is only supplying correct **inputs** in ItemData:
`teamid`, `leagueId`, `nation`, `playStyle`, `loyaltyBonus`, `preferredPosition`.

### 6.4 save_squad rules

1. `players` absent entirely → treated as the tournament captain/kicktakers PUT; returns the
   current squad list unchanged.
2. `players` not a list → `ValueError` → HTTP 400.
3. Each entry: `{index:0..22, itemData:{id|itemId|assetId}, kitNumber:0..99}`.
   `_resolve_item_locked` (`local_identity.py:1985`) resolves by `item_id` first, then by
   `asset_id` as a fallback.
4. **Sparse-write guard**: if the stored squad already has ≥11 filled slots and the incoming
   write recognises fewer than `MIN_RECOGNIZED_SQUAD_PLAYERS = 7`, the write is treated as a
   refresh acknowledgement — only the active flag is reaffirmed, and players, chemistry,
   rating and name are all preserved.
5. Otherwise: deactivate other squads, update metadata, delete and rewrite all 23 slots,
   set `fut_users.active_squad_id`, and re-canonicalise every owned player, moving in-squad
   cards to DB pile `squad` and the rest to `club`.

`_repair_active_squad_locked` (`local_identity.py:1942`) auto-fills a squad from owned
players when the active squad has <11 filled slots but ≥11 owned players exist.

### Gotchas — squad

- **FIFA can PUT a nearly-empty squad while its frontend is still resolving ItemData.**
  The BETA 2.20 capture showed the first write after loading the squad contained only the
  goalkeeper even though 22/23 slots were valid in the DB. Without the sparse-write guard
  that destroys the squad (`local_identity.py:4660-4667`).
- The retail SquadDetails record carries **both** `rating` and `starRating`, plus
  `valid`, `newsquad`, `kicktakers`, `tactics`. Omitting them made an old frontend
  manufacture a partial default squad before parsing all 23 ItemData records
  (`local_identity.py:4844-4847`).
- Empty slots must still be present as `{"index":n, "itemData":{"id":0}, "kitNumber":0}`.

---

## 7. Blaze / FIRE protocol layer

### 7.1 FIRE frame

12-byte big-endian header (`parse_fire_header`, `probe.py:1440`):

| Offset | Size | Field |
|---:|---:|---|
| 0 | 2 | body length |
| 2 | 2 | component |
| 4 | 2 | command |
| 6 | 2 | error |
| 8 | 1 | `type_options` — frame type is the **high nibble** (`>> 4`) |
| 9 | 1 | `options` — high nibble |
| 10 | 2 | sequence |

Frame types used (`probe.py:1456-1474`):

| Builder | type byte | Meaning |
|---|---|---|
| `build_fire_response` | `0x10` (type 1) | RPC reply, error = 0 |
| `build_fire_error_response` | `0x30` (type 3) | RPC error reply, error preserved, sequence preserved |
| `build_fire_notification` | `0x20` (type 2) | Async notification, sequence 0 |

Read loop: `recv_fire_frame` (`probe.py:1489`) reads exactly 12 bytes then exactly `length`.

### 7.2 TDF encoding

Type constants (`probe.py:325-334`):
```
0x0 VAR_INT   0x1 STRING   0x2 BLOB      0x3 GROUP  0x4 LIST
0x5 MAP       0x6 TAGGED_UNION          0x7 VAR_INT_LIST
0x8 OBJECT_TYPE  0x9 OBJECT_ID
```

`tdf_tag(tag, value_type)` (`probe.py:471`) — 4-byte header: 3 packed tag bytes + 1 type
byte. The packing mirrors the `tdf` 0.4.0 crate's `Tagged::serialize_raw` used by
PocketRelay. Tags are ASCII, ≤4 chars. `decode_tdf_tag` (`probe.py:533`) reverses it:
each of 4 six-bit fields + 0x20, right-stripped.

`tdf_varint(value)` (`probe.py:500`) — **first byte carries 6 bits** (`value & 0x3F`) with
continuation bit `0x80`; subsequent bytes carry 7 bits. Negative values are rejected.
`read_tdf_varint` (`probe.py:515`) is the inverse.

Value encodings:
- **String**: varint length (including terminator) + UTF-8 bytes + `\x00`. A string
  whose last byte is not zero is rejected on decode.
- **Blob**: varint length + raw bytes.
- **Group**: fields terminated by a `\x00` byte.
- **List**: 1 element-type byte + varint count + values.
- **Map**: key-type byte + value-type byte + varint count + alternating pairs.
- **Tagged union**: 1 discriminator byte; `0x7F` means "no active member", otherwise
  exactly one tagged field follows.

Helper builders (`probe.py:693-782`): `tdf_u32`, `tdf_u16`, `tdf_bool`, `tdf_raw_string`,
`tdf_string`, `tdf_group`, `tdf_list_u32`, `tdf_list_strings`, `tdf_list_groups`,
`tdf_map_strings`, `tdf_empty_map`, `tdf_blob`, `tdf_empty_list`, `tdf_empty_varint_list`,
`tdf_map_u32`.
Extractors (`probe.py:784-845`): `extract_tdf_string`, `extract_tdf_string_list`,
`extract_tdf_u32`, `extract_tdf_varint_last`.
Full decoder: `decode_tdf_document` (`probe.py:635`).

### 7.3 Components implemented

Constants at `probe.py:343-358`:

| Const | Value |
|---|---:|
| `AUTHENTICATION_COMPONENT` | 1 |
| `STATS_COMPONENT` | 7 |
| `CENSUS_COMPONENT` | 10 |
| `CLUBS_COMPONENT` | 11 |
| `MESSAGING_COMPONENT` | 15 |
| `ROOMS_COMPONENT` | 21 |
| `ASSOCIATION_LISTS_COMPONENT` | 25 |
| `GAME_REPORTING_COMPONENT` | 28 |
| `GAME_REPORTING_RESULT_NOTIFICATION` | 114 (command) |
| `SPONSORED_EVENTS_COMPONENT` | 0x081C (2076) |
| `EASFC_COMPONENT` | 0x081D (2077) |
| `CARDHOUSE_COMPONENT` | 2148 |
| `OSDK_SETTINGS_COMPONENT` | 2249 |
| `OSDK_ONLINE_PASS_COMPONENT` | 2268 |
| Util component | 9 (no named constant) |
| Redirector component | 5 (no named constant) |
| UserSessions component | 0x7802 (30722) |

### 7.4 Command table

Handled directly in `BlazeProbe.handle` (`probe.py:1608`):

| Component | Command | Name | Response |
|---:|---:|---|---|
| 5 | 1 | Redirector.GetServerInstance | `build_redirector_body` — `ADDR` tagged union → `VALU{IP,PORT}`, `SECU:false`, `XDNS:false` (**TLS listener only**) |
| 1 | 0x98 (152) | Authentication.OriginLogin | `build_origin_login_body`; token logged as `redacted-origin-auth-token` |
| 1 | 0x46 (70) | Authentication.Logout | empty success |
| 1 | 0x1D (29), 0x20 (32) | Authentication.ListEntitlements | `build_entitlements_body`; filters on request `GNLS` |
| 9 | 1 | Util.FetchClientConfig | `build_fetch_config_body(CFID)` |
| 9 | 2 | Util.Ping | `STIM` = current epoch |
| 9 | 7 | Util.PreAuth | `build_pre_auth_body`; captures the client `LOC` locale |
| 9 | 8 | Util.PostAuth | `build_post_auth_body` |

Handled by `build_shared_blaze_bootstrap_response` (`probe.py:1317`):

| Component | Command | Trace name | Body |
|---:|---:|---|---|
| 7 (Stats) | 3 | `stats-groups-empty` | empty `GRPS` group list |
| 7 | 15 | `stats-key-scopes-empty` | empty `KSIT` string→group map |
| 7 | 20 | `stats-period-ids` | `build_stats_period_ids_body` |
| 10 (Census) | 1 | `census-subscribe` | empty |
| 11 (Clubs) | 1600 | `clubs-invitations-empty` | empty `CIST` group list |
| 11 | 2600 | `clubs-component-settings` | `build_clubs_component_settings_body` |
| 15 (Messaging) | 2 | `messaging-fetch-count` | `MCNT = 0` |
| 15 | 5 | `messaging-get-empty` | empty |
| 21 (Rooms) | 10 | `rooms-select-view-updates` | empty |
| 25 (AssocLists) | 6 | `association-lists-empty` | empty `LMAP` group list |
| 28 (GameReporting) | 2 | `game-reporting-submit-offline-success` | empty; **triggers async notification 114** |
| 0x081C | 3 | `sponsored-events-local-url` | `URL = http://127.0.0.1:8080/sponsored-events` |
| 0x081D (EASFC) | 1,2,3,4 | `easfc-purchase-game-{win,match,loss,draw}-success` | empty; also feeds `record_easfc_signal(command)` |
| 2148 (CardHouse) | 101 | `cardhouse-new-player-login` | `build_cardhouse_login_body` |
| 2148 | 104 | `cardhouse-no-player-info` | empty body, **error = 1** (full Blaze error `0x00010864`; the FIRE header carries the component separately) |
| 2148 | 102,103,106,301,709 | `cardhouse-empty-success` | empty |
| 2249 (OSDK Settings) | 1 | `osdk-settings` | `build_osdk_settings_body` |
| 2249 | 2 | `osdk-setting-groups` | `build_osdk_setting_groups_body` |
| 2268 (OSDK Online Pass) | 3 | `osdk-online-pass-gates-empty` | empty `LIST` group list |
| 0x7802 (UserSessions) | 8, 20 | `user-sessions-update-ack` | empty |

Everything else → empty success (`empty-success-observation`), so new routes stay visible
in the trace rather than being guessed.

### 7.5 Notifications the server pushes

| Component | Command | Builder | When |
|---:|---:|---|---|
| 0x7802 | 8 | `build_user_authenticated_body` | after OriginLogin success, or first PostAuth |
| 0x7802 | 2 | `build_user_added_body(legacy=True)` | same, 50 ms later |
| 0x7802 | 1 | `build_user_extended_data_body` | same, 50 ms later |
| 28 | 114 | `build_game_reporting_result_notification_body(GRID)` | 20 ms after GameReporting cmd 2 succeeds; body = `EROR:0, FNL:true, GHID, GRID` |

### 7.6 Redirector flow

1. FIFA connects to the redirector port (default **42127**), normally over TLS to
   `gosredirector.ea.com`.
2. `--redirector-mode tcp` → `TcpProbe`: capture only, no reply.
   `--redirector-mode tls` → `TlsTcpProbe` (`probe.py:1942`), which wraps the socket with a
   locally generated cert for `--cert-hostname`.
3. With `--redirector-reply local`, `TlsTcpProbe` reads one FIRE frame; if
   `component == 5 and command == 1`, it replies with `build_redirector_body(main_blaze_host,
   main_blaze_port)` and closes.
4. FIFA then connects to the main Blaze port (default **42128**) where `BlazeProbe` serves
   plaintext FIRE.

Certificate support: `create_ca_files` / `create_cert_files` (sha256, `probe.py:4584`/`4643`),
`create_sha1_cert_files` (`probe.py:4493`), and `create_old_protossl_cert_files`
(`probe.py:4401`) for legacy ProtoSSL. A fresh `SSLContext` is built per connection when
`--cert-dir` is set, to stop legacy session resumption reusing the deliberately malformed
compatibility certificate (`probe.py:4854-4860`).

### 7.7 Login / auth sequence

Observed order (from the handler structure and comments):

1. **Redirector** — component 5 / command 1 over TLS → address of the main Blaze server.
2. **Util.PreAuth** (9/7) — client sends `LOC`; server returns `ANON, ASRC, CIDS
   (advertised component ids), CNGN, CONF{pingPeriod=30s, voipHeadsetUpdateRate=1000,
   xlspConnectionIdleTimeout=300}, INST="fifa-2014-pc", MINR, NASP="cem_ea_id", PILD, PLAT="pc",
   PTAG, QOSS{BWPS{PSA,PSP,SNA}, LNP=1, LTPS, SVID}, RSRC, SVER`.
3. **Util.FetchClientConfig** (9/1) × 5, one per `CFID`: `OSDK_CORE`, `OSDK_CLIENT`,
   `OSDK_NUCLEUS`, `OSDK_WEBOFFER`, `OSDK_XMS_ABUSE_REPORTING` (`probe.py:376-470`).
   `OSDK_CLIENT` is where the FUT HTTP base URLs live:
   `FUT_RS4_BASE_URL`, `FUT_URI`, `FUT_RS4_APIURL_PC`, `FUT_RS4_URL_PC`,
   `FUT/MODULE_BASEURL_PC`, `FUT/SINGLE_BASEURL_PC` all → `http://127.0.0.1:8099/`;
   `FUTBOOTCFGFILE_URL` → `http://127.0.0.1:8080/futBoot.xml`;
   `FUTDYNAMICMESSAGES_URL_BASE` → `http://127.0.0.1:8099` (**no trailing slash** — the
   client appends `/fut/` itself).
   When the identity store reports a club, five keys are rewritten for the returning-user
   path: `FUT/FORCE_TUTORIALS=0`, `FUT/DISABLE_TUTORIALS=1`,
   `FUT/ALWAYS_SHOW_SMART_TUTORIALS=0`, `FUT/IS_RETURNING_USER=1`,
   `FUT_SKIP_ICEBREAKER_FLOW=1` (`probe.py:949-957`).
4. **Authentication.OriginLogin** (1/0x98) — server waits `--origin-login-delay-ms`
   (default 100 ms) before replying, so a loopback reply cannot outrun the legacy client's
   job registration.
5. After OriginLogin success, wait `--login-notification-delay-ms` (default 1500 ms), then
   push UserAuthenticated (0x7802/8), UserAdded (0x7802/2), UserExtendedData (0x7802/1),
   50 ms apart.
6. **Authentication.ListEntitlements** (1/0x1D or 1/0x20) — returns tag
   `FIFA14PCFUTContentUnlocks` inside each requested group name (default group
   `FIFA14PCBoxContent`).
7. **Util.PostAuth** (9/8) — telemetry / ticker / player-sync targets, all loopback.
8. CardHouse (2148/101 …), OSDK settings, stats, messaging, etc.
9. During gameplay: EASFC 0x081D/1-4; at match end GameReporting 28/2 + notification 114.

Session limit: `BlazeProbe.handle` serves at most **64 requests** per connection, with a
300-second socket timeout (`probe.py:1611`, `probe.py:1616`).

### Gotchas — Blaze

- **ListEntitlements filters by `GNLS` (group names), not `ETAG`.** A grant whose `GNAM`
  does not match the filter decodes fine but is discarded by the generated client callback,
  leaving the front end spinning with only keepalive pings (`probe.py:1329-1341`).
- **`TAG` and `GNLS` are not interchangeable.** Returning the group name as the tag decodes
  successfully but does not authorise CardsDLL.
- **The 15-second Blaze socket timeout was itself producing the "server down" error** —
  raised to 300 s (`probe.py:1609-1611`).
- **Login notifications must be delayed.** FIFA's main-thread packet pump can otherwise see
  all three frames in one TLS read before its UserSessions listeners are armed
  (`probe.py:1870-1878`).
- **GameReporting needs the async notification.** Returning only RPC success leaves the
  client's reporting job waiting for `ResultNotification`, and it later tears down the
  online session (`probe.py:1822-1826`).
- **CardHouse 104 must return an error, not success** (local ordinal 1 in component 0x0864;
  the FIRE header carries the component separately so the 16-bit error field is `1`).
- `FUTDYNAMICMESSAGES_URL_BASE` must have **no trailing slash**; absent, CardsDLL falls back
  to a dead fifa13 test host on TCP 8306 (`probe.py:~430`).

---

## 8. Startup / CLI contract

`main()` at `probe.py:4749`. All flags:

| Flag | Type / choices | Default | Effect |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | Bind address for every listener |
| `--instance-token` | str | `""` | Per-launch ownership token; echoed by `/__fifa14_local_fut_health` |
| `--blaze-port` | int | `42127` | Redirector listener |
| `--main-blaze-port` | int | `42128` | Main Blaze application server; also the address handed out by the redirector |
| `--http-port` | int | `8080` | `bootstrap-http` listener (serves `/futBoot.xml`) |
| `--fut-http-port` | int | `8099` | `fut-http` listener — the whole FUT REST surface |
| `--dynamic-http-port` | int | `0` (off) | Optional `dynamic-http` fallback listener for the hard-coded FUT dynamic-messages service (observed on TCP 8306) |
| `--fut-account-mode` | `new` \| `existing` | `new` | First-use persona vs pre-created local test club; also the identity store's `initial_mode` |
| `--identity-db` | path | `<repo>/artifacts/local-fut.sqlite3` | SQLite database path |
| `--beta-mode` | flag | off | Use `BetaIdentityStore` instead of `LocalIdentityStore`: bronze starter club, wallet ledger, match settlement, seasons/tournaments, cosmetics, metrics |
| `--enable-fut-direct-boot-config` | flag | off | A/B test: append `LoadFUTSkipBlaze=1, DirectBootFUT=1, FUT_DIRECT_BOOT=1, FUT_ENABLE_MENU=1` to `OSDK_CLIENT` |
| `--gosca-port` | int | `44125` | GOSCA HTTPS probe port |
| `--enable-gosca` | flag | off | Bind the GOSCA TLS listener (`gosca.ea.com` cert) |
| `--gosca-reply` | `xml` \| `unavailable` | `xml` | GOSCA reply mode; `unavailable` returns 503 |
| `--lsx-port` | int | `3216` | Origin Core/LSX port |
| `--enable-lsx-probe` | flag | off | Bind a capture-only LSX listener. Leave off when EA Desktop owns the port |
| `--redirector-mode` | `tcp` \| `tls` | `tcp` | `tcp` = capture-only `TcpProbe`; `tls` = `TlsTcpProbe` with a generated cert |
| `--redirector-reply` | `none` \| `local` | `none` | TLS mode only: `local` answers component 5/command 1 with the main Blaze address |
| `--cert-hostname` | str | `gosredirector.ea.com` | CN/SAN for the redirector certificate |
| `--cert-dir` | path | none (temp dir) | Persistent CA + cert directory. Setting it also enables per-connection SSLContext creation |
| `--cert-hash` | `old-protossl` \| `sha1` \| `sha256` | `sha256` | Certificate signature style for legacy ProtoSSL compatibility |
| `--origin-login-mode` | `success` \| `error` \| `error-once` | `success` | Controlled OriginLogin result, used to separate callback routing from response decoding |
| `--origin-login-error` | int (`int(value, 0)`, accepts `0x…`) | `0x000D` | Blaze error code used by the above |
| `--origin-first-login` | flag | off | Set `SessionInfo.FRST` without changing the default login fixture |
| `--origin-login-delay-ms` | int | `100` | Delay before the OriginLogin reply |
| `--login-notification-delay-ms` | int | `1500` | Delay before UserAuthenticated/UserAdded/UserExtendedData |

Startup emits one JSON line `{"kind":"started", ...}` on stdout containing build version,
pid, every port, `local_account_profile`, `local_account_snapshot`, and a `first_use_contract`
block (`returningUser`, `userClubList`, `syntheticClubSeeded`, `syntheticSquadSeeded`,
`starterPackClaimed`, `completedActions`).

All logging is line-delimited JSON to stdout via `emit(kind, **fields)` (`probe.py:38`).
`BaseHTTPRequestHandler.log_message` is suppressed.

### Environment variables (not CLI flags)

| Variable | Values | Effect |
|---|---|---|
| `FIFA14_TROPHY_ARCHIVE_MODE` | default `emptybig`; `miss`/`404`/`off`/`disabled` | Serve an empty BIGF container vs 404 for trophy `.big` archives |
| `FIFA14_SEASON_ITEM0_MODE` | default `empty200`; `miss`/`404`/`off`/`disabled` | Answer `/fut/items/pc/0.json` with `{}` 200 vs 404 |
| `FIFA14_STORE_ART_MODE` | `tier` (default), `survey`, `season-ticket` | Store offer `assetId` art selection. `tier` = 1/2/3 bronze/silver/gold |
| `FIFA14_TOURNAMENT_MODE` | `native` (default), `safe`/`empty`/`off`/`disabled` | Serve real tournament records vs empty arrays |

---

## Appendix — constants worth carrying over

```python
DEFAULT_NUCLEUS_ID   = 1_000_001
DEFAULT_PERSONA_ID   = 1_000_001
DEFAULT_PERSONA_NAME = "LocalFUT"
DEFAULT_SID          = "LOCAL-FIFA14-SID"
DEFAULT_PHISHING_TOKEN = "LOCAL-FIFA14-PHISHING"
DEFAULT_FUT_ACTIONS  = ("INTRO_DONE",)
FUT_ACTION_PATTERN   = r"^[A-Z0-9_]{1,64}$"

PLAYER_ITEM_TYPE          = "player"
PLAYER_STAT_COUNT         = 5
PLAYER_ATTRIBUTE_COUNT    = 6
MIN_RECOGNIZED_SQUAD_PLAYERS = 7

FULL_CLUB_ITEM_BASE    = 171_000_000_000
FULL_SPECIAL_ITEM_BASE = 172_000_000_000
FULL_LEGEND_ITEM_BASE  = 173_000_000_000
PACK_ITEM_BASE         = 180_000_000_000
MARKET_ITEM_ID_BASE    = 181_000_000_000
LEGACY_INTRO_ITEM_BASE = 170_000_000_000
MARKET_TRADE_ID_BASE   = 1_900_000_000
USER_TRADE_ID_BASE     = 2_000_000_000
MARKET_MAX_COPIES      = 8
TRANSFER_LIST_CAPACITY = 30
MARKET_SYNTHETIC_RELIST_SECONDS = 900
MARKET_SELL_TAX_RATE   = 0.05
```

## Unclear / unverified areas

These are stated as unknown in the source itself or could not be resolved by reading it:

- **Manager items.** `manager-catalog.v237.json` sets `liveEmissionEnabled: false` and
  `manager_reference.resource_id` is always NULL — manager resource IDs were never verified
  against the PC build. `managerSlots` in the pack catalogue is not consumed by the generator.
- **Legend cards.** Present in the catalogue and in `_weighted_legend`, but the pack chance
  is force-zeroed pending proof that Legend identity/art renders on PC.
- **`catalog_items` table.** Created but no code path inspected reads or writes it.
- **Formation-specific slot positions.** `_v27_positions()` is a fixed 4-4-2 table applied
  to every formation.
- **Real bidding.** Bids below buy-now are acknowledged but never escrowed, timed, or resolved.
- **Card art PNG/BIG assets.** Deliberately 404 — the old EA CDN is gone and nothing is
  fabricated.
- **`/fut/items/<platform>/<n>.json` for non-zero ids** remains a strict 404 until the real
  PC schema is captured.
- **Store offer `extPrice`** is omitted because it is a nested external-money structure
  whose native type is not known.
