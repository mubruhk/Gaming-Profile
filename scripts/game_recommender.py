#!/usr/bin/env python3
"""
game_recommender.py — Weekly Discovery Engine
Scores new/upcoming/relevant games against the taste profile.
Uses public Steam Store APIs (no API key needed).
Outputs recommendations to stdout in JSON format.
"""

import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

from steam_common import (fetch_json, fetch_steamspy_tags,
                          MEMORY_DIR, TASTE_PATH, PROFILE_PATH, STATE_PATH, CONFIG_PATH,
                          GAMING_PROFILE_PATH, MANUAL_PATH)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("game_recommender")

# Scoring weights — overridable via config["scoringWeights"]. Genre+tag stay the backbone;
# interview-derived signals are additive nudges plus two hard penalties (hated mechanics,
# abandonment risk) that can demote an on-genre game the user is likely to bounce off.
DEFAULT_WEIGHTS = {
    "GENRE_WEIGHT": 0.45, "TAG_WEIGHT": 0.25, "MECH_WEIGHT": 0.15, "MOOD_WEIGHT": 0.05,
    "REVIEW_WEIGHT": 0.10, "MECH_HATE_PEN": 0.15, "DIFF_BONUS": 0.05, "DIFF_PENALTY": 0.05,
    "STORY_BONUS": 0.05, "ABANDON_PEN": 0.10, "TOP_GENRE_BONUS": 0.05,
}
HARD_TAGS = {"difficult", "souls-like", "soulslike", "hardcore"}
STOPWORDS = {"the", "and", "for", "with", "you", "your", "this", "that", "are", "but",
             "all", "can", "out", "get", "has", "have", "will", "game", "games"}

def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return default if default is not None else {}

def _tokens(text):
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in STOPWORDS}

def fetch_featured_games():
    """Fetch featured categories from public Steam Store API."""
    url = "https://store.steampowered.com/api/featuredcategories"
    return fetch_json(url)

def fetch_new_releases():
    """Fetch new releases from Steam's featured API."""
    url = "https://store.steampowered.com/api/featured"
    data = fetch_json(url)
    if not data:
        return []
    new_releases = []
    for key in ["specials", "coming_soon", "top_sellers", "new_releases"]:
        items = data.get(key, [])
        for item in items:
            if isinstance(item, dict):
                appid = item.get("id")
                name = item.get("name")
                if appid and name:
                    new_releases.append({"appid": appid, "name": name})
    return new_releases

def fetch_top_sellers():
    """Fetch top sellers to find new popular games."""
    url = "https://store.steampowered.com/api/featuredcategories"
    data = fetch_json(url)
    if not data:
        return []
    all_candidates = []
    for cat_name, cat_data in data.items():
        if isinstance(cat_data, dict):
            items = cat_data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    appid = item.get("id")
                    name = item.get("name")
                    if appid and name:
                        all_candidates.append({"appid": appid, "name": name})
    return all_candidates

def fetch_app_details(appid, region="us"):
    """Fetch store details for a single app, priced in the configured region."""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc={region}&l=en"
    data = fetch_json(url, retries=2)
    if data and str(appid) in data and data[str(appid)].get("success"):
        return data[str(appid)]["data"]
    return None

def build_preference_index(gaming_profile):
    """Build a lightweight index of interview-derived signals for scoring.
    Returns empty-ish structures when no interview data exists, so scoring gracefully
    degrades to the corrected genre+tag baseline."""
    prefs = (gaming_profile or {}).get("preferences", {})
    love_kw, hate_kw = set(), set()
    for obj in prefs.get("mechanics_love", []):
        love_kw |= _tokens(obj.get("keyword", ""))
    for obj in prefs.get("mechanics_hate", []):
        hate_kw |= _tokens(obj.get("keyword", ""))

    mood_tokens = set()
    for mood_key in prefs.get("mood_affinities", {}):
        mood_tokens |= _tokens(mood_key)

    # genres the user is statistically prone to abandon (>= 2 abandoned titles)
    abandon_counts = {}
    for g in (gaming_profile or {}).get("steam_summary", {}).get("abandoned_games", []):
        for genre in g.get("genres", []):
            abandon_counts[genre] = abandon_counts.get(genre, 0) + 1
    abandon_prone = {g for g, c in abandon_counts.items() if c >= 2}

    diff = (prefs.get("difficulty_tolerance", "") or "").lower()
    if any(w in diff for w in ("high", "hard")):
        difficulty = "high"
    elif any(w in diff for w in ("low", "easy")):
        difficulty = "low"
    else:
        difficulty = "variable"

    return {
        "love_kw": love_kw, "hate_kw": hate_kw, "mood_tokens": mood_tokens,
        "abandon_prone": abandon_prone, "difficulty": difficulty,
        "story_lean": (prefs.get("story_vs_gameplay", "") or "").lower(),
    }

