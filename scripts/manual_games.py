#!/usr/bin/env python3
"""
manual_games.py — Manage non-Steam games (Epic, GOG, console, etc.) so games the user
has sunk real time into still shape their taste profile and recommendations.

`memory/manual-games.json` is the source of truth. `game_taste_profile.py` weights these
into the genre/tag profile (capped + recency-adjusted), `build_gaming_profile.py` adds them
to the interview queue, and `game_recommender.py` excludes them from recommendations.

Agent-callable (prints one JSON object to stdout). Data goes to $GAMING_PROFILE_HOME.

  add    --name "Rocket League" --hours 1000 [--platform epic] [--recency occasional]
         [--genres "Sports,Racing"] [--tags "Competitive,Multiplayer"] [--notes "..."]
  list
  remove --name "Rocket League"

On `add`, if --genres/--tags are omitted the game is looked up on Steam (storesearch ->
appdetails + SteamSpy) to auto-fill them. Games delisted from Steam (e.g. Rocket League)
won't resolve — pass --genres/--tags, or leave empty and interview the game later.
"""

import json
import os
import sys
import urllib.parse

from steam_common import (fetch_json, fetch_app_details, fetch_steamspy_tags,
                          atomic_write_json, normalize_game_key, MANUAL_PATH)

VALID_RECENCY = ("active", "occasional", "retired")


def _load():
    if not os.path.exists(MANUAL_PATH):
        return {"games": []}
    try:
        with open(MANUAL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {"games": []}


def _save(data):
    atomic_write_json(MANUAL_PATH, data)


def resolve_on_steam(name):
    """Return (appid, genres, tags) if the game is found on the Steam store, else (None, [], [])."""
    term = urllib.parse.quote_plus(name)
    data = fetch_json(f"https://store.steampowered.com/api/storesearch/?term={term}&cc=us&l=en")
    items = (data or {}).get("items", [])
    if not items:
        return None, [], []
    appid = items[0].get("id")
    if not appid:
        return None, [], []
    details = fetch_app_details(appid)
    genres = [g["description"] for g in (details or {}).get("genres", [])] if details else []
    tags = fetch_steamspy_tags(appid)
    return appid, genres, tags


def op_add(args):
    name = args.get("name")
    if not name or args.get("hours") is None:
        return {"ok": False, "error": "add requires --name and --hours"}
    recency = (args.get("recency") or "active").lower()
    if recency not in VALID_RECENCY:
        return {"ok": False, "error": f"--recency must be one of {VALID_RECENCY}"}

    genres = [g.strip() for g in args["genres"].split(",") if g.strip()] if args.get("genres") else []
    tags = [t.strip() for t in args["tags"].split(",") if t.strip()] if args.get("tags") else []
    appid = None
    enriched = False
    if not genres and not tags:
        appid, genres, tags = resolve_on_steam(name)
        enriched = bool(genres or tags)

    entry = {
        "name": name,
        "playtime_hours": float(args["hours"]),
        "platform": args.get("platform", "unknown"),
        "recency": recency,
        "appid": appid,
        "genres": genres,
        "tags": tags,
        "notes": args.get("notes", ""),
        "mechanics_loved": [m.strip() for m in args["mechanics"].split(",")] if args.get("mechanics") else [],
    }

    data = _load()
    key = normalize_game_key(name)
    games = [g for g in data.get("games", []) if normalize_game_key(g["name"]) != key]
    games.append(entry)
    data["games"] = games
    _save(data)

    return {"ok": True, "added": name, "enriched_from_steam": enriched,
            "appid": appid, "genres": genres, "tags": tags[:8], "recency": recency,
            "total_manual_games": len(games),
            "note": None if (genres or tags) else
            "No genres/tags (not found on Steam) — pass --genres/--tags or interview this game."}


def op_list(_args):
    data = _load()
    return {"ok": True, "count": len(data.get("games", [])),
            "games": [{"name": g["name"], "hours": g["playtime_hours"], "platform": g.get("platform"),
                       "recency": g.get("recency"), "genres": g.get("genres", [])[:4],
                       "enriched": bool(g.get("genres") or g.get("tags"))}
                      for g in data.get("games", [])]}


def op_remove(args):
    name = args.get("name")
    if not name:
        return {"ok": False, "error": "remove requires --name"}
    data = _load()
    key = normalize_game_key(name)
    before = len(data.get("games", []))
    data["games"] = [g for g in data.get("games", []) if normalize_game_key(g["name"]) != key]
    _save(data)
    removed = before - len(data["games"])
    return {"ok": removed > 0, "removed": name if removed else None, "remaining": len(data["games"])}


OPS = {"add": op_add, "list": op_list, "remove": op_remove}
FLAGS = {"--name": "name", "--hours": "hours", "--platform": "platform", "--recency": "recency",
         "--genres": "genres", "--tags": "tags", "--notes": "notes", "--mechanics": "mechanics"}


def main(argv):
    if len(argv) < 2 or argv[1] not in OPS:
        print(json.dumps({"ok": False, "error": f"Usage: manual_games.py {{{'|'.join(OPS)}}} [flags]"}))
        return 1
    try:
        args = {}
        for flag, key in FLAGS.items():
            if flag in argv:
                args[key] = argv[argv.index(flag) + 1]
        result = OPS[argv[1]](args)
    except (ValueError, IndexError) as e:
        result = {"ok": False, "error": f"Bad arguments: {e}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
