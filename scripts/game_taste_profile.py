#!/usr/bin/env python3
"""
game_taste_profile.py — Taste Profile Builder
Analyzes top-played Steam games, fetches store metadata, builds weighted genre/tag profile.
Saves to memory/steam-taste-profile.json.
"""

import json
import logging
import os
import sys
import time

from steam_common import (fetch_app_details, fetch_steamspy_tags, atomic_write_json,
                          PROFILE_PATH, TASTE_PATH, CONFIG_PATH, MANUAL_PATH)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("taste_profile")

MAX_GAMES_TO_ANALYZE = 25
ABANDON_THRESHOLD_MINUTES = 120  # <2h
ABANDON_AGE_DAYS = 30

# Known non-game software to exclude from taste analysis.
# 629520 = Soundpad (microphone soundboard) — ~1089h here, ~44% of all playtime; without this
# it dominates the genre profile with "Audio Production"/"Utilities".
NON_GAME_APPIDS = {629520, 1639250, 431960, 2238520, 1329410, 738170, 2389590, 893510, 527230, 2097850}
NON_GAME_NAMES_LOWER = {"soundpad", "crosshair x", "wallpaper engine", "3dmark", "smart game booster",
                       "lossless scaling", "nvcleanstall", "fps monitor", "msi afterburner",
                       "hwinfo"}

# Rate limit between Steam Store calls (the store API 429s easily).
REQUEST_DELAY_SECONDS = 1.5

# Recency multiplier on a non-Steam game's capped playtime, so an old obsession counts less.
RECENCY_MULT = {"active": 1.0, "occasional": 0.5, "retired": 0.25}

def has_new_games_since_last_build():
    """Check if new games were added since last taste profile build."""
    if not os.path.exists(TASTE_PATH) or not os.path.exists(PROFILE_PATH):
        return True
    try:
        with open(TASTE_PATH) as f:
            taste = json.load(f)
        with open(PROFILE_PATH) as f:
            profile = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return True

    last_build = taste.get("timestamp", 0)
    profile_ts = profile.get("timestamp", 0)
    return profile_ts > last_build

def classify_genre(genres):
    """Clean up genre names."""
    return [g["description"] for g in genres] if genres else []

def extract_tags(app_data):
    """Extract real taste signal: Steam community tags (data.tags), highest-voted first.

    The community tags are user-generated descriptors ("Open World", "Souls-like",
    "Sandbox", ...) and carry actual taste signal. The previous implementation read
    data.categories instead — platform metadata ("Family Sharing", "Single-player",
    "Steam Achievements") shared by nearly every game, i.e. noise. If the appdetails
    payload doesn't include tags, we return [] (clean) rather than falling back to
    categories.
    """
    raw = app_data.get("tags", {})
    if isinstance(raw, dict) and raw:
        # dict of {tag_name: vote_count} — order by votes desc
        return [t for t, _ in sorted(raw.items(), key=lambda kv: -(kv[1] or 0))]
    if isinstance(raw, list):
        return [t for t in raw if t]
    return []

def extract_categories(app_data):
    """Extract Steam platform categories — used only as capability flags
    (e.g. 'VR Only', 'Co-op', 'Single-player'), never mixed into the tag weights."""
    return [c.get("description", "") for c in app_data.get("categories", []) if c.get("description")]