def get_owned_appids():
    """Get set of owned appids — Steam library plus any non-Steam games that resolved to a
    Steam appid (so we never recommend a game the user already plays elsewhere)."""
    profile = load_json(PROFILE_PATH, {"games": []})
    owned = {g["appid"] for g in profile.get("games", []) if "appid" in g}
    manual = load_json(MANUAL_PATH, {"games": []})
    owned |= {g["appid"] for g in manual.get("games", []) if g.get("appid")}
    return owned

def get_previously_recommended():
    """Get set of previously recommended appids."""
    state = load_json(STATE_PATH, {"weeks": []})
    recd = set()
    for week in state.get("weeks", []):
        for rec in week.get("recommendations", []):
            recd.add(rec.get("appid"))
    return recd

def score_game(appid, name, taste, weights, prefs_index, region="us"):
    """Score a candidate against the corrected genre/tag profile plus interview signals.
    Returns a result dict (with a `reasons` trace) or None to skip."""
    details = fetch_app_details(appid, region=region)
    if not details:
        return None

    # Skip non-game items
    game_type = details.get("type", "")
    if game_type not in ("game", "dlc", "demo", "music", ""):
        return None

    genre_profile = taste.get("genres", {})
    tag_profile = taste.get("tags", {})

    genres = [g["description"] for g in details.get("genres", [])]
    if not genres:
        return None

    # Community tags (real taste signal) come from SteamSpy; appdetails has none.
    community_tags = fetch_steamspy_tags(appid)
    tags_lower = {t.lower() for t in community_tags}
    # Categories are capability flags only — never fed into the tag score.
    categories = [c.get("description", "") for c in details.get("categories", [])]

    reasons = []

    # --- backbone: genre + community-tag affinity ---
    genre_score = sum(genre_profile.get(g, 0) * weights["GENRE_WEIGHT"] for g in genres)
    tag_score = sum(tag_profile.get(t, 0) * weights["TAG_WEIGHT"] for t in community_tags)

    # --- interview-derived nudges ---
    text = _tokens(" ".join(community_tags)) | _tokens(details.get("short_description", "")) | _tokens(name)

    love_hits = len(prefs_index["love_kw"] & text)
    hate_hits = len(prefs_index["hate_kw"] & text)
    mech_boost = weights["MECH_WEIGHT"] * (min(love_hits, 3) / 3)
    mech_pen = weights["MECH_HATE_PEN"] * min(hate_hits, 3)
    if love_hits:
        reasons.append(f"matches {love_hits} loved-mechanic signal(s)")
    if hate_hits:
        reasons.append(f"penalty: {hate_hits} disliked-mechanic signal(s)")

    mood_boost = weights["MOOD_WEIGHT"] if (prefs_index["mood_tokens"] & text) else 0
    if mood_boost:
        reasons.append("matches a preferred mood")

    diff_adj = 0
    if HARD_TAGS & tags_lower:
        if prefs_index["difficulty"] == "high":
            diff_adj = weights["DIFF_BONUS"]; reasons.append("hard game, matches high difficulty tolerance")
        elif prefs_index["difficulty"] == "low":
            diff_adj = -weights["DIFF_PENALTY"]; reasons.append("hard game, against low difficulty tolerance")

    story_adj = 0
    if "story" in prefs_index["story_lean"] and "story rich" in tags_lower:
        story_adj = weights["STORY_BONUS"]; reasons.append("story-rich, matches story preference")

    reviews_total = (details.get("recommendations") or {}).get("total", 0) or 0
    review = weights["REVIEW_WEIGHT"] * min(math.log10(reviews_total + 1) / 5, 1.0)

    abandon_pen = 0
    if prefs_index["abandon_prone"] & set(genres):
        abandon_pen = weights["ABANDON_PEN"]
        reasons.append("abandonment risk: matches a genre you tend to drop")

    top_genres = list(genre_profile.keys())[:3]
    top_bonus = weights["TOP_GENRE_BONUS"] if any(g in top_genres for g in genres) else 0

    total = (genre_score + tag_score + mech_boost + mood_boost + diff_adj + story_adj
             + review + top_bonus - mech_pen - abandon_pen)

    price = "N/A"
    if details.get("price_overview"):
        price = details["price_overview"].get("final_formatted", "N/A")
    elif details.get("is_free"):
        price = "Free"

    return {
        "appid": appid,
        "name": name,
        "score": round(total, 3),
        "genres": genres,
        "tags": community_tags[:5],
        "capabilities": categories[:5],
        "reasons": reasons,
        "store_url": f"https://store.steampowered.com/app/{appid}",
        "price": price,
    }

