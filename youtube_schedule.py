"""
youtube_schedule.py — pick the next free publish slots for YouTube uploads.

When YOUTUBE_PRIVACY is 'public', each run uploads its videos as PRIVATE
with a future publishAt; YouTube flips them to public automatically at
that time. Slots are spaced apart (default 12:00 / 20:00 UTC = 8 hours)
so two videos posted the same day each get room to go public alone
instead of shadowing each other. (Was 12h apart; user chose 8h — can be
retuned from analytics later via YOUTUBE_SCHEDULE_TIMES.)

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
    raw = os.environ.get("YOUTUBE_SCHEDULE_TIMES", "12:00,18:00")
    slots = [t.strip() for t in raw.split(",") if t.strip()]
    return slots or ["12:00", "18:00"]


def _read_state():
    """Read the schedule state: next_index + reference_date.

    The reference_date is the fixed anchor from which day-offsets are
    calculated.  Older state files only had next_index — those are
    handled by defaulting reference_date to today.
    """
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            idx = int(data.get("next_index", 0))
            ref_str = data.get("reference_date")
            if ref_str:
                ref_date = datetime.date.fromisoformat(ref_str)
            else:
                ref_date = datetime.date.today()
            return idx, ref_date
    except Exception:
        return 0, datetime.date.today()


def _write_state(idx, ref_date):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"next_index": idx,
                       "reference_date": ref_date.isoformat()}, f)
    except Exception:
        pass


def next_publish_times(count):
    """Return `count` ISO8601 UTC datetime strings (sorted ascending, all in
    the future) for the next free slots, advancing the persisted counter.

    Each slot is the next one that is > 1 hour away, so a run that uploads
    late just pushes both videos to the following slots — they always stay
    MIN_AHEAD or more apart and never collide with earlier runs.

    FIXED: the old code used ``now.date() + idx // slots`` as the day,
    which compounded across consecutive daily runs — each run's "today"
    was one day later AND the index had advanced, so videos drifted
    further into the future every day.  The fix anchors day-offsets to a
    FIXED reference_date stored in the state file (the date of the first
    real upload), so ``idx // slots`` always maps to the same absolute
    day regardless of when the run fires.
    """
    slots = slot_times()
    idx, ref_date = _read_state()
    now = datetime.datetime.now(datetime.timezone.utc)

    # If the reference date is stale (older than today and all its slots
    # are in the past), reset to today so we don't skip empty days.
    if ref_date < now.date():
        # Check if the NEXT slot from ref_date is still in the future.
        test_slot = slots[0]
        hh, mm = test_slot.split(":")
        earliest_from_ref = datetime.datetime(
            ref_date.year, ref_date.month, ref_date.day,
            int(hh), int(mm), tzinfo=datetime.timezone.utc)
        # If even the LAST slot of ref_date is in the past, the reference
        # is stale — but we still use it because idx determines the day
        # offset.  Only reset if idx == 0 (fresh start) and ref_date is
        # old (the state was never properly initialized).
        if idx == 0 and earliest_from_ref < now:
            ref_date = now.date()

    times = []
    idx0 = idx
    guard = idx0 + 120  # hard cap: 120 slots ahead is ~2 months of runway
    while len(times) < count and idx < guard:
        slot = slots[idx % len(slots)]
        try:
            hh, mm = slot.split(":")
            # FIXED: use ref_date (fixed anchor) instead of now.date()
            day = ref_date + datetime.timedelta(days=idx // len(slots))
            t = datetime.datetime(day.year, day.month, day.day,
                                  int(hh), int(mm), tzinfo=datetime.timezone.utc)
        except ValueError:
            t = now + datetime.timedelta(days=1)
        if t > now + MIN_AHEAD:
            times.append(t)
        idx += 1
    if not times:  # pathological: slots far past; just push 12h out
        times = [now + datetime.timedelta(hours=12)]
    _write_state(idx, ref_date)
    return [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
