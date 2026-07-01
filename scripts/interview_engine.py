#!/usr/bin/env python3
"""
interview_engine.py — Agent-callable gaming-interview engine.

Runs inside the OpenClaw agent "Courtney" over Discord: one Discord message per turn.
This is NOT a blocking input loop. Each invocation is one stateless request/response:

    python3 scripts/interview_engine.py <op> [--json '<payload>']

and prints exactly ONE JSON object to stdout. Courtney calls an op, posts the returned
`response` text to Discord, and on the user's reply calls `answer` again.

State files:
    memory/interview_state.json    active session (deleted on completion/cancel; gitignored)
    memory/gaming-profile-v0.json  answers folded into game_deep_dives + global preferences

Ops:
    status     {}                                         -> active session + progress
    next_game  {mode?: auto|deep_dive|quick}              -> next queued game (does not start)
    start      {game, appid?, playtime_hours?, mode?}     -> first question
    answer     {answer}                                   -> next question or completion
    skip       {}                                         -> skip current topic, advance
    cancel     {}                                         -> clear session (keeps partial answers)

Stdlib only; all profile writes are atomic.
"""

import json
import os
import random
import sys

from steam_common import (atomic_write_json, normalize_game_key,
                          GAMING_PROFILE_PATH as PROFILE_PATH,
                          INTERVIEW_STATE_PATH as STATE_PATH)

DEEP_DIVE_MIN_HOURS = 10

QUESTIONS = {
    "general_opinion": "What's your overall opinion on {game}? What makes it special (or not) for you?",
    "mechanics_love": "What mechanics do you love in {game}? (e.g., crafting, combat, exploration, progression, physics)",
    "mechanics_hate": "Are there any mechanics in {game} that you dislike or would change? What and why?",
    "difficulty": "What difficulty do you usually play {game} on? Easy, Normal, Hard, Very Hard? Do you adjust per game?",
    "session_length": "When you play {game}, what's your typical session? Short (30min), medium (1-2h), or long (3h+ marathons)?",
    "story_vs_gameplay": "In {game}, do you play more for the story or the gameplay? Or both equally?",
    "mood": "What mood or feeling do you associate with {game}? (e.g., relaxing, intense, power fantasy, philosophical, chaotic, competitive, social)",
    "genre": "What genre would you classify {game} as? (e.g., action RPG, sim racing, puzzle, sandbox, fighting)",
    "replay": "Would you replay {game}? How many times have you played through it?",
}

DEEP_TOPICS = ["general_opinion", "mechanics_love", "mechanics_hate", "difficulty",
               "session_length", "story_vs_gameplay", "mood", "genre", "replay"]
QUICK_FIXED = ["general_opinion"]
QUICK_POOL = ["mechanics_love", "mechanics_hate", "mood", "replay"]

# topics whose answers accumulate as lists rather than overwrite
LIST_TOPICS = {"mechanics_love", "mechanics_hate", "mood"}

EMPTY_ANSWERS = {
    "general_opinion": "", "mechanics_love": [], "mechanics_hate": [], "difficulty": "",
    "session_length": "", "story_vs_gameplay": "", "mood": [], "genre": "", "replay": "",
}


# ---------- IO helpers ----------

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def load_profile():
    return _load_json(PROFILE_PATH, None)


def save_profile(profile):
    atomic_write_json(PROFILE_PATH, profile)


def load_state():
    return _load_json(STATE_PATH, None)


def save_state(state):
    atomic_write_json(STATE_PATH, state)


def clear_state():
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


def _now():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# ---------- queue / lookup ----------

def _queue(profile):
    return profile.get("interview_progress", {}).get("queue", [])


def _find_in_queue(profile, game_name):
    key = normalize_game_key(game_name)
    for item in _queue(profile):
        if normalize_game_key(item["name"]) == key:
            return item
    return None


def _resolve_mode(mode, playtime_hours):
    if mode in ("deep_dive", "quick"):
        return mode
    return "deep_dive" if (playtime_hours or 0) >= DEEP_DIVE_MIN_HOURS else "quick"


def _topics_for(mode):
    if mode == "deep_dive":
        return list(DEEP_TOPICS)
    return list(QUICK_FIXED) + random.sample(QUICK_POOL, 2)


# ---------- profile folding ----------

def _ensure_dive(profile, game_name, appid, playtime_hours, mode):
    key = normalize_game_key(game_name)
    dives = profile.setdefault("game_deep_dives", {})
    if key not in dives:
        dives[key] = {
            "name": game_name,
            "appid": appid or 0,
            "playtime_hours": playtime_hours or 0,
            "platform": "steam",
            "mode": mode,
            "answers": dict(EMPTY_ANSWERS),
            "interview_complete": False,
            "interview_source": "courtney_discord",
            "interview_started": _now(),
            "interview_updated": _now(),
            "questions_answered": 0,
            "questions_total": len(DEEP_TOPICS) if mode == "deep_dive" else 3,
        }
    return dives[key]


