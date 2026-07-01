#!/bin/bash
# setup.sh — one-time setup for the gaming-profile pipeline.
#
#   STEAM_API_KEY=<your-key> bash setup.sh
#
# Optionally set GAMING_PROFILE_HOME to choose where data lives (default ~/.gaming-profile).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
EXAMPLES="$SKILL_DIR/examples"
DATA_DIR="${GAMING_PROFILE_HOME:-$HOME/.gaming-profile}"
SECRETS_DIR="$DATA_DIR/.secrets"
CONFIG_FILE="$DATA_DIR/steam-games-config.json"

echo "=== gaming-profile setup ==="
echo "Data dir: $DATA_DIR"
echo ""

# Python
command -v python3 >/dev/null 2>&1 || { echo "  ✗ python3 not found"; exit 1; }
echo "[1] python3: $(python3 --version)"

# Data dir + secret
mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR" 2>/dev/null || true
echo "[2] Steam API key"
if [ -n "$STEAM_API_KEY" ]; then
    printf 'STEAM_API_KEY=%s\n' "$STEAM_API_KEY" > "$SECRETS_DIR/steam.env"
    chmod 600 "$SECRETS_DIR/steam.env"
    echo "  ✓ wrote $SECRETS_DIR/steam.env (chmod 600)"
elif [ -f "$SECRETS_DIR/steam.env" ]; then
    echo "  ✓ key already present"
else
    echo "  ✗ no key. Get one at https://steamcommunity.com/dev/apikey then:"
    echo "      STEAM_API_KEY=<your-key> bash $0"
    exit 1
fi

# Config from template
echo "[3] config"
if [ -f "$CONFIG_FILE" ]; then
    echo "  ✓ config exists at $CONFIG_FILE"
else
    cp "$EXAMPLES/steam-games-config.example.json" "$CONFIG_FILE"
    echo "  ✓ created $CONFIG_FILE — edit it and set your steamId (SteamID64)."
    echo "    Find your SteamID64 at https://steamid.io or via your vanity URL."
fi

# Make scripts executable
chmod +x "$SCRIPT_DIR"/*.py 2>/dev/null || true

echo ""
echo "=== next steps ==="
echo "  1. Set your steamId in $CONFIG_FILE"
echo "  2. python3 $SCRIPT_DIR/steam_profile.py"
echo "  3. python3 $SCRIPT_DIR/game_taste_profile.py --force"
echo "  4. python3 $SCRIPT_DIR/build_gaming_profile.py"
echo "  5. python3 $SCRIPT_DIR/game_recommender.py"
echo ""
echo "Full pipeline: python3 $SCRIPT_DIR/weekly_game_discovery.py"
echo "Weekly cron (Mon 10:00 — add yourself if you want it):"
echo "  0 10 * * 1 python3 $SCRIPT_DIR/weekly_game_discovery.py --deliver"
