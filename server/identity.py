"""Persistent club, items, squads, packs and transfer market for FIFA 15 FUT.

Everything the player owns lives in one SQLite file so a club survives across
sessions. The wire format is FUT's `ItemData` object, whose field names and
ORDER are load-bearing: the client's card renderer stops reading early, so
identity and position fields must come first.

Contract details inherited from the FIFA 14 Local FUT project
(https://github.com/KyroGeorge2/FIFA-14-Local-FUT); see
`recon/fifa14-server-reference.md`.
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

RESOURCE_GAME_YEAR = 2015
PLAYER_ITEM_TYPE = "player"
PLAYER_ATTRIBUTE_COUNT = 6
PLAYER_STAT_COUNT = 5

# Wire `pile` values the client understands.
PILE_MARKET = 0
PILE_TRADE = 5      # transfer list
PILE_PURCHASED = 6  # unopened / newly acquired
PILE_CLUB = 7

# DB-side pile names.
DB_PILE_CLUB = "club"
DB_PILE_SQUAD = "squad"
DB_PILE_TRADE = "trade"
DB_PILE_PENDING = "pending"

DB_TO_WIRE_PILE = {
    DB_PILE_CLUB: PILE_CLUB,
    DB_PILE_SQUAD: PILE_CLUB,
    DB_PILE_TRADE: PILE_TRADE,
    DB_PILE_PENDING: PILE_PURCHASED,
}

SQUAD_SIZE = 23

SCHEMA = """
CREATE TABLE IF NOT EXISTS club (
    persona_id      INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL DEFAULT 'Local FUT Club',
    coins           INTEGER NOT NULL DEFAULT 0,
    fifa_points     INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    established     INTEGER NOT NULL DEFAULT 0,
    badge_id        INTEGER NOT NULL DEFAULT 0,
    stadium_id      INTEGER NOT NULL DEFAULT 0,
    formation       TEXT    NOT NULL DEFAULT 'f442'
);

CREATE TABLE IF NOT EXISTS items (
    item_id         INTEGER PRIMARY KEY,
    persona_id      INTEGER NOT NULL,
    asset_id        INTEGER NOT NULL,
    resource_id     INTEGER NOT NULL,
    item_type       TEXT    NOT NULL DEFAULT 'player',
    rating          INTEGER NOT NULL DEFAULT 0,
    position        TEXT    NOT NULL DEFAULT 'CM',
    team_id         INTEGER NOT NULL DEFAULT 0,
    league_id       INTEGER NOT NULL DEFAULT 0,
    nation_id       INTEGER NOT NULL DEFAULT 0,
    rare_flag       INTEGER NOT NULL DEFAULT 0,
    pile            TEXT    NOT NULL DEFAULT 'club',
    item_state      TEXT    NOT NULL DEFAULT 'free',
    untradeable     INTEGER NOT NULL DEFAULT 0,
    contract        INTEGER NOT NULL DEFAULT 7,
    fitness         INTEGER NOT NULL DEFAULT 99,
    morale          INTEGER NOT NULL DEFAULT 50,
    training        INTEGER NOT NULL DEFAULT 0,
    play_style      INTEGER NOT NULL DEFAULT 0,
    loyalty_bonus   INTEGER NOT NULL DEFAULT 0,
    discard_value   INTEGER NOT NULL DEFAULT 0,
    last_sale_price INTEGER NOT NULL DEFAULT 0,
    owners          INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL,
    payload         TEXT    NOT NULL DEFAULT '{}',
    FOREIGN KEY (persona_id) REFERENCES club(persona_id)
);
CREATE INDEX IF NOT EXISTS idx_items_persona_pile ON items(persona_id, pile);
CREATE INDEX IF NOT EXISTS idx_items_asset        ON items(asset_id);

CREATE TABLE IF NOT EXISTS squads (
    squad_id        INTEGER NOT NULL,
    persona_id      INTEGER NOT NULL,
    name            TEXT    NOT NULL DEFAULT 'Squad',
    formation       TEXT    NOT NULL DEFAULT 'f442',
    captain_item_id INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (squad_id, persona_id)
);

CREATE TABLE IF NOT EXISTS squad_slots (
    squad_id        INTEGER NOT NULL,
    persona_id      INTEGER NOT NULL,
    slot_index      INTEGER NOT NULL,
    item_id         INTEGER NOT NULL DEFAULT 0,
    position        TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (squad_id, persona_id, slot_index)
);

