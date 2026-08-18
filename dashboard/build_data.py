"""
build_data.py — generate dashboard/data.json from the real story bank.

Reads:
  reddit_stories/*/stories_with_comments_*.json + stories_*.json  (the bank)
  used_story_ids.json                                            (narrated IDs)
  tiktok_schedule_state.json                                     (assigned slots)
  youtube_schedule_state.json + YOUTUBE_PRIVACY                 (next publishes)

Writes:
  dashboard/data.json  (what the dashboard renders)

Run manually after fetching stories, or let the GitHub workflow regenerate
it on every video run so the deployed dashboard stays fresh.
"""

import datetime
import glob
import hashlib
import json
import os
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
STORIES_DIR = os.path.join(REPO, "reddit_stories")
USED_IDS_FILE = os.path.join(REPO, "used_story_ids.json")
SCHEDULE_FILE = os.path.join(REPO, "tiktok_schedule_state.json")
YT_SCHEDULE_FILE = os.path.join(REPO, "youtube_schedule_state.json")
YT_PRIVACY_FILE = os.path.join(REPO, "YOUTUBE_PRIVACY")
VIDEO_HISTORY_FILE = os.path.join(REPO, "video_history.json")
OUT = os.path.join(REPO, "dashboard", "data.json")
THRESHOLD = 100  # must match MIN_UNUSED in fetch_stories.py (the refill point)
VIDEOS_PER_DAY = 2  # daily cadence (midnight UTC run makes 2 videos)

# Default YouTube publish slots (must match youtube_schedule.py)
DEFAULT_YT_SLOTS = ["12:00", "18:00"]


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


def load_youtube_schedule():
    """Next YouTube publish slots (UTC) + current privacy setting."""
    next_index = 0
    try:
        with open(YT_SCHEDULE_FILE, encoding="utf-8") as f:
            next_index = int(json.load(f).get("next_index", 0))
    except Exception:
        pass

    # Mirror youtube_schedule.slot_times() env override + default
    raw = os.environ.get("YOUTUBE_SCHEDULE_TIMES", ",".join(DEFAULT_YT_SLOTS))
    slots = [t.strip() for t in raw.split(",") if t.strip()] or DEFAULT_YT_SLOTS

    now = datetime.datetime.now(datetime.timezone.utc)
    upcoming = []
    idx = next_index
    # Slots cycle through the day list; show the next 2 future publish times
    while len(upcoming) < 2 and idx < next_index + 60:
        slot = slots[idx % len(slots)]
        try:
            hh, mm = slot.split(":")
            day = now.date() + datetime.timedelta(days=idx // len(slots))
            t = datetime.datetime(day.year, day.month, day.day,
                                  int(hh), int(mm), tzinfo=datetime.timezone.utc)
        except ValueError:
            t = now + datetime.timedelta(days=1)
        if t > now + datetime.timedelta(hours=1):
            upcoming.append(t)
        idx += 1

    privacy = "private"
    try:
        with open(YT_PRIVACY_FILE, encoding="utf-8") as f:
            privacy = f.read().strip().lower()
    except Exception:
        pass
    if privacy not in ("private", "unlisted", "public"):
        privacy = "private"

    return {
        "privacy": privacy,
        "slots": slots,
        "next_publish_utc": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in upcoming],
    }


def main():
    stories = load_bank()
    unused = sum(1 for s in stories if not s["used"])
    # FIXED: used_stories used to be computed as bank_total - unused, but the
    # daily cleanup (cleanup_used_stories.py) DELETES used stories from the
    # bank, so that count collapsed to ~5 and never grew. The true narrated
    # count lives in used_story_ids.json - read it directly.
    used = len(load_used_ids())
    total = unused + used

    by_sub = {}
    for s in stories:
        d = by_sub.setdefault(s["subreddit"], {"name": s["subreddit"], "total": 0, "unused": 0})
        d["total"] += 1
        if not s["used"]:
            d["unused"] += 1
    subreddits = sorted(by_sub.values(), key=lambda d: d["unused"], reverse=True)

    added = [s["added_at"] for s in stories if s.get("added_at")]
    schedule = load_schedule()
    yt = load_youtube_schedule()

    # Story runway: at 2 videos/day, how long until the bank runs dry?
    days_left = unused // VIDEOS_PER_DAY
    runway_date = (datetime.date.today()
                   + datetime.timedelta(days=days_left)).isoformat()

    data = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_stories": total,
        "unused_stories": unused,
        "used_stories": used,
        "low_bank": unused <= THRESHOLD,
        "threshold": THRESHOLD,
        "days_of_stories": days_left,
        "runway_date": runway_date,
        "videos_per_day": VIDEOS_PER_DAY,
        "oldest_added_at": min(added) if added else None,
        "newest_added_at": max(added) if added else None,
        "subreddits": subreddits,
        "schedule": schedule,
        "youtube": yt,
        "videos": load_video_history(),
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[build_data] Wrote {OUT}")
    print(f"   {total} stories, {unused} unused / {used} used "
          f"across {len(subreddits)} subreddits"
          + ("  [LOW BANK]" if data["low_bank"] else ""))
    print(f"   Story runway: ~{days_left} days at {VIDEOS_PER_DAY}/day (dry ~{runway_date})")
    print(f"   YouTube: privacy={yt['privacy']}, next publishes={yt['next_publish_utc']}")


if __name__ == "__main__":
    main()
