"""FUT HTTP REST surface for FIFA 15.

The Ultimate Team client talks plain JSON over HTTP to whatever base URL the
Blaze client config handed it. Routes mirror FIFA 14's `/ut/game/fifa14/...`
tree with the game year swapped.

Two rules that come straight from the FIFA 14 project's scars:

  * Responses are compact JSON with a trailing newline. The client is strict.
  * An unmatched `/ut/*` route returns `200 {}` rather than 404, because a 404
    stalls the client's state machine. Every such request is logged so the real
    contract can be filled in from evidence.
"""
from __future__ import annotations

import json
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from identity import (
    DB_PILE_CLUB,
    DB_PILE_PENDING,
    DB_PILE_SQUAD,
    DB_PILE_TRADE,
    PACK_CATALOG,
    PACK_BY_ID,
    PILE_CLUB,
    PILE_PURCHASED,
    SQUAD_SIZE,
    IdentityStore,
)

GAME = "fifa15"
UT_PREFIX = f"/ut/game/{GAME}"
RESOURCE_YEAR = "2015"


def json_bytes(document: Any) -> bytes:
    return (json.dumps(document, separators=(",", ":")) + "\n").encode("utf-8")


class FutHttpHandler(BaseHTTPRequestHandler):
    store: IdentityStore
    market: Any
    trace: Any
    server_version = "LocalFUT/15"
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return  # routed through the trace log instead

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > 1_048_576:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {"_list": parsed}
        except (ValueError, UnicodeDecodeError):
            return {"_raw": raw.decode("utf-8", "replace")}

    def _send(self, document: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        payload = json_bytes(document)
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_xml(self, text: str, status: int = 200) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "text/xml; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        self._handle()

    do_POST = do_PUT = do_DELETE = do_HEAD = do_GET

    # -- dispatch --------------------------------------------------------

    def _handle(self) -> None:
        split = urlsplit(self.path)
        path = split.path
        query = {key: values[0] for key, values in parse_qs(split.query).items()}
        body = self._read_body() if self.command in ("POST", "PUT", "DELETE") else {}

        # FIFA tunnels DELETE and PUT through POST using this header.
        method = (self.headers.get("X-HTTP-Method-Override") or self.command).upper()

        try:
            handled = self.route(method, path, query, body)
        except Exception as exc:  # a handler bug must not kill the client session
            self.trace.emit("http-error", path=path, method=method, error=repr(exc))
            self._send({}, 200)
            return

        if not handled:
            self.trace.emit("http-unhandled", method=method, path=path, query=query, body=body)
            # 200 {} keeps the client's state machine moving.
            self._send({})
        else:
            self.trace.emit("http", method=method, path=path, query=query)

    def route(self, method: str, path: str, query: dict, body: dict) -> bool:
        store = self.store
        market = self.market
        lower = path.lower()

        # ---- health ----------------------------------------------------
        if path in ("/health", "/__localfut_health"):
            self._send({"ok": True, "game": GAME, "summary": store.summary()})
            return True

        # ---- auth / session --------------------------------------------
        if path == "/ut/auth":
            sid = "localfut-session"
            self._send(
                {
                    "sid": sid,
                    "serverTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "lastOnlineTime": "1970-01-01T00:00:00Z",
                },
                headers={"x-ut-sid": sid},
            )
            return True

        if path in ("/ut/delete/auth", f"{UT_PREFIX}", f"{UT_PREFIX}/", "/utStats"):
            self._send({})
            return True

        if path.startswith(f"{UT_PREFIX}/phishing"):
            if path.endswith("/validate"):
                self._send({"debug": "", "string": "OK", "code": "200", "reason": "", "token": "localfut"})
            elif path.endswith("/trusteddevice"):
                self._send({"trusted": True, "changed": False, "exists": True,
                            "locked": False, "deviceId": "localfut-device"})
            else:
                self._send({"debug": "", "token": "localfut"})
            return True

        if path == f"{UT_PREFIX}/settings":
            self._send({
                "maximumTradePileSize": 100,
                "getOperationTimeoutSec": 300,
                "clubCreateThreshold": 0,
                "fifaPointsCancelTransactionFix": 1,
                "tokenRedemptionEnabled": 0,
                "enableWorldCupMode": 0,
            })
            return True

        # ---- user / club -----------------------------------------------
        if path == f"{UT_PREFIX}/user/accountinfo":
            self._send({"userAccountInfo": {"personas": [self._persona()]}})
            return True

        if path == f"{UT_PREFIX}/user/credits":
            self._send(self._credits())
            return True

        if path == f"{UT_PREFIX}/user/list":
            self._send({"userInfo": [self._user_document()]})
            return True

        if path == f"{UT_PREFIX}/user/action":
            # Branches on key PRESENCE, so only completed actions may appear.
            actions: dict[str, bool] = {}
            if store.club_exists():
                actions["INTRO_DONE"] = True
            self._send(actions)
            return True

        if path.startswith(f"{UT_PREFIX}/user/action/"):
            self._send({})
            return True

        if path == f"{UT_PREFIX}/user/club" and method in ("POST", "PUT"):
            name = str(body.get("clubName") or "Local FUT Club")
            store.establish_club(name)
            self._send({"club": self._club_document()})
            return True

        if path == f"{UT_PREFIX}/user":
            if method in ("POST", "PUT") and body.get("clubName"):
                store.establish_club(str(body["clubName"]))
            self._send(self._user_document())
            return True

        if path == f"{UT_PREFIX}/hub":
            club_count = len(store.items(DB_PILE_CLUB)) + len(store.items(DB_PILE_SQUAD))
            trade = store.items(DB_PILE_TRADE)
            self._send({
                "auctionCount": 0,
                "clubPlayers": club_count,
                "tradePileCount": len(trade),
                "tradePileItems": len(trade),
                "transferListCount": len(trade),
                "selling": len(trade),
                "sold": 0,
            })
            return True

        if path == f"{UT_PREFIX}/userdata":
            self._send({"userData": []})
            return True

        # ---- club stats -------------------------------------------------
        if path.startswith(f"{UT_PREFIX}/club/stats"):
            self._send(self._club_stats())
            return True

        # ---- items / my club --------------------------------------------
        if path in (f"{UT_PREFIX}/club", f"{UT_PREFIX}/clubUser", f"{UT_PREFIX}/item"):
            if method in ("DELETE",):
                self._send(self._quick_sell(query, body))
                return True
            if method in ("POST", "PUT"):
                self._send(self._move_items(body))
                return True
            self._send(self._club_items(query))
            return True

        if path == f"/ut/delete/game/{GAME}/item":
            self._send(self._quick_sell(query, body))
            return True

        if path.startswith(f"{UT_PREFIX}/item"):
            if method in ("POST", "PUT"):
                self._send(self._move_items(body))
            elif method == "DELETE":
                self._send(self._quick_sell(query, body))
            else:
                self._send(self._club_items(query))
            return True

        # ---- squads -------------------------------------------------------
        if path.startswith(f"{UT_PREFIX}/squad"):
            self._send(self._squad_route(method, path, body))
            return True

        # ---- packs / store -------------------------------------------------
        if path == f"{UT_PREFIX}/purchased/items" and method in ("POST", "PUT"):
            self._send(self._purchase_pack(body))
            return True

        if path in (f"{UT_PREFIX}/purchased", f"{UT_PREFIX}/purchased/items"):
            self._send(self._purchased_items())
            return True

        if "/store" in lower and "transaction" not in lower:
            if method in ("POST", "PUT"):
                self._send(self._purchase_pack(body))
            elif "quantity" in lower:
                self._send({"packList": [{"packType": p.pack_id, "quantity": 0} for p in PACK_CATALOG]})
            else:
                self._send(self._store_pack_types())
            return True

        if "/store/transaction" in lower:
            if method in ("POST", "PUT"):
                state = str(body.get("state") or "NOTRANSACTION")
                if state == "TRANSACTIONCANCEL":
                    self._send({"state": state, "transactionId": int(body.get("transactionId") or 0)})
                    return True
                selector = self._pack_selector(body)
                if selector:
                    self._send(self._purchase_pack(body))
                    return True
                self._send({"state": state, "transactionId": int(body.get("transactionId") or 0)})
            else:
                self._send({"transactionId": 0, "state": "NOTRANSACTION"})
            return True

        # ---- transfer market ------------------------------------------------
        if path == f"{UT_PREFIX}/transfermarket":
            self._send(market.search(query))
            return True

        if lower in (f"{UT_PREFIX}/tradepile".lower(),):
            self._send(market.trade_pile())
            return True

        if lower in (f"{UT_PREFIX}/watchlist".lower(),):
            self._send({"auctionInfo": [], "duplicateItemIdList": [], "total": 0})
            return True

        if path in (f"{UT_PREFIX}/auctionhouse", f"{UT_PREFIX}/trade") and method == "POST":
            self._send(self._list_for_sale(body))
            return True

        if path.startswith(f"{UT_PREFIX}/auctionhouse/") or (
            path.startswith(f"{UT_PREFIX}/trade/") and method == "DELETE"
        ):
            trade_id = self._tail_int(path)
            self._send(market.remove_listing(trade_id))
            return True

        if path.startswith(f"/ut/delete/game/{GAME}/trade/"):
            trade_id = self._tail_int(path)
            self._send(market.remove_listing(trade_id))
            return True

        if path == f"{UT_PREFIX}/trade/status":
            self._send(market.trade_pile())
            return True

        if path.startswith(f"{UT_PREFIX}/trade/") and (path.endswith("/bid") or path.endswith("/offer")):
            if method in ("PUT", "POST"):
                self._send(self._bid(path, body))
            else:
                self._send(market.trade_pile())
            return True

        if path.startswith(f"{UT_PREFIX}/trade"):
            self._send({"auctionInfo": [], "duplicateItemIdList": [], "total": 0})
            return True

        # ---- client data / misc ---------------------------------------------
        if path == f"{UT_PREFIX}/clientdata/pileSize":
            self._send({"entries": [{"key": 2, "value": 20000},
                                    {"key": 3, "value": 20000},
                                    {"key": 4, "value": 20000}]})
            return True

        if path.startswith(f"{UT_PREFIX}/clientdata") or path in (
            f"{UT_PREFIX}/activeMessage", f"{UT_PREFIX}/leaderboards",
            f"{UT_PREFIX}/managerquest", f"{UT_PREFIX}/userHubData",
        ):
            self._send({})
            return True

        if path == f"{UT_PREFIX}/season/user":
            self._send({"seasonId": 1, "divisionId": 10, "round": 1})
            return True

        if path.startswith(f"{UT_PREFIX}/season"):
            self._send({"seasons": []})
            return True

        if path.startswith(f"{UT_PREFIX}/tournament"):
            if path.endswith("/teams"):
                self._send({"teamId": []})
            elif path.endswith("/user/list"):
                self._send({"tournamentId": []})
            else:
                self._send({"tournament": []})
            return True

        if path.startswith(f"{UT_PREFIX}/match"):
            self._send(self._match(method, path, body))
            return True

        # ---- static / CDN replacements ---------------------------------------
        if lower.endswith(".xml") or "/fut/loc/" in lower or lower.startswith("/fut"):
            self._send_xml("<MESSAGES></MESSAGES>")
            return True

        if "/fut/items/images/" in lower:
            # Never fabricate card art; the client falls back cleanly on 404.
            self._send({}, status=404)
            return True

        # ---- catch-all for the UT tree ---------------------------------------
        if path.startswith("/ut/"):
            return False  # logged as unhandled, answered with 200 {}

        self._send({"error": "local FUT server"}, status=404)
        return True

    # -- helpers ---------------------------------------------------------

    def _tail_int(self, path: str) -> int:
        for part in reversed(path.strip("/").split("/")):
            if part.isdigit():
                return int(part)
        return 0

    def _persona(self) -> dict[str, Any]:
        club = self.store.club()
        return {
            "personaId": self.store.persona_id,
            "personaName": club.get("name") or "LocalFUT",
            "returningUser": 1 if club.get("established") else 0,
            "onlineAccess": True,
            "trial": False,
            "userState": None,
            "userClubList": [self._club_document()],
            "trialFree": False,
        }

    def _club_document(self) -> dict[str, Any]:
        club = self.store.club()
        return {
            "year": RESOURCE_YEAR,
            "assetId": self.store.persona_id,
            "teamId": int(club.get("badge_id") or 0),
            "lastAccessTime": int(time.time()),
            "platform": "pc",
            "clubName": club.get("name") or "Local FUT Club",
            "clubAbbr": (club.get("name") or "LFC")[:3].upper(),
            "established": int(club.get("established") or 0),
            "divisionOnline": 10,
            "badgeId": int(club.get("badge_id") or 0),
            "skuAccessList": {"FFA15PC": int(time.time())},
        }

    def _user_document(self) -> dict[str, Any]:
        club = self.store.club()
        document = {
            "personaId": self.store.persona_id,
            "personaName": club.get("name") or "LocalFUT",
            "userId": self.store.persona_id,
            "created": int(club.get("created_at") or time.time()),
            "returningUser": 1 if club.get("established") else 0,
            "clubName": club.get("name") or "Local FUT Club",
            "clubAbbr": (club.get("name") or "LFC")[:3].upper(),
            "badgeId": int(club.get("badge_id") or 0),
            "teamId": int(club.get("badge_id") or 0),
            "activeSquadId": 0,
            "userClubList": [self._club_document()],
        }
        if club.get("established"):
            document["INTRO_DONE"] = True
        return document

    def _credits(self) -> dict[str, Any]:
        club = self.store.club()
        coins = int(club.get("coins") or 0)
        points = int(club.get("fifa_points") or 0)
        return {
            "credits": coins,
            "fifaPoints": points,
            "bidTokens": {"count": 0, "updateTime": int(time.time())},
            "currencies": [
                {"name": "coins", "funds": coins, "finalFunds": coins},
                {"name": "points", "funds": points, "finalFunds": points},
            ],
            "unopenedPacks": {"preOrderPacks": 0, "recoveredPacks": 0},
        }

    def _club_stats(self) -> dict[str, Any]:
        items = self.store.items()
        players = [i for i in items if i["item_type"] == "player"]
        gold = sum(1 for i in players if i["rating"] >= 75)
        silver = sum(1 for i in players if 65 <= i["rating"] < 75)
        bronze = sum(1 for i in players if i["rating"] < 65)
        rare = sum(1 for i in players if i["rare_flag"])
        values = {
            "players": len(players), "playersBronze": bronze, "playersSilver": silver,
            "playersGold": gold, "rarePlayers": rare, "staff": 0, "stadia": 0,
            "balls": 0, "kits": 0, "badges": 0, "trophies": 0,
        }
        stat = [{"contextId": 2, "contextValue": 2015, "type": key, "typeValue": value}
                for key, value in values.items()]
        return {"stat": stat, "entries": stat, "playerCount": len(players),
                "totalPlayers": len(players), **values}

    def _club_items(self, query: dict) -> dict[str, Any]:
        start = int(query.get("start", 0) or 0)
        count = max(1, min(int(query.get("count", 50) or 50), 200))

        records = [r for r in self.store.items() if r["pile"] != DB_PILE_TRADE]

        level = str(query.get("level") or query.get("lev") or "").lower()
        level = {"1": "bronze", "2": "silver", "3": "gold", "10": "", "any": ""}.get(level, level)
        if level == "gold":
            records = [r for r in records if r["rating"] >= 75]
        elif level == "silver":
            records = [r for r in records if 65 <= r["rating"] < 75]
        elif level == "bronze":
            records = [r for r in records if r["rating"] < 65]

        position = str(query.get("position") or query.get("pos") or "").upper()
        if position:
            records = [r for r in records if r["position"] == position]

        for key, column in (("nation", "nation_id"), ("nat", "nation_id"),
                            ("league", "league_id"), ("leag", "league_id"),
                            ("team", "team_id"), ("club", "team_id")):
            raw = query.get(key)
            if raw not in (None, "", "-1"):
                try:
                    wanted = int(raw)
                except ValueError:
                    continue
                records = [r for r in records if r[column] == wanted]

        records.sort(key=lambda r: (-r["rating"], -r["rare_flag"], r["asset_id"], r["item_id"]))
        page = records[start : start + count]
        return {
            "itemData": [self.store.item_data(r) for r in page],
            "total": len(records),
            "count": len(page),
            "start": start,
        }

    def _extract_ids(self, query: dict, body: dict) -> list[int]:
        ids: list[int] = []
        for key in ("itemIds", "itemId", "ids", "id"):
            raw = query.get(key)
            if raw:
                ids.extend(int(part) for part in str(raw).split(",") if part.strip().isdigit())
        for key in ("itemId", "itemIds"):
            value = body.get(key)
            if isinstance(value, list):
                ids.extend(int(v) for v in value if str(v).isdigit())
            elif value is not None and str(value).isdigit():
                ids.append(int(value))
        for entry in body.get("itemData") or []:
            if isinstance(entry, dict):
                candidate = entry.get("id") or entry.get("itemId")
                if candidate is not None and str(candidate).isdigit():
                    ids.append(int(candidate))
        return list(dict.fromkeys(ids))

    def _quick_sell(self, query: dict, body: dict) -> dict[str, Any]:
        ids = self._extract_ids(query, body)
        sold_details = []
        for item_id in ids:
            record = self.store.item(item_id)
            if record:
                sold_details.append({
                    "id": item_id, "itemId": item_id,
                    "discardValue": int(record["discard_value"]),
                })
        result = self.store.quick_sell(ids)
        return {
            "items": sold_details,
            "itemData": sold_details,
            "totalCredits": result["balance"],
            "credits": result["balance"],
            "coins": result["balance"],
        }

    def _move_items(self, body: dict) -> dict[str, Any]:
        results = []
        pile_names = {5: DB_PILE_TRADE, 6: DB_PILE_PENDING, 7: DB_PILE_CLUB}
        for entry in body.get("itemData") or []:
            if not isinstance(entry, dict):
                continue
            item_id = int(entry.get("id") or entry.get("itemId") or 0)
            if not item_id:
                continue
            wire_pile = entry.get("pile")
            target = pile_names.get(int(wire_pile)) if wire_pile is not None else DB_PILE_CLUB
            moved = self.store.move_items([item_id], target or DB_PILE_CLUB)
            results.append({
                "id": item_id, "itemId": item_id,
                "success": bool(moved), "reason": "" if moved else "not found",
                "errorCode": 0 if moved else 404,
                "pile": int(wire_pile) if wire_pile is not None else PILE_CLUB,
            })
        return {"itemData": results}

    def _squad_route(self, method: str, path: str, body: dict) -> dict[str, Any]:
        if method in ("PUT", "POST"):
            squad_id = self._tail_int(path)
            slots = body.get("players") or body.get("slots") or []
            normalised = []
            for index, entry in enumerate(slots):
                if not isinstance(entry, dict):
                    continue
                item = entry.get("itemData") or {}
                normalised.append({
                    "index": int(entry.get("index", index)),
                    "itemId": int(item.get("id") or entry.get("itemId") or 0),
                    "position": str(entry.get("position") or item.get("preferredPosition") or ""),
                })
            self.store.save_squad(
                squad_id, normalised,
                formation=str(body.get("formation") or "f442"),
                name=str(body.get("squadName") or "Squad"),
                captain_item_id=int(body.get("captain") or 0),
            )
            return self._squad_details(squad_id)

        if path.endswith("/list"):
            return {"activeSquadId": 0, "squad": [self._squad_compact(0)]}
        if path.endswith("/active"):
            return self._squad_details(0)

        squad_id = self._tail_int(path)
        return self._squad_details(squad_id)

    def _squad_details(self, squad_id: int) -> dict[str, Any]:
        squad = self.store.squad(squad_id)
        by_index = {slot["index"]: slot for slot in squad["slots"]}
        players = []
        for index in range(SQUAD_SIZE):
            slot = by_index.get(index)
            record = self.store.item(slot["itemId"]) if slot and slot["itemId"] else None
            players.append({
                "index": index,
                "itemData": self.store.item_data(record) if record else {"id": 0},
                "kitNumber": index + 1,
            })
        return {
            "id": squad_id, "squadId": squad_id, "personaId": self.store.persona_id,
            "squadName": squad["name"], "formation": squad["formation"],
            "active": squad_id == 0, "changed": False,
            "captain": squad["captainItemId"],
            # Chemistry and star rating are computed client-side; the server
            # only reports what it stored.
            "chemistry": 0, "starRating": 0, "rating": 0,
            "valid": True, "newsquad": 0,
            "kicktakers": [], "tactics": [], "dreamSquad": False,
            "custom": "", "manager": [], "actives": [],
            "players": players,
        }

    def _squad_compact(self, squad_id: int) -> dict[str, Any]:
        squad = self.store.squad(squad_id)
        return {
            "id": squad_id, "personaId": self.store.persona_id,
            "squadName": squad["name"], "formation": squad["formation"],
            "active": squad_id == 0, "changed": False,
            "chemistry": 0, "starRating": 0, "rating": 0,
            "valid": True, "newsquad": 0,
        }

    def _pack_selector(self, body: dict) -> int:
        for key in ("packId", "id", "packType", "purchasePackTypeId", "serverId", "assetId"):
            raw = body.get(key)
            if raw is not None and str(raw).isdigit():
                return int(raw)
        return 0

    def _store_pack_types(self) -> dict[str, Any]:
        club = self.store.club()
        coins = int(club.get("coins") or 0)
        points = int(club.get("fifa_points") or 0)
        offers = []
        for priority, pack in enumerate(PACK_CATALOG):
            offers.append({
                "actionType": "CREATEPACK",
                "assetId": pack.pack_id,
                "bonus": 0,
                "currencies": [
                    {"name": "coins", "funds": coins, "finalFunds": max(0, coins - pack.price)},
                    {"name": "points", "funds": points, "finalFunds": points},
                ],
                "name": f"LOCAL_PACK_NAME_{pack.pack_id}",
                "description": f"FUT_STORE_PACK_{pack.pack_id}_DESC",
                "displayGroup": {"priority": priority, "value": pack.tier},
                "displayGroupAssetId": pack.pack_id,
                "displayGroupUseDefaultImage": True,
                "end": 2147483647,
                "firstPartyStoreId": "0",
                "id": pack.pack_id,
                "isPremium": "Premium" in pack.name or "Rare" in pack.name,
                "isSeasonTicketDiscount": False,
                "points": 0 if pack.currency == "coins" else pack.price,
                "priority": priority,
                "packId": pack.pack_id,
                "packType": pack.pack_id,
                "purchasePackType": "CARDPACK",
                "purchaseCount": 0,
                "purchaseLimit": 999999,
                "quantity": pack.total_items,
                "saleType": "NONE",
                "sortPriority": priority,
                "start": 0,
                "state": "active",
                "unopened": False,
                "useDefaultImage": True,
                "visible": True,
                "coins": pack.price if pack.currency == "coins" else 0,
            })
        return {
            "purchase": offers,
            "timestamp": int(time.time()),
            "packList": offers,
            "packTypes": offers,
            "total": len(offers),
            "unopenedPacks": 0,
            "credits": coins,
            "fifaPoints": points,
        }

    def _purchase_pack(self, body: dict) -> dict[str, Any]:
        pack_id = self._pack_selector(body)
        result = self.store.open_pack(pack_id)
        if "error" in result:
            self.trace.emit("pack-refused", packId=pack_id, reason=result["error"])
            return {"code": "461", "reason": result["error"], "credits": self.store.coins()}

        self.trace.emit(
            "pack-opened", packId=pack_id, name=result["name"],
            cards=len(result["itemData"]), balance=result["balance"],
        )
        return {
            "itemData": result["itemData"],
            "duplicateItemIdList": [],
            "credits": result["balance"],
            "totalCredits": result["balance"],
            "coins": result["balance"],
            "purchasedItems": result["itemData"],
        }

    def _purchased_items(self) -> dict[str, Any]:
        records = self.store.items(DB_PILE_PENDING)
        return {
            "itemData": [self.store.item_data(r, wire_pile=PILE_PURCHASED) for r in records],
            "duplicateItemIdList": [],
            "total": len(records),
            "credits": self.store.coins(),
        }

    def _list_for_sale(self, body: dict) -> dict[str, Any]:
        item = body.get("itemData") or {}
        item_id = int(item.get("id") or body.get("itemId") or 0)
        starting = int(body.get("startingBid") or 150)
        buy_now = int(body.get("buyNowPrice") or 0)
        duration = int(body.get("duration") or 3600)
        auction = self.market.list_item(item_id, starting, buy_now, duration)
        if "reason" in auction:
            return {"code": "470", **auction}
        auction["marketValue"] = buy_now or starting
        auction["cheapestMarketPrice"] = starting
        return auction

    def _bid(self, path: str, body: dict) -> dict[str, Any]:
        trade_id = self._tail_int(path.rsplit("/", 1)[0])
        amount = int(body.get("bid") or body.get("buyNowPrice") or body.get("amount") or 0)
        buy_now = "buyNowPrice" in body
        result = self.market.buy(trade_id, amount, buy_now)
        if "reason" in result:
            self.trace.emit("market-refused", tradeId=trade_id, reason=result["reason"])
        else:
            self.trace.emit("market-buy", tradeId=trade_id, amount=amount, buyNow=buy_now)
        return result

    def _match(self, method: str, path: str, body: dict) -> dict[str, Any]:
        if path.endswith("/end"):
            return {
                "endReason": str(body.get("endReason") or "WIN"),
                "secondsPlayed": int(body.get("secondsPlayed") or 0),
                "matchDifficulty": int(body.get("matchDifficulty") or 0),
                "items": [],
                "matchData": "",
                "credits": self.store.coins(),
                "coins": self.store.coins(),
            }
        if path.endswith("/reset"):
            return {"status": "reset", "stadium": ""}
        if method in ("POST", "PUT"):
            return {"squad": self._squad_details(0), "startDateTime": int(time.time())}
        return {"match": None, "credits": self.store.coins()}


class FutHttpServer(ThreadingHTTPServer):
    """Dual-stack HTTP server; see the note in blaze_server.ReusableThreadingTCPServer."""

    allow_reuse_address = True
    daemon_threads = True
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def start_fut_http(config, store: IdentityStore, market, trace) -> ThreadingHTTPServer:
    handler = type("BoundFutHttpHandler", (FutHttpHandler,),
                   {"store": store, "market": market, "trace": trace})
    server = FutHttpServer(("::", config.fut_http_port), handler)
    trace.emit("listener-started", role="fut-http",
               address=f"{config.host}:{config.fut_http_port}")
    return server
