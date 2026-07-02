# gaming-profile

**Know what to play next — from what you actually play.**

Turns your Steam library (plus non-Steam games) into a weighted taste profile, lets an agent
interview you about your games, and recommends new releases scored against your real taste —
each with a reason.

- **Profile:** genre weights from Steam, community tags from SteamSpy, non-Steam games blended
  in (capped + recency-adjusted).
- **Interview:** short structured Q&A (deep-dive 9 / quick 3), one question per turn — ideal for
  a chat agent to drive.
- **Recommend:** weekly picks with genre/tag + interview-signal scoring and human-readable
  reasons; pluggable delivery.
- **Portable:** stdlib-only (no pip, Python 3.7+), data dir via `GAMING_PROFILE_HOME`, bring-your-own Steam key.

Setup: `bash scripts/setup.sh` (walks you through API key + Steam ID) → see `SKILL.md`.
