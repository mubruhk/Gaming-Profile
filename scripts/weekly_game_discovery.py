#!/usr/bin/env python3
"""
weekly_game_discovery.py — Orchestrator
Runs the full weekly discovery pipeline and outputs a Discord-ready message.
"""

import json
import logging
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

from steam_common import PROFILE_PATH, TASTE_PATH, STATE_PATH, CONFIG_PATH, LATEST_PATH

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("weekly_discovery")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default if default is not None else {}

def run_script(script_name, args=None):
    """Run a Python script and return (returncode, stdout, stderr)."""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    cmd = [sys.executable, script_path]
    if args:
        cmd.extend(args)

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    return result.returncode, result.stdout, result.stderr

def run_profile_scan():
    """Step 1: Run steam profile scanner."""
    log.info("Step 1: Scanning Steam profile...")
    rc, out, err = run_script("steam_profile.py")
    if rc != 0:
        log.error("Profile scan failed:\n%s", err)
        return False
    log.info("Profile scan complete.")
    return True

def run_taste_build(force=False):
    """Step 2: Build/update taste profile."""
    log.info("Step 2: Building taste profile...")
    args = ["--force"] if force else []
    rc, out, err = run_script("game_taste_profile.py", args)
    if rc != 0:
        log.error("Taste profile build failed:\n%s", err)
        return False
    log.info("Taste profile ready.")
    return True

def run_profile_build():
    """Step 2b: Refresh the canonical gaming-profile-v0.json (steam_summary + queue)
    so the recommender scores against fresh genre/tag weights. Non-fatal on failure."""
    log.info("Step 2b: Refreshing fused gaming profile...")
    rc, out, err = run_script("build_gaming_profile.py")
    if rc != 0:
        log.warning("Profile build failed (continuing on baseline scoring):\n%s", err)
        return False
    log.info("Gaming profile refreshed.")
    return True

def run_recommender():
    """Step 3: Generate recommendations."""
    log.info("Step 3: Finding game recommendations...")
    rc, out, err = run_script("game_recommender.py")
    if rc != 0:
        log.error("Recommender failed:\n%s", err)
        return None
    try:
        result = json.loads(out.strip())
        log.info("Found %d recommendations.", len(result.get("recommendations", [])))
        return result
    except json.JSONDecodeError:
        log.error("Could not parse recommender output:\n%s", out)
        return None

def format_recommendations(recs_data):
    """Format recommendations into Discord-friendly message."""
    recs = recs_data.get("recommendations", [])
    if not recs:
        return "🎮 **Weekly Game Discovery**\n\nNo new recommendations this week. Check back later!"

    # Load taste profile for snapshot
    taste = _load_json(TASTE_PATH, {})

    week_of = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = [f"🎮 **Weekly Game Discovery — Week of {week_of}**\n"]

    # Top pick
    top = recs[0]
    lines.append(f"**Top Pick:** {top['name']}")
    lines.append(f"→ Matches: {', '.join(top['genres'][:3])}")
    if top.get('tags'):
        lines.append(f"→ Tags: {', '.join(top['tags'][:3])}")
    if top.get('reasons'):
        lines.append(f"→ Why: {top['reasons'][0]}")
    if top.get('price') and top['price'] != "N/A":
        lines.append(f"→ Price: {top['price']}")
    lines.append(f"→ Steam: <{top['store_url']}>")
    lines.append("")

    # Other recommendations
    if len(recs) > 1:
        lines.append("**Also check out:**")
        for i, rec in enumerate(recs[1:], 2):
            genre_str = ", ".join(rec['genres'][:2]) if rec['genres'] else "Various"
            lines.append(f"{i}. **{rec['name']}** — {genre_str} (match: {rec['score']*100:.0f}%)")
        lines.append("")

    # Taste snapshot
    if taste.get("genres"):
        sorted_genres = sorted(taste["genres"].items(), key=lambda x: -x[1])[:5]
        snapshot = " | ".join(f"{g} {w*100:.0f}%" for g, w in sorted_genres)
        lines.append(f"📊 **Taste snapshot:** {snapshot}")
        lines.append("")

    # Recent activity
    profile = _load_json(PROFILE_PATH, {})
    if profile:
        recent_played = profile.get("recently_played", [])
        if recent_played:
            lines.append("**This week in your library:**")
            for g in recent_played[:3]:
                hours = g.get("playtime_2weeks_minutes", 0) // 60
                total = g.get("playtime_forever_minutes", 0) // 60
                if hours > 0:
                    lines.append(f"• {g['name']} — {hours}h this week ({total}h total)")

    return "\n".join(lines)

def deliver(message, dry=False):
    """Deliver the weekly message via a user-configured command, else save it locally.

    Set config["deliveryCommand"] to a shell template containing '{message}', e.g.
      OpenClaw Discord DM:  "cd ~/.openclaw/workspace/discord-control-unit && ./dcu dm Me {message}"
      A webhook via curl:   "curl -s -X POST -d {message} https://..."
    If unset (or on any failure), the message is written to weekly-discovery-latest.md.
    Returns True only if the delivery command ran successfully."""
    tmpl = _load_json(CONFIG_PATH, {}).get("deliveryCommand", "")
    if not tmpl:
        log.info("No deliveryCommand configured — writing message to file.")
        _write_fallback(message)
        return False
    if dry:
        log.info("[dry] would run deliveryCommand for a %d-char message", len(message))
        _write_fallback(message)
        return False
    try:
        cmd = tmpl.replace("{message}", shlex.quote(message))
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            log.info("Delivered via deliveryCommand.")
            return True
        log.warning("deliveryCommand exited %d: %s", result.returncode, result.stderr.strip()[:200])
    except Exception as e:
        log.warning("deliveryCommand failed: %s", e)
    _write_fallback(message)
    return False

def _write_fallback(message):
    try:
        with open(LATEST_PATH, "w", encoding="utf-8") as f:
            f.write(message + "\n")
        log.info("Wrote fallback message to %s", LATEST_PATH)
    except OSError as e:
        log.error("Could not write fallback file: %s", e)

def main():
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    do_deliver = "--deliver" in sys.argv
    do_deliver_dry = "--deliver-dry" in sys.argv

    if dry_run:
        log.info("DRY RUN MODE")
        log.info("Would run: steam_profile.py -> game_taste_profile.py -> build_gaming_profile.py -> game_recommender.py")
        log.info("Would deliver: %s", "via deliveryCommand (dry)" if do_deliver_dry else
                 ("via deliveryCommand" if do_deliver else "stdout only"))
        return

    # Step 1: Profile scan
    if not run_profile_scan():
        sys.exit(1)

    # Step 2: Taste build (auto-skips if no changes, unless --force)
    if not run_taste_build(force=force):
        sys.exit(1)

    # Step 2b: Refresh fused gaming profile (non-fatal)
    run_profile_build()

    # Step 3: Recommendations
    recs = run_recommender()

    # Step 4: Format, then deliver or print
    if not recs:
        log.info("No recommendations this cycle.")
        return

    message = format_recommendations(recs)
    if do_deliver or do_deliver_dry:
        deliver(message, dry=do_deliver_dry)
        # Always echo to stdout too, for logs/cron output
        print(message)
    else:
        print(message)
        print("---")
    log.info("Recommendations generated and ready.")

if __name__ == "__main__":
    main()
