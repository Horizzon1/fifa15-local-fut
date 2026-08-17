"""Self-test for the identity store: catalog, grants, packs, squads, wire format.

Runs without the game, so regressions in the data model surface immediately.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.stdout.reconfigure(encoding="utf-8")

from identity import (  # noqa: E402
    DB_PILE_CLUB,
    PACK_CATALOG,
    PILE_PURCHASED,
    IdentityStore,
    PlayerCatalog,
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "ok  " if condition else "FAIL"
    print(f"  [{status}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def main() -> int:
    catalog = PlayerCatalog(ROOT / "server" / "fifa15-player-catalog.json")
    print(f"catalog: {len(catalog)} players {catalog.summary()}")
    check("catalog has a full squad's worth of every tier",
          all(count > 100 for count in catalog.summary().values()))

    database = ROOT / "state" / "selftest.sqlite3"
    if database.exists():
        database.unlink()
    store = IdentityStore(database, catalog)

    print("\n-- coins --")
    granted = store.grant_once("selftest-100m", 100_000_000, "test coins")
    check("grant applies", granted["applied"] and granted["balance"] == 100_000_000, str(granted["balance"]))
    repeat = store.grant_once("selftest-100m", 100_000_000, "test coins")
    check("grant is idempotent", not repeat["applied"] and repeat["balance"] == 100_000_000)

    print("\n-- packs --")
    for pack in PACK_CATALOG:
        before = store.coins()
        result = store.open_pack(pack.pack_id)
        check(
            f"{pack.name}: {pack.players} cards, charged {pack.price}",
            len(result.get("itemData", [])) == pack.players
            and result.get("balance") == before - pack.price,
            f"got {len(result.get('itemData', []))} cards, balance {result.get('balance')}",
        )
        keepers = [c for c in result["itemData"] if c["preferredPosition"] == "GK"]
        check(f"{pack.name}: contains a goalkeeper", len(keepers) >= 1, f"{len(keepers)} GK")
        if pack.guaranteed_rare:
            rares = [c for c in result["itemData"] if c["rareflag"]]
            check(
                f"{pack.name}: at least {pack.guaranteed_rare} rare",
                len(rares) >= pack.guaranteed_rare,
                f"{len(rares)} rare",
            )

    print("\n-- gold pack quality --")
    gold = store.open_pack(302)
    ratings = sorted((c["rating"] for c in gold["itemData"]), reverse=True)
    check("rare gold pack tops 75+", ratings[0] >= 75, f"best {ratings[0]}, ratings {ratings}")

    print("\n-- wire contract --")
    sample = gold["itemData"][0]
    order = list(sample)
    check("id is first", order[0] == "id")
    check("preferredPosition is early", order.index("preferredPosition") < 8, f"index {order.index('preferredPosition')}")
    check("definitionId == assetId", sample["definitionId"] == sample["assetId"])
    check("resourceId == assetId", sample["resourceId"] == sample["assetId"])
    check("cardsubtypeid mirrors rareflag",
          all((c["cardsubtypeid"] == 1) == bool(c["rareflag"]) for c in gold["itemData"]))
    check("resourceGameYear is 2015", sample["resourceGameYear"] == 2015)
    check("lowercase teamid present", "teamid" in sample and "teamId" in sample)
    check("pile is purchased", sample["pile"] == PILE_PURCHASED, str(sample["pile"]))
    check("six attributes", len(sample["attributeArray"]) == 6, str(sample["attributeArray"]))
    check("attributes in range", all(0 <= v <= 99 for v in sample["attributeArray"]))
    check("relational closure",
          all(c["teamid"] and c["leagueId"] and c["nation"] for c in gold["itemData"]),
          "no orphan team/league/nation")

    print("\n-- goalkeeper card template --")
    keeper = next((c for c in gold["itemData"] if c["preferredPosition"] == "GK"), None)
    if keeper:
        check("GK has diving-style stats", keeper["attributeArray"][0] > 0, str(keeper["attributeArray"]))

    print("\n-- squads --")
    club_items = store.items()
    slots = [{"index": i, "itemId": item["item_id"], "position": item["position"]}
             for i, item in enumerate(club_items[:11])]
    store.save_squad(0, slots, formation="f442", name="Test XI")
    reloaded = store.squad(0)
    check("squad persists 11 slots", len(reloaded["slots"]) == 11, str(len(reloaded["slots"])))
    check("squad name persists", reloaded["name"] == "Test XI")
    squad_pile = store.items("squad")
    check("squad members move to the squad pile", len(squad_pile) == 11, str(len(squad_pile)))

    # Reopen from disk to prove persistence across sessions.
    reopened = IdentityStore(database, catalog)
    check("squad survives a reopen", len(reopened.squad(0)["slots"]) == 11)
    check("coins survive a reopen", reopened.coins() == store.coins())

    print("\n-- quick sell --")
    # Pack winnings land in the pending pile, so sell from there.
    spare = [i["item_id"] for i in reopened.items("pending")[:3]]
    check("have cards to sell", len(spare) == 3, f"{len(spare)} found")
    before = reopened.coins()
    sold = reopened.quick_sell(spare)
    check("quick sell pays out", sold["coins"] > 0 and sold["balance"] == before + sold["coins"],
          f"+{sold['coins']} coins")
    check("sold cards leave the club",
          bool(spare) and all(reopened.item(i) is None for i in spare))

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
