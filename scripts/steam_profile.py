#!/usr/bin/env python3
"""
steam_profile.py — Steam Profile Scanner
Fetches owned games, recently played, and playtime data via Steam Web API.
Saves to memory/steam-profile.json and appends to memory/steam-games-history.json.
"""

import json
import logging
import os
import sys
import time

from steam_common import (resolve_api_key, mask_key, fetch_json,
                          MEMORY_DIR, CONFIG_PATH, PROFILE_PATH, HISTORY_PATH)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("steam_profile")

def load_config():
    os.makedirs(MEMORY_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        log.error("Config not found at %s. Run setup.sh first.", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)

def fetch_owned_games(api_key, steam_id):
    """Fetch owned games list from Steam API."""
    url = (
        f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
        f"?key={api_key}&steamid={steam_id}"
        f"&include_appinfo=true&include_played_free_games=true"
    )
    data = fetch_json(url, timeout=30)
    if data and "response" in data and "games" in data["response"]:
        return data["response"]
    return None

def fetch_recently_played(api_key, steam_id):
    """Fetch recently played games."""
    url = (
        f"https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/"
        f"?key={api_key}&steamid={steam_id}&count=50"
    )
    data = fetch_json(url, timeout=30)
    if data and "response" in data:
        return data["response"]
    return None

def save_profile(games_response, recent_response):
    """Save profile data with timestamp."""
    profile = {
        "timestamp": int(time.time()),
        "timestamp_readable": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "game_count": 0,
        "total_playtime_hours": 0,
        "games": [],
        "recently_played": []
    }

    if games_response:
        games = games_response.get("games", [])
        profile["game_count"] = games_response.get("game_count", len(games))
        total_minutes = sum(g.get("playtime_forever", 0) for g in games)
        profile["total_playtime_hours"] = round(total_minutes / 60, 1)

        for g in games:
            if "appid" not in g:
                continue
            profile["games"].append({
                "appid": g["appid"],
                "name": g.get("name", "Unknown"),
                "playtime_forever_minutes": g.get("playtime_forever", 0),
                "playtime_2weeks_minutes": g.get("playtime_2weeks", 0),
                "playtime_windows_minutes": g.get("playtime_windows_forever", 0),
                "playtime_mac_minutes": g.get("playtime_mac_forever", 0),
                "playtime_linux_minutes": g.get("playtime_linux_forever", 0),
                "rtime_last_played": g.get("rtime_last_played", 0),
                "has_community_visible_stats": g.get("has_community_visible_stats", False),
            })

    if recent_response:
        recent_games = recent_response.get("games", [])
        total_recent = recent_response.get("total_count", 0)
        profile["recent_total_count"] = total_recent
        for g in recent_games:
            if "appid" not in g:
                continue
            profile["recently_played"].append({
                "appid": g["appid"],
                "name": g.get("name", "Unknown"),
                "playtime_2weeks_minutes": g.get("playtime_2weeks", 0),
                "playtime_forever_minutes": g.get("playtime_forever", 0),
            })

    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)
    log.info("Profile saved: %d games, %s total playtime",
             profile["game_count"], f"{profile['total_playtime_hours']}h")

    # Append to history
    history = {"updates": []}
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                pass

    history["updates"].append({
        "timestamp": profile["timestamp"],
        "timestamp_readable": profile["timestamp_readable"],
        "game_count": profile["game_count"],
        "total_playtime_hours": profile["total_playtime_hours"],
    })
    history["updates"] = history["updates"][-52:]  # cap: ~1 year of weekly runs
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)

    return profile

def main():
    dry_run = "--dry-run" in sys.argv

    config = load_config()
    api_key = resolve_api_key(config)
    steam_id = config.get("steamId", "")

    if not api_key or not steam_id:
        log.error("Missing Steam API key (set STEAM_API_KEY env or memory/.secrets/steam.env) or steamId in config.")
        sys.exit(1)

    if dry_run:
        log.info("DRY RUN: Would fetch profile for SteamID %s", steam_id)
        log.info("API key: %s", mask_key(api_key))
        log.info("Target files: %s, %s", PROFILE_PATH, HISTORY_PATH)
        return

    log.info("Fetching owned games for SteamID %s...", steam_id)
    games_resp = fetch_owned_games(api_key, steam_id)

    log.info("Fetching recently played games...")
    recent_resp = fetch_recently_played(api_key, steam_id)

    if not games_resp and not recent_resp:
        log.error("Could not fetch any data from Steam API. Check your API key and Steam ID.")
        sys.exit(1)

    profile = save_profile(games_resp, recent_resp)
    log.info("Done. %d games found, %s total playtime.",
             profile["game_count"], f"{profile['total_playtime_hours']}h")

if __name__ == "__main__":
    main()
