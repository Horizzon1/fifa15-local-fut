"""Build the FIFA 15 player catalog from the game's own client database.

The catalog must be extracted from `cards_ng_db.db` rather than scraped, so
every id in it is an id the installed client already knows. A player the client
cannot resolve renders as a blank card, and orphan relational rows crash FUT.

Output: `server/fifa15-player-catalog.json`
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fifa_db import read_field, record_bytes, string_table
from inspect_db import load

# FIFA's internal position enum, shared by preferredposition* and
# teamplayerlinks.position.
POSITIONS = {
    0: "GK", 1: "SW", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB", 7: "LB",
    8: "LWB", 9: "RDM", 10: "CDM", 11: "LDM", 12: "RM", 13: "RCM", 14: "CM",
    15: "LCM", 16: "LM", 17: "RAM", 18: "CAM", 19: "LAM", 20: "RF", 21: "CF",
    22: "LF", 23: "RW", 24: "RS", 25: "ST", 26: "LS", 27: "LW",
}

# The FUT card tier a rating falls into. Bronze/silver/gold are the retail bands.
def tier_for(rating: int) -> str:
    if rating >= 75:
        return "gold"
    if rating >= 65:
        return "silver"
    return "bronze"


def column(db, table, name):
    field = table.by_name.get(name.lower())
    if field is None:
        raise KeyError(f"{table.name}.{name} not found")
    return field


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", type=Path, default=Path(r"F:\Games\FIFA 15"))
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parent.parent / "server" / "fifa15-player-catalog.json",
    )
    args = parser.parse_args()

    db, _descriptor, db_source, meta_source, _raw = load(args.game_root, "cards")
    print(f"# source: {db_source}")

    # ---- string tables -------------------------------------------------
    names = string_table(db, "playernames", ("nameid", "playernameid", "id"), ("name", "playername"))
    print(f"# playernames: {len(names)}")

    team_names: dict[int, str] = {}
    teams_table = db.table("teams")
    if teams_table is not None:
        tid = column(db, teams_table, "teamid")
        tname = next(
            (teams_table.by_name[n] for n in ("teamname", "name") if n in teams_table.by_name), None
        )
        if tname is not None:
            if tname.db_type in (13, 14):
                team_names = string_table(db, "teams", ("teamid",), (tname.name.lower(),))
            else:
                for index in range(teams_table.valid_records_count):
                    rec = record_bytes(db, teams_table, index)
                    team_names[int(read_field(rec, tid))] = str(read_field(rec, tname))
    print(f"# teams: {len(team_names)}")

    nation_names: dict[int, str] = {}
    nations_table = db.table("nations")
    if nations_table is not None:
        nfield = next(
            (nations_table.by_name[n] for n in ("nationname", "name") if n in nations_table.by_name), None
        )
        if nfield is not None:
            nid = column(db, nations_table, "nationid")
            if nfield.db_type in (13, 14):
                nation_names = string_table(db, "nations", ("nationid",), (nfield.name.lower(),))
            else:
                for index in range(nations_table.valid_records_count):
                    rec = record_bytes(db, nations_table, index)
                    nation_names[int(read_field(rec, nid))] = str(read_field(rec, nfield))
    print(f"# nations: {len(nation_names)}")

    # ---- relational links ----------------------------------------------
    # player -> team, and team -> league. FUT needs both for chemistry.
    player_team: dict[int, dict] = {}
    tpl = db.table("teamplayerlinks")
    if tpl is not None:
        f_pid, f_tid = column(db, tpl, "playerid"), column(db, tpl, "teamid")
        f_pos = tpl.by_name.get("position")
        f_jersey = tpl.by_name.get("jerseynumber")
        for index in range(tpl.valid_records_count):
            rec = record_bytes(db, tpl, index)
            pid = int(read_field(rec, f_pid))
            # A player can appear on club and national teams; keep the first
            # (club) link, which is what the FUT card is built from.
            if pid in player_team:
                continue
            player_team[pid] = {
                "teamid": int(read_field(rec, f_tid)),
                "position": int(read_field(rec, f_pos)) if f_pos else None,
                "jersey": int(read_field(rec, f_jersey)) if f_jersey else None,
            }
    print(f"# teamplayerlinks: {len(player_team)}")

    team_league: dict[int, int] = {}
    ltl = db.table("leagueteamlinks")
    if ltl is not None:
        f_lid, f_tid = column(db, ltl, "leagueid"), column(db, ltl, "teamid")
        for index in range(ltl.valid_records_count):
            rec = record_bytes(db, ltl, index)
            team_league.setdefault(int(read_field(rec, f_tid)), int(read_field(rec, f_lid)))
    print(f"# leagueteamlinks: {len(team_league)}")

    # ---- players --------------------------------------------------------
    players_table = db.table("players")
    if players_table is None:
        print("!! players table missing", file=sys.stderr)
        return 1

    want = [
        "playerid", "overallrating", "potential", "nationality", "preferredposition1",
        "preferredposition2", "preferredposition3", "firstnameid", "lastnameid",
        "commonnameid", "playerjerseynameid", "skillmoves", "weakfootabilitytypecode",
        "preferredfoot", "attackingworkrate", "defensiveworkrate", "height", "weight",
        "birthdate", "acceleration", "sprintspeed", "finishing", "shotpower", "longshots",
        "positioning", "volleys", "penalties", "shortpassing", "longpassing", "vision",
        "crossing", "curve", "freekickaccuracy", "dribbling", "ballcontrol", "agility",
        "balance", "reactions", "marking", "standingtackle", "slidingtackle",
        "interceptions", "headingaccuracy", "strength", "stamina", "aggression", "jumping",
        "gkdiving", "gkhandling", "gkkicking", "gkpositioning", "gkreflexes",
        "internationalrep",
    ]
    fields = {}
    missing = []
    for name in want:
        field = players_table.by_name.get(name)
        if field is None:
            missing.append(name)
        else:
            fields[name] = field
    if missing:
        print(f"# note: fields absent in this DB: {', '.join(missing)}")

    def name_for(row: dict) -> tuple[str, str, str]:
        first = names.get(row.get("firstnameid", 0), "")
        last = names.get(row.get("lastnameid", 0), "")
        common = names.get(row.get("commonnameid", 0), "")
        return first, last, common

    catalog = []
    tier_counts: dict[str, int] = defaultdict(int)
    position_counts: dict[str, int] = defaultdict(int)

    for index in range(players_table.valid_records_count):
        rec = record_bytes(players_table.__class__ and db, players_table, index)
        row = {key: int(read_field(rec, field)) for key, field in fields.items()}

        pid = row.get("playerid", 0)
        if pid <= 0:
            continue

        first, last, common = name_for(row)
        display = common or (f"{first} {last}".strip()) or last or first
        if not display:
            continue

        rating = row.get("overallrating", 0)
        tier = tier_for(rating)
        link = player_team.get(pid, {})
        teamid = link.get("teamid")
        pos_code = row.get("preferredposition1", 0)
        position = POSITIONS.get(pos_code, "SUB")

        tier_counts[tier] += 1
        position_counts[position] += 1

        entry = {
            "playerId": pid,
            # FUT card art and identity both key off the player id.
            "assetId": pid,
            "name": display,
            "firstName": first,
            "lastName": last,
            "commonName": common,
            "rating": rating,
            "tier": tier,
            "position": position,
            "positionCode": pos_code,
            "altPositions": [
                POSITIONS[p] for p in (row.get("preferredposition2", -1), row.get("preferredposition3", -1))
                if p in POSITIONS and p >= 0
            ],
            "nationId": row.get("nationality", 0),
            "nation": nation_names.get(row.get("nationality", 0), ""),
            "teamId": teamid,
            "team": team_names.get(teamid, "") if teamid else "",
            "leagueId": team_league.get(teamid) if teamid else None,
            "skillMoves": row.get("skillmoves", 0) + 1,
            "weakFoot": row.get("weakfootabilitytypecode", 0),
            "preferredFoot": row.get("preferredfoot", 1),
            "attackingWorkRate": row.get("attackingworkrate", 1),
            "defensiveWorkRate": row.get("defensiveworkrate", 1),
            "height": row.get("height", 0),
            "weight": row.get("weight", 0),
            "birthdate": row.get("birthdate", 0),
            "internationalRep": row.get("internationalrep", 1),
            "attributes": {
                key: row[key]
                for key in (
                    "acceleration", "sprintspeed", "finishing", "shotpower", "longshots",
                    "positioning", "volleys", "penalties", "shortpassing", "longpassing",
                    "vision", "crossing", "curve", "freekickaccuracy", "dribbling",
                    "ballcontrol", "agility", "balance", "reactions", "marking",
                    "standingtackle", "slidingtackle", "interceptions", "headingaccuracy",
                    "strength", "stamina", "aggression", "jumping", "gkdiving",
                    "gkhandling", "gkkicking", "gkpositioning", "gkreflexes",
                )
                if key in row
            },
        }
        catalog.append(entry)

    catalog.sort(key=lambda item: item["playerId"])

    document = {
        "game": "fifa15",
        "source": db_source,
        "descriptor": meta_source,
        "playerCount": len(catalog),
        "tierCounts": dict(sorted(tier_counts.items())),
        "positionCounts": dict(sorted(position_counts.items(), key=lambda kv: -kv[1])),
        "players": catalog,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n# wrote {args.output} : {len(catalog)} players")
    print(f"# tiers: {dict(sorted(tier_counts.items()))}")
    goalkeepers = position_counts.get("GK", 0)
    print(f"# goalkeepers: {goalkeepers}")
    print(f"# top rated: " + ", ".join(
        f"{p['name']}({p['rating']})" for p in sorted(catalog, key=lambda x: -x["rating"])[:8]
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
