# gaming-profile

Build a personal **gaming taste profile** from your Steam library — plus the games you play on
Epic, GOG, or console — then get weekly recommendations scored against what you *actually* enjoy,
not just hours played.

It goes beyond raw stats: genres come from Steam, community tags from SteamSpy, and the real
signal comes from short **interviews** about your games (what mechanics you love, difficulty,
mood, story-vs-gameplay). Non-Steam games are blended in, and one old 1000-hour obsession can't
drown out your current taste.

Runs anywhere with **Python 3.7+ and no dependencies** (standard library only). Designed to work
standalone from the CLI *or* as an [OpenClaw](https://openclaw.ai) skill an assistant can drive.

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
   `STEAM_API_KEY=<key> STEAM_ID=<steamid64> bash scripts/setup.sh`.
2. **Build your profile:**
   ```bash
   python3 scripts/game_taste_profile.py --force
   python3 scripts/build_gaming_profile.py
   ```
3. **Get recommendations:**
   ```bash
   python3 scripts/game_recommender.py
   # or the full formatted weekly run:
   python3 scripts/weekly_game_discovery.py
   ```

## Interviews

Record *why* you like a game — this is what makes recommendations good:
```bash
python3 scripts/interview_engine.py next_game --mode deep_dive
python3 scripts/interview_engine.py start --game "NieR:Automata" --mode auto
python3 scripts/interview_engine.py answer --answer "the combat and the atmosphere"
# ...repeat until done
```
It's a one-question-per-call state machine, so a chat assistant can conduct it turn by turn over
your messaging app. Deep dive = 9 questions, quick = 3.

## Non-Steam games

```bash
python3 scripts/manual_games.py add --name "Rocket League" --hours 1000 --platform epic --recency occasional
python3 scripts/manual_games.py list
```
If the game is still on Steam its genres/tags are pulled automatically; if it's delisted (like
Rocket League) pass `--genres "Sports,Racing" --tags "Competitive,Multiplayer"`. `--recency`
(`active`/`occasional`/`retired`) scales how much that playtime counts. Re-run the taste build to
apply.

## Weekly automation

```bash
# add to your crontab if you want it (Mon 10:00):
0 10 * * 1 python3 /path/to/scripts/weekly_game_discovery.py --deliver
```
`--deliver` runs `config.deliveryCommand` (a shell template with `{message}`); if unset it writes
`weekly-discovery-latest.md`. See `SKILL.md` for delivery examples (Discord, webhook).

## Configuration

- **Data location:** everything lives in `$GAMING_PROFILE_HOME` (default `~/.gaming-profile/`).
- **Scoring:** tune `scoringWeights` in the config (genre/tag/mechanics/mood/review/penalties).
- **Region:** `region` in the config controls store pricing (`us`, `eu`, …).

## API usage & compliance

- **Bring your own key.** No key is bundled; you supply yours and it's read at runtime from a
  gitignored secret file. It's used by exactly one script (`steam_profile.py`) to read *your own*
  owned/recently-played games via the official Steam Web API. You must accept Steam's
  [API Terms](https://steamcommunity.com/dev/apiterms). This is a personal, non-commercial tool.
- **Unofficial endpoints.** Genres/candidates come from `store.steampowered.com/api/*`, which is
  undocumented/unofficial and may rate-limit or change — used at your own risk.
- **SteamSpy.** Community tags come from [SteamSpy](https://steamspy.com), a third party with its
  own terms and rate limits. Thanks to SteamSpy for the data.
- **Be polite.** The built-in ~1.5s delay + retries respect rate limits; please don't remove them.

## License

MIT — see `LICENSE`.
