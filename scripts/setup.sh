#!/bin/bash
# setup.sh — one-time interactive setup for the gaming-profile pipeline.
#
# Interactive:      bash setup.sh          (prompts for your Steam API key + Steam ID)
# Non-interactive:  STEAM_API_KEY=<key> STEAM_ID=<steamid64> bash setup.sh
#
# Optionally set GAMING_PROFILE_HOME to choose where data lives (default ~/.gaming-profile).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
EXAMPLES="$SKILL_DIR/examples"
DATA_DIR="${GAMING_PROFILE_HOME:-$HOME/.gaming-profile}"
SECRETS_DIR="$DATA_DIR/.secrets"
SECRETS_FILE="$SECRETS_DIR/steam.env"
CONFIG_FILE="$DATA_DIR/steam-games-config.json"

echo "=== gaming-profile setup ==="
echo "Data dir: $DATA_DIR"
echo ""

# ---------------------------------------------------------------- [1] python
command -v python3 >/dev/null 2>&1 || { echo "  ✗ python3 not found (need Python 3.7+)"; exit 1; }
echo "[1] python3: $(python3 --version)"

mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR" 2>/dev/null || true

# ------------------------------------------------------- [2] Steam API key
echo ""
echo "[2] Steam Web API key"
API_KEY="${STEAM_API_KEY:-}"
if [ -z "$API_KEY" ] && [ -f "$SECRETS_FILE" ]; then
    API_KEY=$(grep -m1 '^STEAM_API_KEY=' "$SECRETS_FILE" | cut -d= -f2-)
    [ -n "$API_KEY" ] && echo "  ✓ using existing key from $SECRETS_FILE"
fi
if [ -z "$API_KEY" ]; then
    echo ""
    echo "  A Steam Web API key is required to read your own game library."
    echo "  Get yours (free, ~30 seconds): https://steamcommunity.com/dev/apikey"
    echo "    - Log in with your Steam account and agree to the API terms"
    echo "    - 'Domain name' can be anything (e.g. localhost)"
    echo "    - Copy the 32-character key it shows you"
    echo "  Your key stays on this machine ($SECRETS_FILE) and is never shared or logged."
    echo ""
    for _try in 1 2 3; do
        printf "  Paste your Steam API key: "
        read -rs API_KEY && echo ""
        API_KEY=$(printf '%s' "$API_KEY" | tr -d '[:space:]')
        if printf '%s' "$API_KEY" | grep -qiE '^[0-9a-f]{32}$'; then break; fi
        echo "  ✗ that doesn't look like a Steam key (expected 32 hex characters) — try again."
        API_KEY=""
    done
    if [ -z "$API_KEY" ]; then
        echo "  ✗ no valid key entered. Re-run setup, or use: STEAM_API_KEY=<key> bash $0"
        exit 1
    fi
fi
printf 'STEAM_API_KEY=%s\n' "$API_KEY" > "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
echo "  ✓ key saved to $SECRETS_FILE (chmod 600)"

# ------------------------------------------------------- [3] Steam account ID
echo ""
echo "[3] Steam account ID"
STEAM_ID64="${STEAM_ID:-}"
VANITY=""

# keep an already-configured id
if [ -z "$STEAM_ID64" ] && [ -f "$CONFIG_FILE" ]; then
    EXISTING=$(python3 -c "import json;print(json.load(open('$CONFIG_FILE')).get('steamId',''))" 2>/dev/null || true)
    case "$EXISTING" in 7656[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9])
        STEAM_ID64="$EXISTING"; echo "  ✓ using existing steamId from config: $STEAM_ID64";;
    esac
fi

