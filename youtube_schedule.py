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
    the future) for the next free slots.

    PER-BATCH FIXED SLOTS (no drift): each workflow batch configures exactly
    ONE slot (YOUTUBE_SCHEDULE_TIMES, e.g. '15:00' for Batch 1), and every
    run schedules its videos at the next occurrence(s) of those slots that
    are still >= MIN_AHEAD in the future — today when the run finishes in
    time, otherwise tomorrow.  There is no shared next_index counter to
    drift (the old state file consumed one full day per video once batches
    dropped to one slot, pushing uploads weeks out — the Sep-25 bug).

    Consecutive runs of the same batch can never both claim the same slot:
    the first run takes today's slot and the next day's run gets tomorrow's.
    """
    slots = slot_times()
    now = datetime.datetime.now(datetime.timezone.utc)

    times = []
    guard = 0
    # Walk forward through (day, slot) until we have `count` times, each at
    # least MIN_AHEAD in the future. Cap at 120 slots (~2 months) so a
    # pathological config can't loop forever.
    while len(times) < count and guard < 120 * max(1, len(slots)):
        slot = slots[guard % len(slots)]
        try:
            hh, mm = slot.split(":")
            day = now.date() + datetime.timedelta(days=guard // len(slots))
            t = datetime.datetime(day.year, day.month, day.day,
                                  int(hh), int(mm), tzinfo=datetime.timezone.utc)
        except ValueError:
            t = now + datetime.timedelta(days=1)
        if t > now + MIN_AHEAD:
            times.append(t)
        guard += 1
    if not times:  # pathological: slots far past; just push 12h out
        times = [now + datetime.timedelta(hours=12)]
    return [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
