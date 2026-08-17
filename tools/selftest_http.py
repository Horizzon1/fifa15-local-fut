"""Exercise the FUT HTTP surface the way the client does.

Runs against an already-running server, so it proves the real wire behaviour
rather than calling the handlers directly.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.stdout.reconfigure(encoding="utf-8")

from config import ServerConfig  # noqa: E402

BASE = f"http://127.0.0.1:{ServerConfig().fut_http_port}"
UT = "/ut/game/fifa15"

failures: list[str] = []


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        return {"_status": exc.code}
    except Exception as exc:
        return {"_error": repr(exc)}


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def main() -> int:
    print("-- session --")
    health = call("GET", "/health")
    check("health responds", health.get("ok") is True, str(health.get("summary", {}).get("coins")))

    auth = call("POST", "/ut/auth", {"clientVersion": 15})
    check("auth issues a session id", bool(auth.get("sid")), auth.get("sid", ""))

    settings = call("GET", f"{UT}/settings")
    check("settings returns trade pile size", "maximumTradePileSize" in settings)

    print("\n-- club --")
    call("POST", f"{UT}/user/club", {"clubName": "Test Club FC"})
    user = call("GET", f"{UT}/user")
    check("club name persists", user.get("clubName") == "Test Club FC", str(user.get("clubName")))
    check("returning user flag set", user.get("returningUser") == 1)

    account = call("GET", f"{UT}/user/accountinfo")
    personas = account.get("userAccountInfo", {}).get("personas", [])
    check("accountinfo has one persona", len(personas) == 1)
    check("persona has a club list", bool(personas and personas[0].get("userClubList")))

    credits = call("GET", f"{UT}/user/credits")
    check("credits reports coins", isinstance(credits.get("credits"), int), str(credits.get("credits")))
    check("currencies array present", len(credits.get("currencies", [])) == 2)
    starting_coins = credits.get("credits", 0)

    print("\n-- store --")
    store = call("GET", f"{UT}/store")
    offers = store.get("purchase", [])
    check("store lists packs", len(offers) >= 8, f"{len(offers)} offers")
    check("offer has native contract keys",
          bool(offers) and all(k in offers[0] for k in
                               ("actionType", "packId", "packType", "purchasePackType", "quantity")))

    print("\n-- packs --")
    pack = call("POST", f"{UT}/purchased/items",
                {"packId": 302, "useCredits": 1, "usePreOrder": 0, "currency": "COINS"})
    cards = pack.get("itemData", [])
    check("rare gold pack returns 12 cards", len(cards) == 12, f"{len(cards)}")
    check("pack charged the club", pack.get("credits", 0) < starting_coins,
          f"{starting_coins} -> {pack.get('credits')}")
    if cards:
        card = cards[0]
        check("card carries a real name", bool(card.get("name")), card.get("name", ""))
        check("card has a rating", 1 <= card.get("rating", 0) <= 99, str(card.get("rating")))
        check("card has 6 attributes", len(card.get("attributeArray", [])) == 6)
        check("definitionId == assetId", card.get("definitionId") == card.get("assetId"))
        check("cardsubtypeid mirrors rareflag",
              all((c["cardsubtypeid"] == 1) == bool(c["rareflag"]) for c in cards))
        check("relational closure",
              all(c.get("teamid") and c.get("leagueId") and c.get("nation") for c in cards))
        print(f"       sample: {card['rating']} {card['preferredPosition']} {card['name']}")

    purchased = call("GET", f"{UT}/purchased/items")
    check("purchased pile holds the pack", len(purchased.get("itemData", [])) >= 12,
          str(len(purchased.get("itemData", []))))

    print("\n-- club items --")
    club_items = call("GET", f"{UT}/club?start=0&count=50")
    check("club returns items", len(club_items.get("itemData", [])) > 0,
          f"{club_items.get('total')} total")
    check("club paging fields present",
          all(k in club_items for k in ("itemData", "total", "count", "start")))

    gold_only = call("GET", f"{UT}/club?level=gold&count=50")
    check("gold filter works",
          all(c["rating"] >= 75 for c in gold_only.get("itemData", [])),
          f"{len(gold_only.get('itemData', []))} gold")

    keepers = call("GET", f"{UT}/club?position=GK&count=50")
    check("position filter works",
          all(c["preferredPosition"] == "GK" for c in keepers.get("itemData", [])),
          f"{len(keepers.get('itemData', []))} GK")

    stats = call("GET", f"{UT}/club/stats/year")
    check("club stats report players", stats.get("players", 0) > 0, str(stats.get("players")))

    print("\n-- squad --")
    pool = club_items.get("itemData", [])[:11]
    payload = {
        "formation": "f442",
        "squadName": "HTTP XI",
        "players": [
            {"index": i, "itemData": {"id": c["id"]}, "position": c["preferredPosition"]}
            for i, c in enumerate(pool)
        ],
    }
    saved = call("PUT", f"{UT}/squad/0", payload)
    check("squad saves", saved.get("squadName") == "HTTP XI", str(saved.get("squadName")))
    check("squad has 23 slots", len(saved.get("players", [])) == 23,
          str(len(saved.get("players", []))))
    filled = [p for p in saved.get("players", []) if p.get("itemData", {}).get("id")]
    check("squad kept 11 players", len(filled) == 11, str(len(filled)))

    reloaded = call("GET", f"{UT}/squad/0")
    check("squad reloads", reloaded.get("squadName") == "HTTP XI")
    refilled = [p for p in reloaded.get("players", []) if p.get("itemData", {}).get("id")]
    check("squad reload keeps 11", len(refilled) == 11, str(len(refilled)))

    squad_list = call("GET", f"{UT}/squad/list")
    check("squad list responds", "squad" in squad_list)

    print("\n-- transfer market --")
    search = call("GET", f"{UT}/transfermarket?start=0&count=12&lev=gold")
    auctions = search.get("auctionInfo", [])
    check("market returns listings", len(auctions) == 12, f"{len(auctions)}")
    check("market total is large", search.get("total", 0) > 100, str(search.get("total")))
    if auctions:
        first = auctions[0]
        check("auction has pricing",
              first.get("buyNowPrice", 0) > 0 and first.get("startingBid", 0) > 0,
              f"buy {first.get('buyNowPrice')} / start {first.get('startingBid')}")
        check("auction carries item data", bool(first.get("itemData", {}).get("name")),
              first.get("itemData", {}).get("name", ""))
        check("auction has expiry aliases",
              all(k in first for k in ("expires", "EXPIRE_TIME", "expireTime")))
        check("gold search returns gold cards",
              all(a["itemData"]["rating"] >= 75 for a in auctions),
              f"lowest {min(a['itemData']['rating'] for a in auctions)}")
        prices = [a["buyNowPrice"] for a in auctions]
        check("prices vary between copies", len(set(prices)) > 1, f"{len(set(prices))} distinct")

    filtered = call("GET", f"{UT}/transfermarket?start=0&count=10&pos=ST&lev=gold")
    check("market position filter",
          all(a["itemData"]["preferredPosition"] == "ST" for a in filtered.get("auctionInfo", [])),
          f"{len(filtered.get('auctionInfo', []))} strikers")

    named = call("GET", f"{UT}/transfermarket?start=0&count=5&name=Messi")
    check("market name search", len(named.get("auctionInfo", [])) > 0,
          str([a["itemData"]["name"] for a in named.get("auctionInfo", [])][:3]))

    print("\n-- buying --")
    if auctions:
        target = auctions[0]
        before = call("GET", f"{UT}/user/credits").get("credits", 0)
        bought = call("PUT", f"{UT}/trade/{target['tradeId']}/bid",
                      {"buyNowPrice": target["buyNowPrice"]})
        check("buy now succeeds", "reason" not in bought, str(bought.get("reason", "")))
        after = call("GET", f"{UT}/user/credits").get("credits", 0)
        check("buy now charged coins", after == before - target["buyNowPrice"],
              f"{before} -> {after} (price {target['buyNowPrice']})")

    print("\n-- selling --")
    sellable = call("GET", f"{UT}/club?start=0&count=5").get("itemData", [])
    if sellable:
        item = sellable[0]
        listed = call("POST", f"{UT}/auctionhouse",
                      {"itemData": {"id": item["id"]}, "startingBid": 300,
                       "buyNowPrice": 900, "duration": 3600})
        check("listing created", listed.get("tradeId", 0) > 0, str(listed.get("tradeId")))
        check("listing is owned by us", listed.get("tradeOwner") is True)
        check("listed auction carries item data",
              bool(listed.get("itemData", {}).get("name")),
              listed.get("itemData", {}).get("name", ""))

        pile = call("GET", f"{UT}/tradepile")
        check("trade pile shows the listing", len(pile.get("auctionInfo", [])) >= 1,
              str(len(pile.get("auctionInfo", []))))
        check("no closed auction has empty itemData",
              all(a.get("itemData") for a in pile.get("auctionInfo", [])))

        removed = call("DELETE", f"{UT}/auctionhouse/{listed.get('tradeId')}")
        check("listing can be removed", removed.get("tradeId") == listed.get("tradeId"))

    print("\n-- misc routes --")
    for path in (f"{UT}/hub", f"{UT}/userdata", f"{UT}/clientdata/pileSize",
                 f"{UT}/season/user", f"{UT}/tournament", f"{UT}/watchlist"):
        response = call("GET", path)
        check(f"{path} responds", "_error" not in response)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S):"))
    for failure in failures:
        print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