CREATE TABLE IF NOT EXISTS listings (
    trade_id        INTEGER PRIMARY KEY,
    persona_id      INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    start_price     INTEGER NOT NULL,
    buy_now_price   INTEGER NOT NULL,
    current_bid     INTEGER NOT NULL DEFAULT 0,
    bidder          TEXT    NOT NULL DEFAULT '',
    expires_at      INTEGER NOT NULL,
    state           TEXT    NOT NULL DEFAULT 'active',
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_state ON listings(state);

CREATE TABLE IF NOT EXISTS ledger (
    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id      INTEGER NOT NULL,
    delta           INTEGER NOT NULL,
    balance         INTEGER NOT NULL,
    reason          TEXT    NOT NULL,
    reference       TEXT    NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_persona ON ledger(persona_id);

CREATE TABLE IF NOT EXISTS grants (
    grant_key       TEXT PRIMARY KEY,
    applied_at      INTEGER NOT NULL,
    amount          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Face-stat derivation
# ---------------------------------------------------------------------------

def _weighted(attributes: dict[str, int], weights: dict[str, float]) -> int:
    total = sum(attributes.get(key, 0) * weight for key, weight in weights.items())
    return max(1, min(99, round(total)))


def face_stats(player: dict[str, Any]) -> list[int]:
    """The six numbers printed on a FUT card.

    Outfield: PAC SHO PAS DRI DEF PHY. Goalkeeper: DIV HAN KIC REF SPD POS.
    """
    attributes = player.get("attributes") or {}
    if player.get("position") == "GK":
        speed = round((attributes.get("acceleration", 0) + attributes.get("sprintspeed", 0)) / 2)
        return [
            attributes.get("gkdiving", 0),
            attributes.get("gkhandling", 0),
            attributes.get("gkkicking", 0),
            attributes.get("gkreflexes", 0),
            max(1, min(99, speed)),
            attributes.get("gkpositioning", 0),
        ]
    return [
        _weighted(attributes, {"acceleration": 0.55, "sprintspeed": 0.45}),
        _weighted(attributes, {
            "finishing": 0.45, "longshots": 0.20, "shotpower": 0.20,
            "positioning": 0.05, "volleys": 0.05, "penalties": 0.05,
        }),
        _weighted(attributes, {
            "shortpassing": 0.35, "vision": 0.20, "crossing": 0.15,
            "longpassing": 0.15, "curve": 0.05, "freekickaccuracy": 0.10,
        }),
        _weighted(attributes, {
            "dribbling": 0.50, "ballcontrol": 0.35, "agility": 0.10, "balance": 0.05,
        }),
        _weighted(attributes, {
            "marking": 0.30, "standingtackle": 0.30, "interceptions": 0.20,
            "headingaccuracy": 0.10, "slidingtackle": 0.10,
        }),
        _weighted(attributes, {
            "strength": 0.50, "stamina": 0.25, "aggression": 0.20, "jumping": 0.05,
        }),
    ]


def discard_value_for(rating: int, rare: bool) -> int:
    """Quick-sell price, following FUT's rating bands."""
    if rating >= 85:
        base = 1000
    elif rating >= 80:
        base = 600
    elif rating >= 75:
        base = 400
    elif rating >= 70:
        base = 300
    elif rating >= 65:
        base = 200
    elif rating >= 60:
        base = 100
    else:
        base = 50
    return base * 2 if rare else base


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class PlayerCatalog:
    """The FIFA 15 players extracted from the client's own database."""

    def __init__(self, path: Path):
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        self.players: list[dict[str, Any]] = document["players"]
        self.by_asset: dict[int, dict[str, Any]] = {p["assetId"]: p for p in self.players}
        self.by_tier: dict[str, list[dict[str, Any]]] = {"bronze": [], "silver": [], "gold": []}
        for player in self.players:
            self.by_tier.setdefault(player["tier"], []).append(player)
        # Rare pools are the stronger half of each tier, so a rare card is
        # meaningfully better than a common one from the same tier.
        self.rare_by_tier: dict[str, list[dict[str, Any]]] = {}
        for tier, pool in self.by_tier.items():
            if not pool:
                self.rare_by_tier[tier] = []
                continue
            ordered = sorted(pool, key=lambda p: -p["rating"])
            self.rare_by_tier[tier] = ordered[: max(1, len(ordered) // 2)]

    def __len__(self) -> int:
        return len(self.players)

    def pick(self, tier: str, rare: bool, rng: random.Random, position: str | None = None) -> dict[str, Any]:
        pool = (self.rare_by_tier if rare else self.by_tier).get(tier) or self.by_tier["bronze"]
        if position:
            filtered = [p for p in pool if p["position"] == position]
            if filtered:
                pool = filtered
        return rng.choice(pool)

    def summary(self) -> dict[str, int]:
        return {tier: len(pool) for tier, pool in self.by_tier.items()}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

@dataclass
class PackDefinition:
    pack_id: int
    name: str
    price: int
    currency: str          # "coins" or "points"
    players: int
    total_items: int
    tier: str              # highest tier the pack can draw
    guaranteed_rare: int    # how many cards are guaranteed rare
    gold_chance: float      # chance a non-guaranteed card is gold
    rare_chance: float


# Retail-shaped pack lineup. Prices follow FIFA 15's real store.
PACK_CATALOG: tuple[PackDefinition, ...] = (
    PackDefinition(100, "Bronze Pack",         400,  "coins", 12, 12, "bronze", 0, 0.00, 0.10),
    PackDefinition(101, "Premium Bronze Pack", 750,  "coins", 12, 12, "bronze", 1, 0.00, 0.30),
    PackDefinition(200, "Silver Pack",         2500, "coins", 12, 12, "silver", 0, 0.02, 0.12),
    PackDefinition(201, "Premium Silver Pack", 3750, "coins", 12, 12, "silver", 1, 0.05, 0.35),
    PackDefinition(300, "Gold Pack",           5000, "coins", 12, 12, "gold",   0, 0.35, 0.15),
    PackDefinition(301, "Premium Gold Pack",   7500, "coins", 12, 12, "gold",   1, 0.60, 0.35),
    PackDefinition(302, "Rare Gold Pack",      15000, "coins", 12, 12, "gold",  3, 0.85, 0.60),
    PackDefinition(303, "Jumbo Premium Gold",  25000, "coins", 24, 24, "gold",  5, 0.90, 0.55),
)

PACK_BY_ID = {pack.pack_id: pack for pack in PACK_CATALOG}


class IdentityStore:
    """SQLite-backed club state."""

    def __init__(self, database: Path, catalog: PlayerCatalog, persona_id: int = 1_000_001):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog
        self.persona_id = persona_id
        self._lock = threading.RLock()
        self._rng = random.Random()
        self._next_item_id = 0
        self._initialise()

    # -- plumbing --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialise(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.executescript(SCHEMA)
            now = int(time.time())
            connection.execute(
                "INSERT OR IGNORE INTO club (persona_id, created_at, updated_at) VALUES (?,?,?)",
                (self.persona_id, now, now),
            )
            row = connection.execute("SELECT MAX(item_id) AS top FROM items").fetchone()
            self._next_item_id = max(int(row["top"] or 0), 1_500_000_000)

    def _allocate_item_id(self) -> int:
        self._next_item_id += 1
        return self._next_item_id

    # -- club ------------------------------------------------------------

    def club(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM club WHERE persona_id=?", (self.persona_id,)
            ).fetchone()
            return dict(row) if row else {}

    def club_exists(self) -> bool:
        return bool(self.club().get("established"))

    def establish_club(self, name: str) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE club SET name=?, established=1, updated_at=? WHERE persona_id=?",
                (name, now, self.persona_id),
            )
        return self.club()

    def coins(self) -> int:
        return int(self.club().get("coins", 0))

    def adjust_coins(self, delta: int, reason: str, reference: str = "") -> int:
        """Move the balance and record why. Never lets a balance go negative."""
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT coins FROM club WHERE persona_id=?", (self.persona_id,)
            ).fetchone()
            balance = max(0, int(row["coins"]) + delta)
            connection.execute(
                "UPDATE club SET coins=?, updated_at=? WHERE persona_id=?",
                (balance, now, self.persona_id),
            )
            connection.execute(
                "INSERT INTO ledger (persona_id, delta, balance, reason, reference, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (self.persona_id, delta, balance, reason, reference, now),
            )
            return balance

    def grant_once(self, key: str, amount: int, reason: str) -> dict[str, Any]:
        """Idempotent coin grant, used by the test-coins helper."""
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT * FROM grants WHERE grant_key=?", (key,)
            ).fetchone()
            if existing:
                current = connection.execute(
                    "SELECT coins FROM club WHERE persona_id=?", (self.persona_id,)
                ).fetchone()
                return {"applied": False, "balance": int(current["coins"]), "grant": key}
            connection.execute(
                "INSERT INTO grants (grant_key, applied_at, amount) VALUES (?,?,?)",
                (key, now, amount),
            )
        balance = self.adjust_coins(amount, reason, key)
        return {"applied": True, "balance": balance, "grant": key}

    # -- items -----------------------------------------------------------

    def add_player_item(
        self,
        player: dict[str, Any],
        *,
        pile: str = DB_PILE_CLUB,
        rare: bool | None = None,
        untradeable: bool = False,
        contract: int = 7,
    ) -> dict[str, Any]:
        now = int(time.time())
        item_id = self._allocate_item_id()
        rare_flag = int(rare if rare is not None else player["tier"] == "gold" and player["rating"] >= 80)
        record = {
            "item_id": item_id,
            "persona_id": self.persona_id,
            "asset_id": player["assetId"],
            # Base FIFA cards serialize resourceId equal to assetId.
            "resource_id": player["assetId"],
            "item_type": PLAYER_ITEM_TYPE,
            "rating": player["rating"],
            "position": player["position"],
            "team_id": player.get("teamId") or 0,
            "league_id": player.get("leagueId") or 0,
            "nation_id": player.get("nationId") or 0,
            "rare_flag": rare_flag,
            "pile": pile,
            "item_state": "free",
            "untradeable": int(untradeable),
            "contract": contract,
            "fitness": 99,
            "morale": 50,
            "training": 0,
            "play_style": 0,
            "loyalty_bonus": 0,
            "discard_value": 0 if untradeable else discard_value_for(player["rating"], bool(rare_flag)),
            "last_sale_price": 0,
            "owners": 1,
            "created_at": now,
            "payload": json.dumps({"name": player["name"], "commonName": player.get("commonName", "")}),
        }
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO items (item_id, persona_id, asset_id, resource_id, item_type, rating,"
                " position, team_id, league_id, nation_id, rare_flag, pile, item_state, untradeable,"
                " contract, fitness, morale, training, play_style, loyalty_bonus, discard_value,"
                " last_sale_price, owners, created_at, payload)"
                " VALUES (:item_id,:persona_id,:asset_id,:resource_id,:item_type,:rating,:position,"
                ":team_id,:league_id,:nation_id,:rare_flag,:pile,:item_state,:untradeable,:contract,"
                ":fitness,:morale,:training,:play_style,:loyalty_bonus,:discard_value,"
                ":last_sale_price,:owners,:created_at,:payload)",
                record,
            )
        return record

    def items(self, pile: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM items WHERE persona_id=?"
        params: list[Any] = [self.persona_id]
        if pile:
            query += " AND pile=?"
            params.append(pile)
        query += " ORDER BY created_at DESC, item_id DESC"
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, params)]

    def item(self, item_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM items WHERE item_id=? AND persona_id=?", (item_id, self.persona_id)
            ).fetchone()
            return dict(row) if row else None

    def move_items(self, item_ids: Sequence[int], pile: str) -> int:
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.executemany(
                "UPDATE items SET pile=? WHERE item_id=? AND persona_id=?",
                [(pile, item_id, self.persona_id) for item_id in item_ids],
            )
            return cursor.rowcount

    def delete_items(self, item_ids: Sequence[int]) -> int:
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.executemany(
                "DELETE FROM items WHERE item_id=? AND persona_id=?",
                [(item_id, self.persona_id) for item_id in item_ids],
            )
            return cursor.rowcount

    def quick_sell(self, item_ids: Sequence[int]) -> dict[str, Any]:
        total = 0
        sold: list[int] = []
        for item_id in item_ids:
            record = self.item(item_id)
            if not record or record["untradeable"]:
                continue
            total += int(record["discard_value"])
            sold.append(item_id)
        if sold:
            self.delete_items(sold)
            balance = self.adjust_coins(total, "quick-sell", ",".join(map(str, sold)))
        else:
            balance = self.coins()
        return {"soldItemIds": sold, "coins": total, "balance": balance}

    # -- wire format -----------------------------------------------------

    def item_data(self, record: dict[str, Any], wire_pile: int | None = None) -> dict[str, Any]:
        """Build the FUT `ItemData` object.

        Field order is deliberate: the client's parser reads sequentially and a
        late `preferredPosition` makes outfield cards render with the goalkeeper
        stat template.
        """
        asset_id = int(record["asset_id"])
        player = self.catalog.by_asset.get(asset_id, {})
        payload = json.loads(record.get("payload") or "{}")
        attributes = face_stats(player) if player else [0] * PLAYER_ATTRIBUTE_COUNT
        rare_flag = int(record["rare_flag"])
        pile_value = wire_pile if wire_pile is not None else DB_TO_WIRE_PILE.get(record["pile"], PILE_CLUB)

        return {
            "id": int(record["item_id"]),
            "assetId": asset_id,
            "resourceId": int(record["resource_id"]),
            "rating": int(record["rating"]),
            "preferredPosition": record["position"],
            # lowercase `teamid` is the spelling the native card renderer reads.
            "teamid": int(record["team_id"]),
            "leagueId": int(record["league_id"]),
            "nation": int(record["nation_id"]),
            "itemType": PLAYER_ITEM_TYPE,
            "itemState": record["item_state"],
            "formation": "f442",
            "contract": int(record["contract"]),
            "fitness": int(record["fitness"]),
            "injuryGames": 0,
            "injuryType": "none",
            "suspension": 0,
            "training": int(record["training"]),
            "playStyle": int(record["play_style"]),
            "discardValue": int(record["discard_value"]),
            "lastSalePrice": int(record["last_sale_price"]),
            "timestamp": max(1, int(record["created_at"])),
            "untradeable": bool(record["untradeable"]),
            "rareflag": rare_flag,
            # A rare/common discriminator, NOT a positional band.
            "cardsubtypeid": 1 if rare_flag else 0,
            "assists": 0,
            "lifetimeAssists": 0,
            "attributeList": [{"index": i, "value": v} for i, v in enumerate(attributes)],
            "statsList": [{"index": i, "value": 0} for i in range(PLAYER_STAT_COUNT)],
            "lifetimeStats": [{"index": i, "value": 0} for i in range(PLAYER_STAT_COUNT)],
            "itemId": int(record["item_id"]),
            "teamId": int(record["team_id"]),
            "name": payload.get("name") or player.get("name", ""),
            "commonName": payload.get("commonName") or player.get("commonName", ""),
            "owners": int(record["owners"]),
            "morale": int(record["morale"]),
            "playerId": asset_id,
            "rareFlag": rare_flag,
            "loyaltyBonus": int(record["loyalty_bonus"]),
            "pile": pile_value,
            "resourceGameYear": RESOURCE_GAME_YEAR,
            "attributeArray": attributes,
            "statsArray": [0] * PLAYER_STAT_COUNT,
            "lifetimeStatsArray": [0] * PLAYER_STAT_COUNT,
            # Always the base asset, even for special cards.
            "definitionId": asset_id,
        }

    # -- packs -----------------------------------------------------------

    def open_pack(self, pack_id: int) -> dict[str, Any]:
        """Charge for a pack, draw its contents, and file them in the club."""
        pack = PACK_BY_ID.get(pack_id)
        if pack is None:
            return {"error": "unknown pack", "packId": pack_id}

        balance = self.coins()
        if pack.currency == "coins" and balance < pack.price:
            return {"error": "insufficient coins", "required": pack.price, "balance": balance}

        drawn = self._draw_pack(pack)
        records = [
            self.add_player_item(player, pile=DB_PILE_PENDING, rare=rare)
            for player, rare in drawn
        ]
        if pack.currency == "coins":
            balance = self.adjust_coins(-pack.price, "pack-purchase", str(pack_id))

        return {
            "packId": pack.pack_id,
            "name": pack.name,
            "price": pack.price,
            "balance": balance,
            "itemData": [self.item_data(record, wire_pile=PILE_PURCHASED) for record in records],
        }

    def _draw_pack(self, pack: PackDefinition) -> list[tuple[dict[str, Any], bool]]:
        """Pick a pack's cards.

        Guaranteed rares are drawn first, then the remainder rolls independently
        for tier and rarity. One goalkeeper is forced so squads are buildable.
        """
        rng = self._rng
        tier_ladder = {"bronze": ["bronze"], "silver": ["bronze", "silver"], "gold": ["silver", "gold"]}
        pool_tiers = tier_ladder[pack.tier]

        drawn: list[tuple[dict[str, Any], bool]] = []
        for index in range(pack.players):
            rare = index < pack.guaranteed_rare or rng.random() < pack.rare_chance
            if pack.tier == "gold":
                tier = "gold" if (index < pack.guaranteed_rare or rng.random() < pack.gold_chance) else rng.choice(pool_tiers)
            elif pack.tier == "silver":
                tier = "silver" if rng.random() > 0.25 else "bronze"
            else:
                tier = "bronze"
            # Guarantee at least one keeper per pack.
            position = "GK" if index == pack.players - 1 and not any(
                p["position"] == "GK" for p, _ in drawn
            ) else None
            drawn.append((self.catalog.pick(tier, rare, rng, position), rare))
        return drawn

    # -- squads ----------------------------------------------------------

    def squad(self, squad_id: int = 0) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            head = connection.execute(
                "SELECT * FROM squads WHERE squad_id=? AND persona_id=?", (squad_id, self.persona_id)
            ).fetchone()
            slots = connection.execute(
                "SELECT * FROM squad_slots WHERE squad_id=? AND persona_id=? ORDER BY slot_index",
                (squad_id, self.persona_id),
            ).fetchall()
        return {
            "squadId": squad_id,
            "name": head["name"] if head else "Squad",
            "formation": head["formation"] if head else "f442",
            "captainItemId": int(head["captain_item_id"]) if head else 0,
            "slots": [
                {"index": int(row["slot_index"]), "itemId": int(row["item_id"]), "position": row["position"]}
                for row in slots
            ],
        }

    def save_squad(
        self,
        squad_id: int,
        slots: Iterable[dict[str, Any]],
        *,
        formation: str = "f442",
        name: str = "Squad",
        captain_item_id: int = 0,
    ) -> dict[str, Any]:
        """Persist a squad exactly as the client sent it.

        Chemistry and star rating are computed client-side, so the server's job
        is storage and pile bookkeeping, not recalculation.
        """
        now = int(time.time())
        normalised = [
            (
                int(slot.get("index", position)),
                int(slot.get("itemId", 0) or 0),
                str(slot.get("position", "") or ""),
            )
            for position, slot in enumerate(slots)
        ]
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO squads (squad_id, persona_id, name, formation, captain_item_id, updated_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(squad_id, persona_id) DO UPDATE SET"
                " name=excluded.name, formation=excluded.formation,"
                " captain_item_id=excluded.captain_item_id, updated_at=excluded.updated_at",
                (squad_id, self.persona_id, name, formation, captain_item_id, now),
            )
            connection.execute(
                "DELETE FROM squad_slots WHERE squad_id=? AND persona_id=?", (squad_id, self.persona_id)
            )
            connection.executemany(
                "INSERT INTO squad_slots (squad_id, persona_id, slot_index, item_id, position)"
                " VALUES (?,?,?,?,?)",
                [(squad_id, self.persona_id, index, item_id, position)
                 for index, item_id, position in normalised],
            )
            # Cards in a squad live in the squad pile; everything else falls back
            # to the club so a removed player is not orphaned.
            in_squad = [item_id for _, item_id, _ in normalised if item_id]
            connection.execute(
                "UPDATE items SET pile=? WHERE persona_id=? AND pile=?",
                (DB_PILE_CLUB, self.persona_id, DB_PILE_SQUAD),
            )
            if in_squad:
                placeholders = ",".join("?" for _ in in_squad)
                connection.execute(
                    f"UPDATE items SET pile=? WHERE persona_id=? AND item_id IN ({placeholders})",
                    (DB_PILE_SQUAD, self.persona_id, *in_squad),
                )
        return self.squad(squad_id)

    def summary(self) -> dict[str, Any]:
        club = self.club()
        with closing(self._connect()) as connection:
            counts = {
                row["pile"]: int(row["n"])
                for row in connection.execute(
                    "SELECT pile, COUNT(*) AS n FROM items WHERE persona_id=? GROUP BY pile",
                    (self.persona_id,),
                )
            }
        return {
            "club": club.get("name"),
            "established": bool(club.get("established")),
            "coins": int(club.get("coins", 0)),
            "items": counts,
            "totalItems": sum(counts.values()),
            "catalog": self.catalog.summary(),
        }
