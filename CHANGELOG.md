# Changelog

## 0.1.0 — 2026-07-01
Initial release.
- Steam library scan + weighted genre/tag taste profile (community tags via SteamSpy).
- Non-Steam games (Epic/GOG/console) blended in, capped + recency-adjusted.
- Structured game interviews (deep-dive / quick), agent-callable JSON interface.
- Weekly recommendations scored against genre/tag weights + interview signals, with reasons.
- Pluggable delivery (`deliveryCommand`) with local-file fallback.
- Interactive setup: prompts for the user's own Steam API key (with instructions) and Steam ID
  (accepts SteamID64, profile URL, or vanity name — resolved via ResolveVanityURL).
- Stdlib-only (no pip); configurable data dir via `GAMING_PROFILE_HOME`.
