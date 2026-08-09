"""
build_data.py — generate dashboard/data.json from the real story bank.

Reads:
  reddit_stories/*/stories_with_comments_*.json + stories_*.json  (the bank)
  used_story_ids.json                                            (narrated IDs)
  tiktok_schedule_state.json                                     (assigned slots)

Writes:
  dashboard/data.json  (what the dashboard renders)

Run manually after fetching stories, or let the GitHub workflow regenerate
it on every video run so the deployed dashboard stays fresh.
"""

import glob
import hashlib
import json
import os
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
STORIES_DIR = os.path.join(REPO, "reddit_stories")
USED_IDS_FILE = os.path.join(REPO, "used_story_ids.json")
SCHEDULE_FILE = os.path.join(REPO, "tiktok_schedule_state.json")
VIDEO_HISTORY_FILE = os.path.join(REPO, "video_history.json")
OUT = os.path.join(REPO, "dashboard", "data.json")
THRESHOLD = 20  # must match STORY_REFILL_THRESHOLD in tasks.py


def load_used_ids():
    try:
        with open(USED_IDS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def synth_id(subreddit, title):
    return "synth_" + hashlib.md5(
        f"{subreddit}|{title}".encode("utf-8")).hexdigest()[:12]


def load_bank():
    """All stories across the bank, deduped by id, with used-flag computed."""
    stories = []
    seen = set()
    used = load_used_ids()
    for f in (glob.glob(os.path.join(STORIES_DIR, "*", "stories_with_comments_*.json"))
              + glob.glob(os.path.join(STORIES_DIR, "*", "stories_*.json"))):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for s in (data if isinstance(data, list) else [data]):
            sid = s.get("story_id") or s.get("id")
            key = sid or synth_id(s.get("subreddit", ""), s.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
            is_used = bool(sid and sid in used) or synth_id(
                s.get("subreddit", ""), s.get("title", "")) in used
            stories.append({
                "id": key,
                "title": s.get("title", ""),
                "subreddit": s.get("subreddit", "unknown"),
                "score": s.get("score", 0),
                "added_at": s.get("added_at"),
                "used": is_used,
            })
    return stories


def load_video_history():
    """Most recent recorded videos (written by video_history.py in the workflow)."""
    try:
        with open(VIDEO_HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data[:10]
    except Exception:
        pass
    return []


def load_schedule():
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    return None


def main():
    stories = load_bank()
    total = len(stories)
    unused = sum(1 for s in stories if not s["used"])
    used = total - unused

    by_sub = {}
    for s in stories:
        d = by_sub.setdefault(s["subreddit"], {"name": s["subreddit"], "total": 0, "unused": 0})
        d["total"] += 1
        if not s["used"]:
            d["unused"] += 1
    subreddits = sorted(by_sub.values(), key=lambda d: d["unused"], reverse=True)

    added = [s["added_at"] for s in stories if s.get("added_at")]
    schedule = load_schedule()

    data = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_stories": total,
        "unused_stories": unused,
        "used_stories": used,
        "low_bank": unused <= THRESHOLD,
        "threshold": THRESHOLD,
        "oldest_added_at": min(added) if added else None,
        "newest_added_at": max(added) if added else None,
        "subreddits": subreddits,
        "schedule": schedule,
        "videos": load_video_history(),
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ Wrote {OUT}")
    print(f"   {total} stories, {unused} unused / {used} used "
          f"across {len(subreddits)} subreddits"
          + ("  ⚠️ LOW BANK" if data["low_bank"] else ""))


if __name__ == "__main__":
    main()
