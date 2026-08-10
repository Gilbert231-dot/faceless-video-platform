"""
youtube_schedule.py — pick the next free publish slots for YouTube uploads.

When YOUTUBE_PRIVACY is 'public', each run uploads its videos as PRIVATE
with a future publishAt; YouTube flips them to public automatically at
that time. Slots are spaced far apart (default 10:00 / 22:00 UTC = 12
hours) so two videos posted the same day each get room to go public
alone instead of shadowing each other.

A tiny state file (youtube_schedule_state.json, shipped by the state
push like tiktok_schedule_state.json) tracks the next free slot, so
consecutive runs never hand out the same publish time twice.
"""

import datetime
import json
import os

STATE_FILE = "youtube_schedule_state.json"

# Slots must be at least this far in the future when allocated — the
# upload itself takes a few minutes, and YouTube rejects past publishAt.
MIN_AHEAD = datetime.timedelta(hours=1)


def slot_times():
    """The daily publish slots (UTC), e.g. '10:00,22:00'. Override with
    YOUTUBE_SCHEDULE_TIMES in the workflow env (e.g. '12:00,18:00')."""
    raw = os.environ.get("YOUTUBE_SCHEDULE_TIMES", "10:00,22:00")
    slots = [t.strip() for t in raw.split(",") if t.strip()]
    return slots or ["10:00", "22:00"]


def _read_index():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return int(json.load(f).get("next_index", 0))
    except Exception:
        return 0


def _write_index(idx):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"next_index": idx}, f)
    except Exception:
        pass


def next_publish_times(count):
    """Return `count` ISO8601 UTC datetime strings (sorted ascending, all in
    the future) for the next free slots, advancing the persisted counter.

    Each slot is the next one that is > 1 hour away, so a run that uploads
    late just pushes both videos to the following slots — they always stay
    MIN_AHEAD or more apart and never collide with earlier runs.
    """
    slots = slot_times()
    idx = _read_index()
    now = datetime.datetime.now(datetime.timezone.utc)
    times = []
    idx0 = idx
    guard = idx0 + 120  # hard cap: 120 slots ahead is ~2 months of runway
    while len(times) < count and idx < guard:
        slot = slots[idx % len(slots)]
        try:
            hh, mm = slot.split(":")
            day = now.date() + datetime.timedelta(days=idx // len(slots))
            t = datetime.datetime(day.year, day.month, day.day,
                                  int(hh), int(mm), tzinfo=datetime.timezone.utc)
        except ValueError:
            t = now + datetime.timedelta(days=1)
        if t > now + MIN_AHEAD:
            times.append(t)
        idx += 1
    if not times:  # pathological: slots far past; just push 12h out
        times = [now + datetime.timedelta(hours=12)]
    _write_index(idx)
    return [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