def build_recommendations():
    """Build and score weekly recommendations from Steam's public APIs."""
    taste = load_json(TASTE_PATH)
    if not taste.get("genres"):
        log.error("No taste profile available. Run game_taste_profile.py first.")
        sys.exit(1)

    config = load_json(CONFIG_PATH)
    max_recs = config.get("maxRecommendations", 5)
    region = config.get("region", "us")
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(config.get("scoringWeights", {}))

    gaming_profile = load_json(GAMING_PROFILE_PATH, {})
    prefs_index = build_preference_index(gaming_profile)
    if prefs_index["love_kw"] or prefs_index["hate_kw"]:
        log.info("Interview signals active: %d loved / %d disliked mechanic tokens, difficulty=%s",
                 len(prefs_index["love_kw"]), len(prefs_index["hate_kw"]), prefs_index["difficulty"])
    else:
        log.info("No interview signals — scoring on corrected genre/tag baseline only.")

    owned = get_owned_appids()
    previous = get_previously_recommended()
    log.info("Owned: %d games, Previously recommended: %d", len(owned), len(previous))

    # Gather candidates from multiple public sources
    candidates = set()
    seen_ids = set()

    def add_candidate(appid, name):
        if appid and name and appid not in owned and appid not in previous and appid not in seen_ids:
            candidates.add((appid, name))
            seen_ids.add(appid)

    # Source 1: Featured categories (specials, top sellers, new releases, coming soon)
    log.info("Fetching featured categories...")
    featured = fetch_featured_games()
    if featured:
        for cat_name, cat_data in featured.items():
            if isinstance(cat_data, dict):
                for item in cat_data.get("items", []):
                    add_candidate(item.get("id"), item.get("name"))
        log.info("  Got %d candidates from featured categories", len(candidates))
    else:
        log.warning("  Featured categories API returned nothing, trying alternative...")

    # Source 2: Featured (specials + new releases)
    log.info("Fetching new releases and specials...")
    new_releases = fetch_new_releases()
    for item in new_releases:
        add_candidate(item["appid"], item["name"])
    log.info("  Total candidates after new releases: %d", len(candidates))

    # Take the first N to score (rate limits)
    candidate_list = list(candidates)[:60]
    log.info("Scoring %d candidates (rate-limited to 20)...", len(candidate_list))

    scored = []
    scored_count = 0
    max_score_attempts = min(30, len(candidate_list))

    for i, (appid, name) in enumerate(candidate_list):
        if scored_count >= 20:
            log.info("Reached scoring limit (20).")
            break

        log.info("Scoring [%d/%d]: %s (appid %d)", i + 1, len(candidate_list), name, appid)
        result = score_game(appid, name, taste, weights, prefs_index, region=region)
        if result and result["score"] > 0:
            scored.append(result)
            scored_count += 1
        time.sleep(0.6)  # Rate limiting

    # Sort by score descending
    scored.sort(key=lambda x: -x["score"])

    return scored[:max_recs]

def save_state(recommendations):
    """Save recommendations to state file."""
    state = load_json(STATE_PATH, {"weeks": []})

    week_entry = {
        "timestamp": int(time.time()),
        "timestamp_readable": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "week_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "recommendations": recommendations
    }

    state.setdefault("weeks", []).append(week_entry)
    state["weeks"] = state["weeks"][-10:]

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

    log.info("Saved %d recommendations", len(recommendations))

def main():
    list_all = "--list-all" in sys.argv

    if not os.path.exists(TASTE_PATH):
        log.error("Taste profile not found. Run game_taste_profile.py first.")
        sys.exit(1)

    scored = build_recommendations()

    if not scored:
        log.info("No recommendations found this week.")
        return

    save_state(scored)

    output = {"recommendations": scored, "generated_at": int(time.time())}
    print(json.dumps(output, indent=2))

    if list_all:
        print("\n--- All scored candidates ---")
        state = load_json(STATE_PATH, {"weeks": []})
        weeks = state.get("weeks", [])
        latest = weeks[-1] if weeks else {}
        print(json.dumps({"top": latest.get("recommendations", [])}, indent=2))

    log.info("Done. %d recommendations generated.", len(scored))

if __name__ == "__main__":
    main()
