"""
update_agent_state.py — move an agent around the Story Lab agent room.

The room (dashboard/agent_room.html) is a personal FUN SIDE PROJECT
visualization (animated characters in a mock HQ). It is NOT part of the
main product (the Reddit-story -> narrated short-video -> YouTube/TikTok/
Facebook publishing pipeline). The room page carries a visible disclosure
banner saying exactly that, and so does the landing page (index.html).

This script is the single source of truth for an agent's station, activity,
and the activity log. The pipeline calls it at milestones
(generate_video.yml), and it can be run by hand whenever Buffy / You / Bro
do something outside a run.

Usage:
  python update_agent_state.py buffy "PROJECT FOLDER" "Fixing the caption size"
  python update_agent_state.py you   "LAPTOP"         "Downloading the artifact"
  python update_agent_state.py bro   "BRO'S DESK"     "Standing by, ready to help"
"""

import glob
import json
import os
import sys
import time

STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard", "agent_state.json"
)

# Top N stories to surface on the room's STORY WALL (by score).
STORY_WALL_COUNT = 3
STORY_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "reddit_stories"),
]

AGENTS = {
    "buffy": {"name": "Buffy", "color": "#a78bfa", "default": "PROJECT FOLDER"},
    "you":   {"name": "You",   "color": "#4ade80", "default": "LOUNGE"},
    "bro":   {"name": "Bro",   "color": "#fb923c", "default": "BRO'S DESK"},
}


def load():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"room": "Story Lab HQ", "agents": {}, "log": []}


def save(state):
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def top_stories(count=STORY_WALL_COUNT):
    """Pull the highest-scoring usable stories from the story bank for the wall."""
    picks = []
    for base in STORY_DIRS:
        for path in sorted(glob.glob(os.path.join(base, "*", "stories_with_comments_*.json"))):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            sub = os.path.basename(os.path.dirname(path))
            for s in data:
                if not isinstance(s, dict):
                    continue
                title = (s.get("title") or "").strip()
                body = (s.get("story") or s.get("selftext") or "").strip()
                if not title or len(body) < 300:
                    continue  # too short to be a real story
                try:
                    score = int(s.get("score") or 0)
                except (TypeError, ValueError):
                    score = 0
                picks.append({
                    "subreddit": sub,
                    "title": title[:90],
                    "score": score,
                    "comments": s.get("num_comments") or 0,
                    "url": s.get("url") or "",
                })
    picks.sort(key=lambda p: p["score"], reverse=True)
    return picks[:count]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    key = sys.argv[1].lower()
    if key not in AGENTS:
        print(f"Unknown agent '{key}'. Known: {', '.join(AGENTS)}")
        return 1
    station = sys.argv[2].upper()
    activity = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "Busy"

    state = load()
    agents = state.setdefault("agents", {})
    meta = AGENTS[key]
    agent = agents.setdefault(key, {"name": meta["name"], "color": meta["color"]})
    agent["station"] = station
    agent["activity"] = activity

    state.setdefault("log", []).insert(0, {
        "t": time.strftime("%H:%M:%S", time.gmtime()) + " UTC",
        "who": meta["name"],
        "station": station,
        "activity": activity,
    })
    state["log"] = state["log"][:30]

    # Refresh the STORY WALL with the current top picks every time state changes.
    state["stories"] = top_stories()

    save(state)
    # ASCII-safe (Windows cp1252 consoles choke on emoji)
    print(f"[OK] {meta['name']} -> {station}: {activity}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