def _upsert_mechanic(prefs, bucket, keyword, game_name):
    kw = (keyword or "").strip()
    if not kw:
        return
    norm = normalize_game_key(kw)
    for obj in prefs[bucket]:
        if normalize_game_key(obj.get("keyword", "")) == norm:
            if game_name not in obj["source_games"]:
                obj["source_games"].append(game_name)
            return
    prefs[bucket].append({"keyword": kw, "source_games": [game_name], "weight": 1.0})


def _fold_answer(profile, game_name, topic, answer):
    """Record an answer into both the per-game dive and the global preferences."""
    dive = profile["game_deep_dives"][normalize_game_key(game_name)]
    ans = dive["answers"]
    if topic in LIST_TOPICS:
        if answer not in ans[topic]:
            ans[topic].append(answer)
    else:
        ans[topic] = answer
    dive["interview_updated"] = _now()
    dive["questions_answered"] = sum(1 for v in ans.values() if v)

    prefs = profile.setdefault("preferences", {})
    prefs.setdefault("mechanics_love", [])
    prefs.setdefault("mechanics_hate", [])
    prefs.setdefault("mood_affinities", {})
    prefs.setdefault("genre_affinities", {})

    if topic == "mechanics_love":
        _upsert_mechanic(prefs, "mechanics_love", answer, game_name)
    elif topic == "mechanics_hate":
        _upsert_mechanic(prefs, "mechanics_hate", answer, game_name)
    elif topic == "difficulty":
        prefs["difficulty_tolerance"] = answer
    elif topic == "session_length":
        prefs["session_length"] = answer
    elif topic == "story_vs_gameplay":
        prefs["story_vs_gameplay"] = answer
    elif topic == "mood":
        mood_key = normalize_game_key(answer)[:40] or "unspecified"
        bucket = prefs["mood_affinities"].setdefault(mood_key, [])
        if game_name not in bucket:
            bucket.append(game_name)
    elif topic == "genre":
        gkey = normalize_game_key(answer)
        if gkey:
            prefs["genre_affinities"][gkey] = prefs["genre_affinities"].get(gkey, 0) + 1


def _finalize(profile, state):
    game_name = state["game"]
    key = normalize_game_key(game_name)
    dive = profile["game_deep_dives"][key]
    dive["interview_complete"] = True
    dive["interview_updated"] = _now()
    dive["mode"] = state["mode"]

    ip = profile.setdefault("interview_progress", {})
    ip["last_interview"] = game_name
    if state["mode"] == "deep_dive":
        ip["deep_dive_completed"] = ip.get("deep_dive_completed", 0) + 1
    else:
        ip["quick_completed"] = ip.get("quick_completed", 0) + 1
    # drop from queue
    ip["queue"] = [q for q in ip.get("queue", []) if normalize_game_key(q["name"]) != key]


def _summary(profile):
    dives = profile.get("game_deep_dives", {})
    prefs = profile.get("preferences", {})
    return {
        "games_interviewed": len(dives),
        "loved_mechanics": len(prefs.get("mechanics_love", [])),
        "hated_mechanics": len(prefs.get("mechanics_hate", [])),
        "moods": list(prefs.get("mood_affinities", {}).keys()),
    }


# ---------- operations ----------

def op_status(_payload):
    profile = load_profile()
    if not profile:
        return {"ok": False, "error": "No gaming-profile-v0.json. Run build_gaming_profile.py first."}
    state = load_state()
    active = None
    if state:
        active = {
            "game": state["game"],
            "mode": state["mode"],
            "question_number": state["current_index"] + 1,
            "total": len(state["topics"]),
        }
    ip = profile.get("interview_progress", {})
    return {
        "ok": True,
        "active_session": active,
        "progress": {
            "deep_dive_eligible": ip.get("deep_dive_eligible", 0),
            "deep_dive_completed": ip.get("deep_dive_completed", 0),
            "quick_completed": ip.get("quick_completed", 0),
            "queue_remaining": len(ip.get("queue", [])),
        },
    }


def op_next_game(payload):
    profile = load_profile()
    if not profile:
        return {"ok": False, "error": "No gaming-profile-v0.json. Run build_gaming_profile.py first."}
    mode = payload.get("mode", "auto")
    for item in _queue(profile):
        if mode in ("deep_dive", "quick") and item.get("mode") != mode:
            continue
        return {"ok": True, "game": item["name"], "appid": item.get("appid"),
                "playtime_hours": item.get("playtime_hours"), "mode": item.get("mode")}
    return {"ok": True, "game": None, "message": "Interview queue is empty for this mode."}


