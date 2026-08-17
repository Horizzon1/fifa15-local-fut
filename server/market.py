"""Transfer market for FIFA 15 local FUT.

Two supplies feed the market:

  * **Bot listings** — synthesised on demand from the player catalog, seeded per
    search so results are stable while a player pages through them, and priced
    from a rating anchor with slow drift. Nothing is stored, so the market never
    bloats the database.
  * **User listings** — real rows in `listings`, created when the player sells a
    card. Bots bid on these over time so selling actually works offline.

Contract shape follows the FIFA 14 Local FUT project; see
`recon/fifa14-server-reference.md` §5.
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from contextlib import closing
from typing import Any, Sequence

from identity import (
    DB_PILE_CLUB,
    DB_PILE_PENDING,
    DB_PILE_TRADE,
    PILE_MARKET,
    PILE_TRADE,
    IdentityStore,
    discard_value_for,
)

# Bot trade ids live above this line so they can never collide with user rows.
BOT_TRADE_ID_BASE = 900_000_000
USER_TRADE_ID_BASE = 100_000_000

# Coin anchor by rating. Real FUT prices are convex in rating; these are the
# knots and everything between them is interpolated.
PRICE_ANCHORS: tuple[tuple[int, int], ...] = (
    (40, 150), (50, 200), (60, 350), (65, 600), (70, 1100),
    (75, 2200), (78, 4000), (80, 7000), (82, 12000), (84, 22000),
    (86, 45000), (88, 90000), (90, 200000), (93, 500000),
)

SELLER_NAMES = (
    "AlpineFC", "RedZoneUtd", "NorthStand", "TikiTaka99", "CatenaccioFC",
    "GegenPress", "TotalFootball", "ParkTheBus", "FalseNine", "WingBackWizard",
    "BoxToBox", "TargetMan", "SweeperKeeper", "Route1FC", "TikiTakaTom",
)


def base_price(rating: int) -> int:
    """Interpolate the coin anchor for a rating."""
    if rating <= PRICE_ANCHORS[0][0]:
        return PRICE_ANCHORS[0][1]
    if rating >= PRICE_ANCHORS[-1][0]:
        return PRICE_ANCHORS[-1][1]
    for (low_rating, low_price), (high_rating, high_price) in zip(PRICE_ANCHORS, PRICE_ANCHORS[1:]):
        if low_rating <= rating <= high_rating:
            span = high_rating - low_rating
            weight = (rating - low_rating) / span if span else 0
            return int(round(low_price + (high_price - low_price) * weight))
    return PRICE_ANCHORS[-1][1]


def market_price(player: dict[str, Any], rare: bool, now: int | None = None) -> int:
    """Price a card: rating anchor, rarity premium, and a slow drift over time.

    The drift is a sine wave keyed to the asset, so prices move but a given card
    stays recognisably in its band rather than jumping around.
    """
    now = now if now is not None else int(time.time())
    price = base_price(int(player.get("rating", 50)))
    if rare:
        price = int(price * 1.6)

    # A per-asset phase so different cards peak at different times.
    phase = (int(player.get("assetId", 0)) % 360) * math.pi / 180
    # One full cycle per six hours.
    drift = math.sin(now / 21600 + phase) * 0.12
    price = int(price * (1 + drift))

    return max(150, round_price(price))


def round_price(value: int) -> int:
    """Snap to FUT's bid increments so prices look native."""
    if value < 1000:
        return max(150, (value // 50) * 50)
    if value < 10000:
        return (value // 100) * 100
    if value < 50000:
        return (value // 250) * 250
    if value < 100000:
        return (value // 500) * 500
    return (value // 1000) * 1000


def next_bid(current: int) -> int:
    if current < 1000:
        return current + 50
    if current < 10000:
        return current + 100
    if current < 50000:
        return current + 250
    if current < 100000:
        return current + 500
    return current + 1000


class TransferMarket:
    def __init__(self, store: IdentityStore):
        self.store = store
        self.catalog = store.catalog

    # -- searching -------------------------------------------------------

    def search(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Return listings matching the client's filters.

        User listings come first so a player always sees their own market, then
        bot supply fills the page.
        """
        start = int(filters.get("start", 0) or 0)
        count = max(1, min(int(filters.get("count", 20) or 20), 50))

        matches = self._matching_players(filters)
        auctions: list[dict[str, Any]] = []

        # Deterministic per-query seed keeps paging stable.
        seed = hashlib.sha256(
            "|".join(f"{k}={v}" for k, v in sorted(filters.items()) if k not in ("start", "count")).encode()
        ).hexdigest()
        rng = random.Random(int(seed[:16], 16))

        now = int(time.time())
        for index, player in enumerate(matches[start : start + count]):
            rare = player in self.catalog.rare_by_tier.get(player["tier"], [])
            price = market_price(player, rare, now)
            # Per-copy spread so identical cards are not all the same price.
            spread = rng.uniform(0.85, 1.25)
            buy_now = round_price(int(price * spread))
            starting = round_price(max(150, int(buy_now * rng.uniform(0.55, 0.85))))
            trade_id = BOT_TRADE_ID_BASE + (start + index) * 7 + (player["assetId"] % 7)

            auctions.append(
                self._bot_auction(player, rare, trade_id, starting, buy_now, rng, now)
            )

        return {
            "auctionInfo": auctions,
            "duplicateItemIdList": [],
            "total": len(matches),
            "credits": self.store.coins(),
            "totalCredits": self.store.coins(),
            "coins": self.store.coins(),
        }

    def _matching_players(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        players = self.catalog.players

        def as_int(*keys: str) -> int | None:
            for key in keys:
                raw = filters.get(key)
                if raw not in (None, "", "-1", -1):
                    try:
                        return int(raw)
                    except (TypeError, ValueError):
                        continue
            return None

        level = str(filters.get("lev") or filters.get("level") or "").lower()
        level = {"1": "bronze", "2": "silver", "3": "gold", "10": "", "any": ""}.get(level, level)
        position = str(filters.get("pos") or filters.get("position") or "").upper()
        nation = as_int("nat", "nation")
        league = as_int("leag", "league")
        team = as_int("team", "club")
        min_rating = as_int("minrating", "minRating")
        max_rating = as_int("maxrating", "maxRating")
        name = str(filters.get("name") or filters.get("playerName") or "").strip().lower()
        max_price = as_int("maxb", "maxBuy", "maxPrice")

        result = []
        for player in players:
            if level and player["tier"] != level:
                continue
            if position and player["position"] != position:
                continue
            if nation is not None and player.get("nationId") != nation:
                continue
            if league is not None and player.get("leagueId") != league:
                continue
            if team is not None and player.get("teamId") != team:
                continue
            if min_rating is not None and player["rating"] < min_rating:
                continue
            if max_rating is not None and player["rating"] > max_rating:
                continue
            if name and name not in player["name"].lower():
                continue
            if max_price is not None and base_price(player["rating"]) > max_price:
                continue
            result.append(player)

        # Best cards first: that is what a market search looks like.
        result.sort(key=lambda p: (-p["rating"], p["assetId"]))
        return result

    def _bot_auction(
        self,
        player: dict[str, Any],
        rare: bool,
        trade_id: int,
        starting: int,
        buy_now: int,
        rng: random.Random,
        now: int,
    ) -> dict[str, Any]:
        """A synthetic listing, shaped exactly like a real one."""
        expires = rng.choice([300, 600, 900, 1800, 3600])
        offers = rng.choice([0, 0, 0, 1, 2, 3])
        current_bid = starting if offers == 0 else round_price(int(starting * (1 + 0.1 * offers)))

        # Build a transient ItemData without touching the database.
        record = {
            "item_id": trade_id,
            "asset_id": player["assetId"],
            "resource_id": player["assetId"],
            "rating": player["rating"],
            "position": player["position"],
            "team_id": player.get("teamId") or 0,
            "league_id": player.get("leagueId") or 0,
            "nation_id": player.get("nationId") or 0,
            "rare_flag": int(rare),
            "pile": DB_PILE_CLUB,
            "item_state": "forSale",
            "untradeable": 0,
            "contract": 7,
            "fitness": 99,
            "morale": 50,
            "training": 0,
            "play_style": 0,
            "loyalty_bonus": 0,
            "discard_value": discard_value_for(player["rating"], rare),
            "last_sale_price": current_bid,
            "owners": 1,
            "created_at": now,
            "payload": "{}",
        }
        item_data = self.store.item_data(record, wire_pile=PILE_MARKET)

        return {
            "tradeId": trade_id,
            "tradeState": "active",
            "expires": expires,
            "EXPIRE_TIME": expires,
            "expireTime": expires,
            "startTime": 0,
            "endtime": 2147483647,
            "buyNowPrice": buy_now,
            "startingBid": starting,
            "currentBid": current_bid,
            "offers": offers,
            "watched": False,
            "bidState": "none",
            "tradeOwner": False,
            "sellerName": rng.choice(SELLER_NAMES),
            "sellerEstablished": 2014,
            "sellerId": 2_000_000 + (trade_id % 100_000),
            "confidenceValue": 100,
            "itemData": item_data,
        }

    # -- buying ----------------------------------------------------------

    def buy(self, trade_id: int, amount: int, buy_now: bool) -> dict[str, Any]:
        """Buy or bid on a bot listing. Winning moves the card into the club."""
        balance = self.store.coins()
        if amount <= 0:
            return {"reason": "invalid amount", "tradeId": trade_id}
        if balance < amount:
            return {
                "reason": "insufficient funds", "tradeId": trade_id,
                "credits": balance, "coins": balance, "totalCredits": balance,
            }

        player = self._player_for_trade(trade_id)
        if player is None:
            return {"reason": "auction not found", "tradeId": trade_id}

        rare = player in self.catalog.rare_by_tier.get(player["tier"], [])

        if buy_now:
            record = self.store.add_player_item(player, pile=DB_PILE_PENDING, rare=rare)
            balance = self.store.adjust_coins(-amount, "market-buy", str(trade_id))
            return {
                "tradeId": trade_id,
                "tradeState": "closed",
                "bidState": "buyNow",
                "currentBid": amount,
                "offers": 1,
                "credits": balance,
                "totalCredits": balance,
                "coins": balance,
                "itemData": self.store.item_data(record, wire_pile=PILE_MARKET),
            }

        # A bid holds the coins but does not deliver the card yet.
        balance = self.store.adjust_coins(-amount, "market-bid", str(trade_id))
        return {
            "tradeId": trade_id,
            "tradeState": "active",
            "bidState": "highest",
            "currentBid": amount,
            "offers": 1,
            "credits": balance,
            "totalCredits": balance,
            "coins": balance,
        }

    def _player_for_trade(self, trade_id: int) -> dict[str, Any] | None:
        """Recover which card a synthetic trade id refers to.

        Bot listings are not stored, so the id is reversed against the catalog.
        """
        if trade_id < BOT_TRADE_ID_BASE:
            return None
        offset = trade_id - BOT_TRADE_ID_BASE
        remainder = offset % 7
        index = offset // 7
        candidates = [p for p in self.catalog.players if p["assetId"] % 7 == remainder]
        if not candidates:
            return None
        candidates.sort(key=lambda p: (-p["rating"], p["assetId"]))
        return candidates[index % len(candidates)]

    # -- selling ---------------------------------------------------------

    def list_item(self, item_id: int, starting_bid: int, buy_now_price: int, duration: int) -> dict[str, Any]:
        record = self.store.item(item_id)
        if record is None:
            return {"reason": "item not found", "itemId": item_id}
        if record["untradeable"]:
            return {"reason": "item is untradeable", "itemId": item_id}

        now = int(time.time())
        trade_id = USER_TRADE_ID_BASE + item_id % 1_000_000
        with self.store._lock, closing(self.store._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO listings (trade_id, persona_id, item_id, start_price,"
                " buy_now_price, current_bid, bidder, expires_at, state, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (trade_id, self.store.persona_id, item_id, starting_bid, buy_now_price,
                 0, "", now + max(60, duration), "active", now),
            )
            connection.execute(
                "UPDATE items SET pile=?, item_state=? WHERE item_id=? AND persona_id=?",
                (DB_PILE_TRADE, "forSale", item_id, self.store.persona_id),
            )

        return self._user_auction(trade_id)

    def trade_pile(self) -> dict[str, Any]:
        """Everything the player currently has listed or has sold."""
        self._settle_expired()
        with closing(self.store._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM listings WHERE persona_id=? AND state IN ('active','closed')"
                " ORDER BY created_at DESC",
                (self.store.persona_id,),
            ).fetchall()

        auctions = [self._auction_from_row(dict(row)) for row in rows]
        balance = self.store.coins()
        return {
            "auctionInfo": [a for a in auctions if a],
            "duplicateItemIdList": [],
            "total": len(auctions),
            "credits": balance,
            "totalCredits": balance,
            "coins": balance,
        }

    def remove_listing(self, trade_id: int) -> dict[str, Any]:
        with self.store._lock, closing(self.store._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM listings WHERE trade_id=? AND persona_id=?",
                (trade_id, self.store.persona_id),
            ).fetchone()
            if row:
                connection.execute("DELETE FROM listings WHERE trade_id=?", (trade_id,))
                connection.execute(
                    "UPDATE items SET pile=?, item_state=? WHERE item_id=? AND persona_id=?",
                    (DB_PILE_CLUB, "free", int(row["item_id"]), self.store.persona_id),
                )
        return {"id": trade_id, "tradeId": trade_id}

    def _settle_expired(self) -> None:
        """Let bots buy listings whose time is up, so selling actually pays.

        A listing sells if its buy-now is at or below the card's market value;
        otherwise it expires unsold and the card returns to the club.
        """
        now = int(time.time())
        with self.store._lock, closing(self.store._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM listings WHERE persona_id=? AND state='active' AND expires_at<=?",
                (self.store.persona_id, now),
            ).fetchall()

            for row in rows:
                record = self.store.item(int(row["item_id"]))
                if record is None:
                    connection.execute("DELETE FROM listings WHERE trade_id=?", (row["trade_id"],))
                    continue

                player = self.catalog.by_asset.get(int(record["asset_id"]), {})
                value = market_price(player, bool(record["rare_flag"]), now) if player else 0
                asking = int(row["buy_now_price"]) or int(row["start_price"])

                if asking and asking <= value * 1.1:
                    connection.execute(
                        "UPDATE listings SET state='closed', current_bid=?, bidder='market' WHERE trade_id=?",
                        (asking, row["trade_id"]),
                    )
                    connection.execute(
                        "UPDATE items SET item_state='sold', last_sale_price=? WHERE item_id=?",
                        (asking, record["item_id"]),
                    )
                else:
                    connection.execute(
                        "UPDATE listings SET state='expired' WHERE trade_id=?", (row["trade_id"],)
                    )
                    connection.execute(
                        "UPDATE items SET pile=?, item_state='free' WHERE item_id=?",
                        (DB_PILE_CLUB, record["item_id"]),
                    )

    def collect_sold(self) -> dict[str, Any]:
        """Bank the proceeds of sold listings and remove the cards."""
        total = 0
        with self.store._lock, closing(self.store._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM listings WHERE persona_id=? AND state='closed'",
                (self.store.persona_id,),
            ).fetchall()
            for row in rows:
                total += int(row["current_bid"])
                connection.execute("DELETE FROM listings WHERE trade_id=?", (row["trade_id"],))
                connection.execute("DELETE FROM items WHERE item_id=?", (row["item_id"],))

        balance = self.store.adjust_coins(total, "market-sale") if total else self.store.coins()
        return {"coins": total, "credits": balance, "totalCredits": balance, "balance": balance}

    def _user_auction(self, trade_id: int) -> dict[str, Any]:
        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT * FROM listings WHERE trade_id=?", (trade_id,)).fetchone()
        return self._auction_from_row(dict(row)) if row else {}

    def _auction_from_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        record = self.store.item(int(row["item_id"]))
        if record is None:
            return None
        now = int(time.time())
        expires = max(0, int(row["expires_at"]) - now)
        closed = row["state"] == "closed"

        auction = {
            "tradeId": int(row["trade_id"]),
            "tradeState": "closed" if closed else "active",
            "expires": 0 if closed else expires,
            "EXPIRE_TIME": 0 if closed else expires,
            "expireTime": 0 if closed else expires,
            "startTime": 0,
            "endtime": 2147483647,
            "buyNowPrice": int(row["buy_now_price"]),
            "startingBid": int(row["start_price"]),
            "currentBid": int(row["current_bid"]),
            "offers": 1 if int(row["current_bid"]) else 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": True,
            "sellerName": self.store.club().get("name", "Local FUT Club"),
            "sellerEstablished": 2014,
            "sellerId": self.store.persona_id,
            "confidenceValue": 100,
        }
        # A closed auction must carry real item data; an empty object makes the
        # client's trade-pile parser dereference null and crash.
        auction["itemData"] = self.store.item_data(record, wire_pile=PILE_TRADE)
        return auction