def build_taste_profile(profile_data, force=False):
    """Build weighted taste profile from game data."""
    games = profile_data.get("games", [])
    now = int(time.time())

    # Sort by playtime descending
    sorted_games = sorted(games, key=lambda g: g.get("playtime_forever_minutes", 0), reverse=True)

    top_games = sorted_games[:MAX_GAMES_TO_ANALYZE]
    total_playtime_minutes = sum(g.get("playtime_forever_minutes", 0) for g in sorted_games)

    # Gather store metadata for top games (with rate limiting)
    enriched_games = []
    genre_weights = {}
    tag_weights = {}
    top_game_names = []

    log.info("Fetching store details for top %d games (this takes a moment)...", len(top_games))

    genres_by_appid = {}
    for i, game in enumerate(top_games):
        if "appid" not in game:
            continue
        appid = game["appid"]
        name = game.get("name", "Unknown")
        playtime = game.get("playtime_forever_minutes", 0)

        # Skip non-game software
        if appid in NON_GAME_APPIDS or name.lower() in NON_GAME_NAMES_LOWER:
            log.info("[%d/%d] %s (%dh) — SKIPPED (non-game software)", i + 1, len(top_games), name, playtime // 60)
            enriched_games.append({
                "appid": appid,
                "name": name,
                "playtime_minutes": playtime,
                "genres": [],
                "tags": [],
                "categories": [],
                "skipped": True
            })
            continue

        log.info("[%d/%d] %s (%dh)", i + 1, len(top_games), name, playtime // 60)

        details = fetch_app_details(appid)
        if details:
            genres = classify_genre(details.get("genres", []))
            # Community tags come from SteamSpy (appdetails has none); fall back to any
            # tags the store payload happens to include.
            tags = fetch_steamspy_tags(appid) or extract_tags(details)
            categories = extract_categories(details)
            genres_by_appid[appid] = genres

            enriched_games.append({
                "appid": appid,
                "name": name,
                "playtime_minutes": playtime,
                "genres": genres,
                "tags": tags,
                "categories": categories
            })

            # Weight by playtime proportion
            weight = playtime / max(total_playtime_minutes, 1)
            for g in genres:
                genre_weights[g] = genre_weights.get(g, 0) + weight
            for t in tags:
                tag_weights[t] = tag_weights.get(t, 0) + weight

            if playtime > 60:
                top_game_names.append(name)
        else:
            log.warning("No store details for %s (appid %d) — left without genres/tags", name, appid)

        # Rate limit
        time.sleep(REQUEST_DELAY_SECONDS)

    # Blend in non-Steam games so games played on other platforms still shape taste.
    # Each game's playtime is CAPPED at the top Steam game's hours and scaled by a recency
    # multiplier, so one old 1000h obsession can't dominate current taste.
    manual_games = []
    if os.path.exists(MANUAL_PATH):
        try:
            with open(MANUAL_PATH, encoding="utf-8") as f:
                manual_games = json.load(f).get("games", [])
        except json.JSONDecodeError:
            log.warning("Corrupt %s — skipping non-Steam games", MANUAL_PATH)
    if manual_games:
        cap_minutes = max((e["playtime_minutes"] for e in enriched_games if not e.get("skipped")), default=0)
        for mg in manual_games:
            raw_minutes = int(mg.get("playtime_hours", 0) * 60)
            mult = RECENCY_MULT.get(mg.get("recency", "active"), 1.0)
            effective = (min(raw_minutes, cap_minutes) if cap_minutes else raw_minutes) * mult
            genres = mg.get("genres", [])
            tags = mg.get("tags", [])
            weight = effective / max(total_playtime_minutes, 1)
            for g in genres:
                genre_weights[g] = genre_weights.get(g, 0) + weight
            for t in tags:
                tag_weights[t] = tag_weights.get(t, 0) + weight
            enriched_games.append({
                "appid": mg.get("appid"),
                "name": mg.get("name"),
                "playtime_minutes": raw_minutes,
                "effective_minutes": round(effective),
                "genres": genres,
                "tags": tags,
                "source": "manual",
                "platform": mg.get("platform", "unknown"),
                "recency": mg.get("recency", "active"),
            })
            if effective > 60:
                top_game_names.append(mg.get("name"))
        log.info("Blended %d non-Steam game(s) (cap=%dh, recency-adjusted)",
                 len(manual_games), cap_minutes // 60)

    # Normalize weights
    total_genre_weight = sum(genre_weights.values()) or 1
    genre_profile = {g: round(w / total_genre_weight, 4) for g, w in
                     sorted(genre_weights.items(), key=lambda x: -x[1])[:10]}

    total_tag_weight = sum(tag_weights.values()) or 1
    tag_profile = {t: round(w / total_tag_weight, 4) for t, w in
                   sorted(tag_weights.items(), key=lambda x: -x[1])[:15]}

    # Detect abandoned games. Genres are joined from enriched_games when available
    # (only the top-25 are enriched), else left empty — the recommender's abandonment
    # signal (Phase 4) keys off these genres.
    abandoned = []
    for game in sorted_games:
        if "appid" not in game:
            continue
        playtime = game.get("playtime_forever_minutes", 0)
        rtime = game.get("rtime_last_played", 0)
        name = game.get("name", "Unknown")

        if playtime < ABANDON_THRESHOLD_MINUTES and rtime > 0:
            last_played_age_days = (now - rtime) / 86400
            if last_played_age_days > ABANDON_AGE_DAYS:
                abandoned.append({
                    "name": name,
                    "appid": game["appid"],
                    "playtime_minutes": playtime,
                    "days_since_played": round(last_played_age_days),
                    "genres": genres_by_appid.get(game["appid"], [])
                })

    taste = {
        "timestamp": int(time.time()),
        "timestamp_readable": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "game_count": profile_data.get("game_count", 0),
        "total_playtime_hours": profile_data.get("total_playtime_hours", 0),
        "top_games": top_game_names[:10],
        "genres": genre_profile,
        "tags": tag_profile,
        "abandoned_games": abandoned[:10],
        "enriched_games": enriched_games
    }

    atomic_write_json(TASTE_PATH, taste)

    log.info("Taste profile built: %d genres, %d tags tracked", len(genre_profile), len(tag_profile))
    log.info("Top genres: %s", ", ".join(list(genre_profile.keys())[:5]))
    log.info("Abandoned games detected: %d", len(abandoned))

    return taste

def main():
    force = "--force" in sys.argv

    if not os.path.exists(PROFILE_PATH):
        log.error("No profile data found at %s. Run steam_profile.py first.", PROFILE_PATH)
        sys.exit(1)

    try:
        with open(PROFILE_PATH) as f:
            profile_data = json.load(f)
    except json.JSONDecodeError as e:
        log.error("Corrupt profile JSON at %s: %s. Re-run steam_profile.py.", PROFILE_PATH, e)
        sys.exit(1)

    if not force and os.path.exists(TASTE_PATH) and not has_new_games_since_last_build():
        log.info("No new games since last taste profile build. Use --force to rebuild anyway.")
        with open(TASTE_PATH) as f:
            taste = json.load(f)
        print(json.dumps(taste, indent=2))
        return

    build_taste_profile(profile_data, force=force)

if __name__ == "__main__":
    main()
