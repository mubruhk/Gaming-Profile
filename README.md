# gaming-profile

**Know what to play next — from what you actually play.**

Build a personal gaming taste profile from your Steam library — plus the games you play on Epic,
GOG, or console — then get weekly recommendations scored against what you *actually* enjoy, with a
human-readable reason for every pick.

- 🧬 **Taste profile, not just stats** — weighted genre profile from your playtime, real community
  tags (Souls-like, Story Rich, Open World…) from SteamSpy, non-game software filtered out.
- 🎙️ **Game interviews** — short structured Q&A about your games (loved/hated mechanics,
  difficulty, mood, story-vs-gameplay). This is the signal hours can't give you.
- 🌍 **Non-Steam games count** — blend in your Epic/GOG/console hours, capped and recency-adjusted
  so one old 1000-hour obsession can't drown out your current taste.
- 🎯 **Explained recommendations** — every pick comes with reasons ("matches 2 loved-mechanic
  signals", "abandonment risk: a genre you tend to drop").
- 🤖 **Agent-ready** — every command is a one-shot call that prints a single JSON object, so an AI
  assistant (e.g. an [OpenClaw](https://openclaw.ai) agent) can drive the whole thing over chat.
  Also works fine as a plain CLI.
- 🪶 **Zero dependencies** — Python 3.7+ standard library only. No pip install.

## Why

Raw playtime lies. A soundboard utility idling in your library for 1,000 hours says nothing about
your taste, while 15 intense hours in a game you adored says everything. Store "categories" lie
too — "Single-player" and "Steam Achievements" describe almost every game ever made.

This tool builds taste from three honest signals: **where your hours really went** (with non-games
filtered and idle-software noise removed), **what the community actually calls your games**
(SteamSpy user tags), and **what you say about them** (interviews). Recommendations are scored
against all three.

## How it works

```
Steam Web API ──▶ steam_profile.py ──▶ your library (playtime, recently played)
                                              │
Steam store + SteamSpy ──▶ game_taste_profile.py ──▶ weighted genre/tag profile
manual_games.py (Epic/GOG/console) ──────────┤         (capped, recency-adjusted)
                                              ▼
interview_engine.py (Q&A) ──▶ build_gaming_profile.py ──▶ gaming-profile-v0.json
                                              │              (the canonical profile)
                                              ▼
              game_recommender.py ──▶ scored picks with reasons
                                              │
              weekly_game_discovery.py ──▶ formatted weekly digest (+ optional delivery)
```

Scoring backbone is genre + community-tag affinity (≈70%), nudged by interview signals — loved
mechanics boost, hated mechanics penalize, mood and difficulty fit adjust, review volume adds
confidence, and genres you statistically abandon get docked. All weights are tunable in config.

## Quick start

1. **Run setup — it walks you through everything:**
   ```bash
   bash scripts/setup.sh
   ```
   It asks for:
   - your **Steam Web API key** — free at https://steamcommunity.com/dev/apikey (log in, accept
     the API terms, any domain name works). Stored locally in `~/.gaming-profile/.secrets/steam.env`
     (chmod 600), never shared or logged.
   - your **Steam account ID** — paste your SteamID64, your profile URL, or just your vanity name
     (e.g. `yourname` from `steamcommunity.com/id/yourname`) and it resolves the ID for you.

   Then it runs your first library scan. Non-interactive (cron/CI):
   `STEAM_API_KEY=<key> STEAM_ID=<steamid64> bash scripts/setup.sh`
2. **Build your profile:**
   ```bash
   python3 scripts/game_taste_profile.py --force   # genre/tag weights (takes ~1 min: store lookups)
   python3 scripts/build_gaming_profile.py         # fuse into gaming-profile-v0.json
   ```
3. **Get recommendations:**
   ```bash
   python3 scripts/game_recommender.py             # JSON picks with scores + reasons
   python3 scripts/weekly_game_discovery.py        # or: full refresh + formatted digest
   ```

The weekly digest looks like:

```
🎮 Weekly Game Discovery — Week of July 02, 2026

Top Pick: <game>
→ Matches: Action, RPG
→ Tags: Open World, Story Rich
→ Why: matches 2 loved-mechanic signal(s)
→ Price: $39.99
→ Steam: <store link>

Also check out:
2. <game> — Racing, Simulation (match: 34%)
...
📊 Taste snapshot: Action 17% | Indie 15% | Adventure 14% | Simulation 14% | Racing 11%
```

## Interviews

Record *why* you like a game — this is what makes recommendations good:

```bash
python3 scripts/interview_engine.py next_game --mode deep_dive   # who's next in the queue?
python3 scripts/interview_engine.py start --game "NieR:Automata" --mode auto
# → {"ok": true, "response": "What's your overall opinion on NieR:Automata? ...", "question_number": 1, "total_questions": 9}
python3 scripts/interview_engine.py answer --answer "the combat and the atmosphere"
# → next question ... repeat until {"done": true}
```

Games with 10+ hours get a **deep dive** (9 questions: opinion, loved/hated mechanics, difficulty,
session length, story-vs-gameplay, mood, genre, replay); lighter games get a **quick** 3-question
pass. Answers fold into your global preferences and feed scoring. `status`, `skip`, and `cancel`
round out the ops. It's a one-question-per-call state machine — nothing blocks, so a chat
assistant can run it turn by turn over your messaging app.

## Non-Steam games

Your taste isn't only Steam. Add the games you play elsewhere:

```bash
python3 scripts/manual_games.py add --name "Rocket League" --hours 1000 --platform epic --recency occasional
python3 scripts/manual_games.py list
python3 scripts/manual_games.py remove --name "Rocket League"
```

- If the game is still on Steam, genres/tags are pulled automatically; if it's delisted (like
  Rocket League), pass `--genres "Sports,Racing" --tags "Competitive,Multiplayer"`.
- `--recency` (`active` / `occasional` / `retired`) scales how much that playtime counts, and each
  game's influence is **capped at your top Steam game's hours** — reflecting your history without
  letting it dominate.
- Re-run `game_taste_profile.py --force && build_gaming_profile.py` to apply. Manual games also
  join the interview queue and are excluded from recommendations.

## Weekly automation

```bash
# crontab — Mondays at 10:00 (add it yourself if you want it):
0 10 * * 1 python3 /path/to/scripts/weekly_game_discovery.py --deliver
```

`--deliver` runs `deliveryCommand` from your config — a shell template where `{message}` is
replaced with the (safely quoted) digest. Examples:

```jsonc
// Discord DM via a messaging CLI:
"deliveryCommand": "cd ~/discord-cli && ./dcu dm Me {message}"
// Webhook:
"deliveryCommand": "curl -s -X POST --data-urlencode content={message} https://discord.com/api/webhooks/..."
```

If `deliveryCommand` is unset (or fails), the digest is written to
`weekly-discovery-latest.md` in the data dir instead — nothing is lost.

> ⚠️ `deliveryCommand` executes as a shell command by design. Only you should set it — an AI
> assistant must never write it from untrusted content.

## Using it with an AI assistant

Every script is a stateless one-shot call that prints exactly one JSON object — designed so an
agent can drive it with plain shell tools:

- the agent runs `interview_engine.py start …`, shows you the returned `response`, and feeds your
  reply back via `answer --answer "…"` — a natural interview over chat;
- "what should I play?" → the agent runs `game_recommender.py` and presents the top pick with its
  `reasons`;
- "I've got 300 hours in X on Epic" → the agent runs `manual_games.py add …`.

For [OpenClaw](https://openclaw.ai) users, `SKILL.md` in this repo is the ready-made skill
instruction file: drop this repo into your skills directory and the agent knows the triggers,
commands, and rules (including "never invent or reuse someone else's API key").

## Configuration

Everything lives in `$GAMING_PROFILE_HOME` (default `~/.gaming-profile/`):

| File | What it is |
|------|------------|
| `steam-games-config.json` | your settings (see below) |
| `.secrets/steam.env` | your API key (chmod 600, never committed/logged) |
| `steam-profile.json` | raw library scan |
| `steam-taste-profile.json` | weighted genre/tag profile |
| `gaming-profile-v0.json` | the canonical fused profile (interviews live here) |
| `manual-games.json` | your non-Steam games |
| `game-recs-state.json` | recommendation history (prevents repeats, keeps 10 weeks) |

Config fields (`steam-games-config.json`):

| Field | Default | Purpose |
|-------|---------|---------|
| `userName` | `"You"` | display name in your profile |
| `steamId` | — | your SteamID64 (setup fills this) |
| `maxRecommendations` | `5` | picks per run |
| `region` | `"us"` | store pricing region |
| `deliveryCommand` | `""` | shell template for `--deliver` (see above) |
| `scoringWeights` | `{}` | override any scoring weight (`GENRE_WEIGHT` 0.45, `TAG_WEIGHT` 0.25, `MECH_WEIGHT` 0.15, `REVIEW_WEIGHT` 0.10, `MOOD_WEIGHT` 0.05, penalties `MECH_HATE_PEN` 0.15, `ABANDON_PEN` 0.10, …) |

## Troubleshooting

- **Scan fails with 401/403** — wrong/expired API key, or your profile's **Game details** privacy
  setting isn't Public (Steam profile → Edit Profile → Privacy Settings). Keys never appear in
  logs (they're masked), so just re-run `setup.sh` with a fresh key.
- **A game has empty tags** — very new releases often have no SteamSpy data yet; they're scored on
  genres until tags exist.
- **Utility software polluting your profile** — known offenders (Wallpaper Engine, Soundpad, …)
  are filtered; add others to `NON_GAME_APPIDS` in `scripts/game_taste_profile.py`.
- **Rate-limit warnings** — the built-in ~1.5s delay + retries handle Steam/SteamSpy limits;
  they're intentional, please don't lower them.

## API usage & compliance

- **Bring your own key.** No key is bundled; you supply yours and it's read at runtime from a
  gitignored secret file. It's used by exactly one script (`steam_profile.py`) to read *your own*
  owned/recently-played games via the official Steam Web API. You must accept Steam's
  [API Terms](https://steamcommunity.com/dev/apiterms). This is a personal, non-commercial tool.
- **Unofficial endpoints.** Genres/candidates come from `store.steampowered.com/api/*`, which is
  undocumented/unofficial and may rate-limit or change — used at your own risk.
- **SteamSpy.** Community tags come from [SteamSpy](https://steamspy.com), a third party with its
  own terms and rate limits. Thanks to SteamSpy for the data.

## Repo layout

```
SKILL.md            agent instruction file (OpenClaw skill)
scripts/            the pipeline (8 stdlib-only Python scripts + setup.sh)
examples/           config + manual-games templates
skill-card.md       one-screen summary · _meta.json skill metadata
```

## License

MIT — see `LICENSE`.
