#!/usr/bin/env python3
"""
steam_common.py — Shared helpers for the gaming-profile / Steam discovery pipeline.

Centralizes things every script in this pipeline needs so they stay DRY and consistent:
  - resolve_api_key(config): STEAM_API_KEY env -> memory/.secrets/steam.env -> legacy config key
  - mask_key(key):           safe representation for logging
  - fetch_json(url, ...):    urllib GET with exponential backoff + 429/Retry-After handling
  - fetch_app_details(appid):Steam Store appdetails wrapper
  - atomic_write_json(...):  temp-file + os.replace so a crash never corrupts a JSON file

Stdlib only (no `requests`), matching the rest of the pipeline.
"""

import json
import logging
import os
import re
import tempfile
import time
import urllib.request
import urllib.error

log = logging.getLogger("steam_common")

# Where all per-user data lives. Override with GAMING_PROFILE_HOME; defaults to ~/.gaming-profile.
# (OpenClaw users sharing an existing install can point this at ~/.openclaw/workspace/memory.)
DATA_DIR = os.environ.get("GAMING_PROFILE_HOME") or os.path.join(os.path.expanduser("~"), ".gaming-profile")
MEMORY_DIR = DATA_DIR  # back-compat alias

CONFIG_PATH = os.path.join(DATA_DIR, "steam-games-config.json")
PROFILE_PATH = os.path.join(DATA_DIR, "steam-profile.json")
TASTE_PATH = os.path.join(DATA_DIR, "steam-taste-profile.json")
STATE_PATH = os.path.join(DATA_DIR, "game-recs-state.json")
HISTORY_PATH = os.path.join(DATA_DIR, "steam-games-history.json")
GAMING_PROFILE_PATH = os.path.join(DATA_DIR, "gaming-profile-v0.json")
MANUAL_PATH = os.path.join(DATA_DIR, "manual-games.json")
INTERVIEW_STATE_PATH = os.path.join(DATA_DIR, "interview_state.json")
LATEST_PATH = os.path.join(DATA_DIR, "weekly-discovery-latest.md")
SECRETS_PATH = os.path.join(DATA_DIR, ".secrets", "steam.env")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _parse_env_file(path):
    """Parse simple KEY=VALUE lines (ignores blanks and # comments)."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def resolve_api_key(config=None):
    """Resolve the Steam Web API key with precedence:
       1) STEAM_API_KEY environment variable
       2) memory/.secrets/steam.env  (a KEY=VALUE file with STEAM_API_KEY=...)
       3) legacy config["steamApiKey"] (deprecated; logs a warning)
    Returns the key string, or "" if none found.
    """
    env_key = os.environ.get("STEAM_API_KEY", "").strip()
    if env_key:
        return env_key

    secrets = _parse_env_file(SECRETS_PATH)
    if secrets.get("STEAM_API_KEY"):
        return secrets["STEAM_API_KEY"]

    if config and config.get("steamApiKey"):
        log.warning("Using legacy plaintext steamApiKey from config. Move it to %s "
                    "(or the STEAM_API_KEY env var) and rotate the key.", SECRETS_PATH)
        return config["steamApiKey"]

    return ""


def mask_key(key):
    """Return a log-safe masked version of an API key."""
    if not key:
        return "(none)"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def _safe_url(url):
    """URL rendered safe for logging: masks any key= query param so the Steam API key
    can never leak into logs, cron output, or agent-visible errors."""
    return re.sub(r"(key=)[^&]*", r"\1***", url or "")[:120]


def fetch_json(url, retries=3, backoff=1.0, timeout=20):
    """GET JSON with exponential backoff. Honors Retry-After on HTTP 429.
    Returns the parsed object, or None on persistent failure."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After", 0)) or backoff * (2 ** attempt)
                log.warning("Rate limited (429). Waiting %.1fs (attempt %d/%d)...",
                            wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            log.warning("HTTP %d fetching %s: %s", e.code, _safe_url(url), e)
            return None
        except Exception as e:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                log.warning("Error fetching %s: %s — retry in %.1fs", _safe_url(url), e, wait)
                time.sleep(wait)
                continue
            log.warning("Error fetching %s: %s", _safe_url(url), e)
            return None
    return None


def fetch_app_details(appid, retries=3, backoff=1.0):
    """Fetch Steam Store appdetails for a single appid. Returns the 'data' dict or None."""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    data = fetch_json(url, retries=retries, backoff=backoff, timeout=15)
    if data and str(appid) in data and data[str(appid)].get("success"):
        return data[str(appid)]["data"]
    return None


def normalize_game_key(name):
    """Stable dict key for a game name: lowercase, strip trademark/version glyphs, snake_case.
    e.g. 'NieR:Automata™' -> 'nier_automata',
         'NieR Replicant ver.1.22474487139...' -> 'nier_replicant'."""
    s = (name or "").lower().replace("™", "").replace("®", "")
    s = re.sub(r"\bver\.?\s*[\d.]+.*$", "", s)   # drop version suffixes
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"


def fetch_steamspy_tags(appid, retries=3, backoff=1.0, top_n=20):
    """Fetch Steam community tags for an appid, ordered by vote count.

    The Steam Store appdetails endpoint does NOT expose user tags (only platform
    `categories`). SteamSpy mirrors the community tags as {tag: votes}, which is the
    real taste signal. Returns a list of tag names (highest-voted first), or []."""
    url = f"https://steamspy.com/api.php?request=appdetails&appid={appid}"
    data = fetch_json(url, retries=retries, backoff=backoff, timeout=15)
    if not data:
        return []
    tags = data.get("tags", {})
    if isinstance(tags, dict) and tags:
        return [t for t, _ in sorted(tags.items(), key=lambda kv: -(kv[1] or 0))][:top_n]
    if isinstance(tags, list):
        return [t for t in tags if t][:top_n]
    return []


def atomic_write_json(path, obj, indent=2, ensure_ascii=False):
    """Write JSON atomically: serialize to a temp file in the same directory, then os.replace.
    A crash mid-write leaves the original file intact rather than truncated/corrupt."""
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent, ensure_ascii=ensure_ascii)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
