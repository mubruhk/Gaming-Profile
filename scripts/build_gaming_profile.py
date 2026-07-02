#!/usr/bin/env python3
"""
build_gaming_profile.py — Canonical gaming-profile fuser.

Produces gaming-profile-v0.json (in the data dir) by fusing:
  - steam-profile.json         (library: playtime, recently played)
  - steam-taste-profile.json   (genre/tag weights, abandoned games)
  - manual-games.json          (non-Steam games — synced in, added to the interview queue)
  - existing interview overlay  (preferences + game_deep_dives) — PRESERVED across runs

Design rule: REFERENCE, don't duplicate. The full library stays in steam-profile.json; this file
stores derived summaries plus the interview overlay only. Re-running after a fresh Steam sync
refreshes `steam_summary` and `interview_progress.queue` without destroying interview answers.
"""

import json
import logging
import os
import sys
import time

from steam_common import (atomic_write_json, normalize_game_key,
                          PROFILE_PATH, TASTE_PATH, CONFIG_PATH, GAMING_PROFILE_PATH, MANUAL_PATH)
from game_taste_profile import NON_GAME_APPIDS, NON_GAME_NAMES_LOWER

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("build_profile")

DEEP_DIVE_MIN_MINUTES = 600  # >= 10h qualifies for a full deep-dive interview
DEEP_DIVE_MIN_HOURS = 10

EMPTY_ANSWERS = {
    "general_opinion": "", "mechanics_love": [], "mechanics_hate": [], "difficulty": "",
    "session_length": "", "story_vs_gameplay": "", "mood": [], "genre": "", "replay": "",
}