resolve_input() {
    # Normalizes user input (SteamID64 / profile URL / vanity name) into STEAM_ID64 (+VANITY).
    local input="$1" digits vanity
    input=$(printf '%s' "$input" | tr -d '[:space:]')
    # bare SteamID64
    case "$input" in 7656[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9])
        STEAM_ID64="$input"; return 0;; esac
    # /profiles/<id> URL
    digits=$(printf '%s' "$input" | grep -oE 'profiles/7656[0-9]{13}' | grep -oE '7656[0-9]{13}' | head -1)
    if [ -n "$digits" ]; then STEAM_ID64="$digits"; return 0; fi
    # /id/<vanity> URL or bare vanity name -> resolve via Steam API (key passed via env, not argv)
    vanity=$(printf '%s' "$input" | sed -E 's#.*/id/##; s#/+$##')
    echo "  Resolving vanity name '$vanity' via Steam..."
    digits=$(SK="$API_KEY" SV="$vanity" python3 - << 'PYEOF'
import json, os, urllib.parse, urllib.request
url = ("https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
       f"?key={os.environ['SK']}&vanityurl={urllib.parse.quote_plus(os.environ['SV'])}")
try:
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.load(r).get("response", {})
    print(d.get("steamid", "") if d.get("success") == 1 else "")
except Exception:
    print("")
PYEOF
)
    if [ -n "$digits" ]; then STEAM_ID64="$digits"; VANITY="$vanity"; return 0; fi
    return 1
}

if [ -z "$STEAM_ID64" ]; then
    echo ""
    echo "  Your Steam account ID (SteamID64) tells the pipeline whose library to read."
    echo "  You can paste ANY of these:"
    echo "    - the 17-digit SteamID64 itself (starts with 7656...)"
    echo "    - your profile URL   e.g. https://steamcommunity.com/profiles/7656...."
    echo "    - your vanity URL/name  e.g. https://steamcommunity.com/id/yourname (or just 'yourname')"
    echo "  Unsure? Look it up at https://steamid.io"
    echo "  Note: your profile's 'Game details' must be public for the library scan to work"
    echo "  (Steam profile > Edit Profile > Privacy Settings)."
    echo ""
    for _try in 1 2 3; do
        printf "  Steam ID / profile URL / vanity name: "
        read -r RAW_ID
        if [ -n "$RAW_ID" ] && resolve_input "$RAW_ID"; then break; fi
        echo "  ✗ couldn't resolve that — try the 17-digit SteamID64 from https://steamid.io"
        STEAM_ID64=""
    done
    if [ -z "$STEAM_ID64" ]; then
        echo "  ✗ no Steam ID resolved. Re-run setup, or use: STEAM_ID=<steamid64> bash $0"
        exit 1
    fi
fi
echo "  ✓ SteamID64: $STEAM_ID64"

# ------------------------------------------------------- [4] write config
echo ""
echo "[4] config"
if [ ! -f "$CONFIG_FILE" ]; then
    cp "$EXAMPLES/steam-games-config.example.json" "$CONFIG_FILE"
fi
CF="$CONFIG_FILE" SID="$STEAM_ID64" SVAN="$VANITY" python3 - << 'PYEOF'
import json, os
p = os.environ["CF"]
c = json.load(open(p))
c["steamId"] = os.environ["SID"]
if os.environ.get("SVAN"):
    c["steamVanityUrl"] = os.environ["SVAN"]
json.dump(c, open(p, "w"), indent=2)
PYEOF
echo "  ✓ $CONFIG_FILE (steamId set)"

chmod +x "$SCRIPT_DIR"/*.py 2>/dev/null || true

# ------------------------------------------------------- [5] initial scan
echo ""
echo "[5] Running initial profile scan..."
if python3 "$SCRIPT_DIR/steam_profile.py"; then
    echo "  ✓ profile scan complete"
else
    echo "  ✗ scan failed — double-check your API key, Steam ID, and that your profile's"
    echo "    'Game details' privacy setting is Public. Then re-run: python3 $SCRIPT_DIR/steam_profile.py"
fi

echo ""
echo "=== setup complete — next steps ==="
echo "  1. python3 $SCRIPT_DIR/game_taste_profile.py --force   # build genre/tag taste weights"
echo "  2. python3 $SCRIPT_DIR/build_gaming_profile.py         # fuse into your gaming profile"
echo "  3. python3 $SCRIPT_DIR/game_recommender.py             # get recommendations"
echo ""
echo "Full pipeline: python3 $SCRIPT_DIR/weekly_game_discovery.py"
echo "Weekly cron (Mon 10:00 — add yourself if you want it):"
echo "  0 10 * * 1 python3 $SCRIPT_DIR/weekly_game_discovery.py --deliver"