def op_start(payload):
    profile = load_profile()
    if not profile:
        return {"ok": False, "error": "No gaming-profile-v0.json. Run build_gaming_profile.py first."}
    if load_state():
        return {"ok": False, "error": "An interview is already active. Call answer/cancel first.",
                "active_session": op_status({})["active_session"]}

    game_name = payload.get("game")
    if not game_name:
        return {"ok": False, "error": "Missing 'game'."}

    queue_item = _find_in_queue(profile, game_name)
    playtime = payload.get("playtime_hours")
    if playtime is None and queue_item:
        playtime = queue_item.get("playtime_hours")
    appid = payload.get("appid")
    if appid is None and queue_item:
        appid = queue_item.get("appid")

    mode = _resolve_mode(payload.get("mode", "auto"), playtime)
    topics = _topics_for(mode)

    _ensure_dive(profile, game_name, appid, playtime, mode)
    save_profile(profile)

    state = {
        "game": game_name,
        "appid": appid,
        "playtime_hours": playtime,
        "mode": mode,
        "topics": topics,
        "current_index": 0,
        "started": _now(),
    }
    save_state(state)

    return {
        "ok": True,
        "response": QUESTIONS[topics[0]].format(game=game_name),
        "question_number": 1,
        "total_questions": len(topics),
        "game": game_name,
        "mode": mode,
    }


def _advance(profile, state):
    """Persist state+profile and return the next-question payload, or finalize."""
    idx = state["current_index"]
    if idx >= len(state["topics"]):
        _finalize(profile, state)
        save_profile(profile)
        clear_state()
        return {
            "ok": True, "done": True,
            "response": f"Interview for {state['game']} complete — your profile has been updated.",
            "profile_summary": _summary(profile),
        }
    save_profile(profile)
    save_state(state)
    return {
        "ok": True,
        "response": QUESTIONS[state["topics"][idx]].format(game=state["game"]),
        "question_number": idx + 1,
        "total_questions": len(state["topics"]),
        "game": state["game"],
    }


def op_answer(payload):
    state = load_state()
    if not state:
        return {"ok": False, "error": "No active interview. Start one with the 'start' op."}
    profile = load_profile()
    if not profile:
        return {"ok": False, "error": "No gaming-profile-v0.json."}
    answer = (payload.get("answer") or "").strip()
    if not answer:
        return {"ok": False, "error": "Missing 'answer'."}

    topic = state["topics"][state["current_index"]]
    _fold_answer(profile, state["game"], topic, answer)
    state["current_index"] += 1
    return _advance(profile, state)


def op_skip(_payload):
    state = load_state()
    if not state:
        return {"ok": False, "error": "No active interview."}
    profile = load_profile()
    if not profile:
        return {"ok": False, "error": "No gaming-profile-v0.json."}
    state["current_index"] += 1
    return _advance(profile, state)


def op_cancel(_payload):
    state = load_state()
    clear_state()
    return {"ok": True, "cancelled": bool(state),
            "message": "Interview cancelled; partial answers kept." if state else "No active interview."}


OPS = {
    "status": op_status, "next_game": op_next_game, "start": op_start,
    "answer": op_answer, "skip": op_skip, "cancel": op_cancel,
}


def _parse_flags(argv):
    """Build the payload from either --json '<obj>' or individual convenience flags.
    Flags avoid embedding JSON (with apostrophes etc.) inside shell quotes — e.g.
        answer --answer "I love the combat in Liar's Bar"
        start  --game "Liar's Bar" --mode auto
    Convenience flags override matching --json keys."""
    payload = {}
    if "--json" in argv:
        i = argv.index("--json")
        payload = json.loads(argv[i + 1])  # may raise; caught by caller
    flag_map = {"--game": "game", "--mode": "mode", "--answer": "answer",
                "--appid": "appid", "--playtime": "playtime_hours"}
    for flag, key in flag_map.items():
        if flag in argv:
            val = argv[argv.index(flag) + 1]
            if key in ("appid",):
                val = int(val)
            elif key == "playtime_hours":
                val = float(val)
            payload[key] = val
    return payload


def main(argv):
    if len(argv) < 2 or argv[1] not in OPS:
        print(json.dumps({"ok": False, "error":
              f"Usage: interview_engine.py {{{'|'.join(OPS)}}} "
              "[--json '<payload>'] [--game N] [--mode auto|deep_dive|quick] [--answer TEXT]"}))
        return 1
    op = argv[1]
    try:
        payload = _parse_flags(argv)
    except (ValueError, IndexError) as e:
        print(json.dumps({"ok": False, "error": f"Bad arguments: {e}"}))
        return 1
    print(json.dumps(OPS[op](payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