def _load(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        log.warning("Corrupt JSON at %s: %s", path, e)
        return default


def _is_non_game(appid, name):
    return appid in NON_GAME_APPIDS or (name or "").lower() in NON_GAME_NAMES_LOWER


def _empty_overlay():
    return {
        "preferences": {
            "mechanics_love": [], "mechanics_hate": [],
            "difficulty_tolerance": "", "session_length": "", "story_vs_gameplay": "",
            "mood_affinities": {}, "genre_affinities": {},
        },
        "game_deep_dives": {},
        "manual_non_steam_games": {},
    }


def _build_queue(games, completed_keys):
    """Prioritized interview backlog: real games not yet deep-dived, playtime desc.
    >=10h -> deep_dive, else quick."""
    queue = []
    eligible = 0
    for g in sorted(games, key=lambda x: x.get("playtime_forever_minutes", 0), reverse=True):
        appid = g.get("appid")
        name = g.get("name", "Unknown")
        if appid is None or _is_non_game(appid, name):
            continue
        minutes = g.get("playtime_forever_minutes", 0)
        mode = "deep_dive" if minutes >= DEEP_DIVE_MIN_MINUTES else "quick"
        if mode == "deep_dive":
            eligible += 1
        if normalize_game_key(name) in completed_keys:
            continue
        queue.append({"appid": appid, "name": name,
                      "playtime_hours": round(minutes / 60, 1), "mode": mode})
    return queue, eligible


def _load_manual_games():
    data = _load(MANUAL_PATH, {})
    return data.get("games", []) if data else []


def _migrate_manual_to_file(manual_non_steam_games):
    """One-time upgrade: seed manual-games.json (the source of truth) from a legacy
    manual_non_steam_games dict in an older profile, if the file doesn't exist yet."""
    if os.path.exists(MANUAL_PATH) or not manual_non_steam_games:
        return
    games = []
    for v in manual_non_steam_games.values():
        eng = (v.get("engagement", "") or "").lower()
        recency = "occasional" if ("occasional" in eng or "used to" in eng) else "active"
        games.append({
            "name": v.get("name", ""),
            "playtime_hours": v.get("playtime_hours", 0),
            "platform": v.get("platform", "unknown"),
            "recency": recency,
            "appid": v.get("appid"),
            "genres": v.get("genres", []),   # legacy genre strings may not be Steam taxonomy; left for user/interview
            "tags": v.get("tags", []),
            "notes": v.get("engagement", ""),
            "mechanics_loved": v.get("mechanics_loved", []),
        })
    atomic_write_json(MANUAL_PATH, {"games": games})
    log.info("Migrated %d manual non-Steam game(s) into %s", len(games), MANUAL_PATH)


def _manual_entries_dict(manual_games):
    """Render manual-games.json into the profile's manual_non_steam_games dict (keyed by name)."""
    out = {}
    for g in manual_games:
        out[normalize_game_key(g["name"])] = {
            "name": g["name"], "playtime_hours": g.get("playtime_hours", 0),
            "platform": g.get("platform", "unknown"), "recency": g.get("recency", "active"),
            "appid": g.get("appid"), "genres": g.get("genres", []), "tags": g.get("tags", []),
            "mechanics_loved": g.get("mechanics_loved", []), "notes": g.get("notes", ""),
        }
    return out


def _fold_manual_mechanics(prefs, manual_games):
    """Upsert each manual game's mechanics_loved into preferences.mechanics_love."""
    love = prefs.setdefault("mechanics_love", [])
    for g in manual_games:
        for kw in g.get("mechanics_loved", []):
            norm = normalize_game_key(kw)
            existing = next((o for o in love if normalize_game_key(o.get("keyword", "")) == norm), None)
            if existing:
                if g["name"] not in existing["source_games"]:
                    existing["source_games"].append(g["name"])
            else:
                love.append({"keyword": kw, "source_games": [g["name"]], "weight": 1.0})


def _append_manual_queue(queue, manual_games, completed_keys):
    """Add not-yet-interviewed manual games to the interview queue."""
    for g in manual_games:
        if normalize_game_key(g["name"]) in completed_keys:
            continue
        hours = g.get("playtime_hours", 0)
        queue.append({
            "appid": g.get("appid"), "name": g["name"], "playtime_hours": hours,
            "mode": "deep_dive" if hours >= DEEP_DIVE_MIN_HOURS else "quick",
            "platform": g.get("platform", "unknown"),
        })


def build():
    profile = _load(PROFILE_PATH)
    if not profile:
        log.error("No steam-profile.json — run steam_profile.py first.")
        sys.exit(1)
    taste = _load(TASTE_PATH, {})
    config = _load(CONFIG_PATH, {})
    games = profile.get("games", [])

    existing = _load(GAMING_PROFILE_PATH)
    if existing:
        overlay = {
            "preferences": existing.get("preferences", _empty_overlay()["preferences"]),
            "game_deep_dives": existing.get("game_deep_dives", {}),
            "manual_non_steam_games": existing.get("manual_non_steam_games", {}),
        }
        log.info("Preserving %d existing deep dives across rebuild", len(overlay["game_deep_dives"]))
    else:
        overlay = _empty_overlay()

    # Non-Steam games: manual-games.json is the source of truth. Seed it once from any legacy
    # manual_non_steam_games in an older profile, then sync that section from the file.
    _migrate_manual_to_file(overlay.get("manual_non_steam_games", {}))
    manual_games = _load_manual_games()
    overlay["manual_non_steam_games"] = _manual_entries_dict(manual_games)
    _fold_manual_mechanics(overlay["preferences"], manual_games)

    completed_keys = set(overlay["game_deep_dives"].keys())
    queue, eligible = _build_queue(games, completed_keys)
    _append_manual_queue(queue, manual_games, completed_keys)

    deep_done = sum(1 for d in overlay["game_deep_dives"].values() if d.get("mode") == "deep_dive")
    quick_done = sum(1 for d in overlay["game_deep_dives"].values() if d.get("mode") == "quick")

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    result = {
        "profile_version": "v0",
        "schema_updated": now,
        "user": {
            "name": config.get("userName", "You"),
            "steam_id": config.get("steamId", ""),
            "steam_vanity": config.get("steamVanityUrl", ""),
        },
        "sources": {
            "steam_profile": "memory/steam-profile.json",
            "taste_profile": "memory/steam-taste-profile.json",
        },
        "steam_summary": {
            "last_synced": profile.get("timestamp_readable", ""),
            "total_games": profile.get("game_count", len(games)),
            "total_playtime_hours": profile.get("total_playtime_hours", 0),
            "genre_weights": taste.get("genres", {}),
            "tag_weights": taste.get("tags", {}),
            "abandoned_games": taste.get("abandoned_games", []),
        },
        "preferences": overlay["preferences"],
        "game_deep_dives": overlay["game_deep_dives"],
        "manual_non_steam_games": overlay["manual_non_steam_games"],
        "interview_progress": {
            "deep_dive_eligible": eligible,
            "deep_dive_completed": deep_done,
            "quick_completed": quick_done,
            "last_interview": None,
            "queue": queue,
        },
    }

    atomic_write_json(GAMING_PROFILE_PATH, result)
    log.info("Wrote %s", GAMING_PROFILE_PATH)
    log.info("  %d games in library, %d deep-dive eligible (%d done), %d in interview queue",
             result["steam_summary"]["total_games"], eligible, deep_done, len(queue))
    return result


if __name__ == "__main__":
    build()
