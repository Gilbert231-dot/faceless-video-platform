"""
clip_quality.py — choose WHICH stretch of a background file to use, by measuring it.

Why this exists
---------------
The channel's best-looking video on TikTok and its softest ones came off the same
background file. Nothing in the render differed. What differed was WHERE in the
file the cursor happened to be: drive_clip_manager.plan_footage walks a cursor
through each file (`offset += take`) and uses whatever comes next, so the
delivered sharpness is a lottery decided by position.

Measured on real background files, per 60-second window: detail ranged 8.24-13.48
inside a single 30-minute file — a 1.6x spread — with the same encoder, the same
settings, the same everything. The videos that looked good on TikTok sat at the
top of that range. So most of the "why is this one soft" difference is simply
which minute of gameplay the story landed on.

This module scores the candidate windows of a file and lets the planner start at
the best one instead of at the cursor. It is deliberately conservative:

  * it only ever chooses BETWEEN windows of the same file — it cannot make a
    file look worse than its own median, and it never touches resolution, CRF,
    preset or the render;
  * the accept threshold is RELATIVE to that file's own median score, because
    absolute numbers depend on the source's resolution and codec (a 4K source
    scores very differently from a 1440p output), and a fixed threshold would be
    meaningless across files;
  * anything that goes wrong (probe failure, no candidates, tiny file) returns
    None and the planner keeps its old cursor behaviour unchanged.

What is measured
----------------
detail: mean Laplacian of a small grayscale frame — mid-frequency edge energy,
        i.e. how much real picture there is to keep. It is what survives a
        platform re-encode as visible crispness.
motion: mean frame-to-frame change. Busy footage spends a fixed bitrate on
        describing change instead of detail, so it goes soft first.
score:  detail scaled down by motion — detail * (40 / (40 + motion)), so a window
        at motion 40 keeps half its detail weight, and a calm detailed window wins.

Cost and caching
----------------
The probe is fixed-length (PROBE_LEN, default 90s) and independent of how much
footage a particular video needs, so ONE probe of a file serves every future
run regardless of story length. The map is written to clip_quality.json and
committed back by the workflow (like clip_state.json), so a file is measured
once and then it is free. Candidates are ordered to spread across the whole file
first, so even a truncated probe covers it evenly instead of measuring only the
opening minutes.

    python clip_quality.py <video>            # print the table for one file
    python clip_quality.py <video> --need 60  # ...and what it would pick
"""

import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_NAME = "clip_quality.json"          # committed by the workflow, like clip_state.json
CACHE_PATH = os.path.join(HERE, CACHE_NAME)

ENABLED = os.environ.get("CLIP_QUALITY", "true").lower() not in ("false", "0", "no", "off")
PROBE_LEN = float(os.environ.get("CLIPQ_PROBE_LEN", "90"))   # fixed window length probed
STEP = float(os.environ.get("CLIPQ_STEP", "12"))             # seconds between candidates
SAMPLE_SECONDS = float(os.environ.get("CLIPQ_SAMPLE_SEC", "2.5"))
SAMPLES_PER_WINDOW = int(os.environ.get("CLIPQ_SAMPLES", "2"))
SAMPLE_FPS = int(os.environ.get("CLIPQ_FPS", "4"))
GRAY_W, GRAY_H = 160, 284
CANDIDATE_LIMIT = int(os.environ.get("CLIPQ_CANDIDATES", "14"))
BUDGET_SEC = float(os.environ.get("CLIPQ_BUDGET_SEC", "300"))
REL_FLOOR = float(os.environ.get("CLIPQ_REL_FLOOR", "0.95"))      # of the file's own median
MOTION_CAP = float(os.environ.get("CLIPQ_MOTION_CAP", "45"))      # mush at any bitrate
MOTION_FLOOR = float(os.environ.get("CLIPQ_MOTION_FLOOR", "4"))   # near-static footage
AVOID_WINDOW_SEC = float(os.environ.get("CLIPQ_AVOID_SEC", "25"))  # don't repeat a start
RECENT_KEPT = int(os.environ.get("CLIPQ_RECENT_KEPT", "6"))
MOTION_REF = 40.0          # motion at which a window keeps half its detail weight


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def load_map(path=CACHE_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"files": {}}


def save_map(data, path=CACHE_PATH):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[clipq] could not write {path}: {e}")


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------
def _frames(path, start, duration):
    """Frames from one small slice of the source, as a grayscale array."""
    cmd = ["ffmpeg", "-v", "error", "-an", "-ss", "%.3f" % max(start, 0.0),
           "-t", "%.3f" % duration, "-i", path,
           "-vf", "fps=%d,scale=%d:%d,format=gray" % (SAMPLE_FPS, GRAY_W, GRAY_H),
           "-f", "rawvideo", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True,
                             timeout=120).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    n = len(out) // (GRAY_W * GRAY_H)
    if n < 2:
        return None
    return np.frombuffer(out[:n * GRAY_W * GRAY_H], dtype=np.uint8
                         ).reshape(n, GRAY_H, GRAY_W).astype(np.float32)


def _detail_and_motion(frames):
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    p = np.pad(frames, ((0, 0), (1, 1), (1, 1)), mode="edge")
    acc = np.zeros_like(frames)
    for dy in range(3):
        for dx in range(3):
            acc += k[dy, dx] * p[:, dy:dy + GRAY_H, dx:dx + GRAY_W]
    detail = float(np.abs(acc).mean())
    motion = float(np.abs(np.diff(frames, axis=0)).mean())
    return detail, motion


def measure_window(path, start, need):
    """detail + motion + score for the window [start, start+need). None on failure."""
    details, motions = [], []
    for i in range(SAMPLES_PER_WINDOW):
        frac = (i + 0.5) / SAMPLES_PER_WINDOW
        at = start + frac * need - SAMPLE_SECONDS / 2.0
        f = _frames(path, at, SAMPLE_SECONDS)
        if f is None:
            continue
        d, m = _detail_and_motion(f)
        details.append(d)
        motions.append(m)
    if not details:
        return None
    detail = float(np.mean(details))
    motion = float(np.mean(motions))
    score = detail * (MOTION_REF / (MOTION_REF + motion))
    return {"start": round(float(start), 2), "detail": round(detail, 3),
            "motion": round(motion, 3), "score": round(score, 3)}


def candidate_starts(duration, probe_len=PROBE_LEN, limit=CANDIDATE_LIMIT, step=STEP):
    """Candidate start times across the file, ordered for even coverage.

    The order matters: if the probe budget runs out, whatever it did measure is
    spread over the whole file instead of bunched in the opening minutes. That is
    done by bucketing the timeline and taking one candidate per bucket in each
    pass, so the first N candidates are always N-well-spread.
    """
    last = max(duration - probe_len, 0.0)
    if last <= 0:
        return [0.0]
    starts = [round(i * step, 2) for i in range(int(last // step) + 1)]
    if len(starts) > limit:
        starts = [round(i * last / (limit - 1), 2) for i in range(limit)]
    if last <= 0:
        return starts
    return sorted(starts, key=lambda s: ((s / last) * 8.0) % 1.0)


def probe_file(path, duration, entry=None, probe_len=PROBE_LEN, budget=BUDGET_SEC,
               limit=CANDIDATE_LIMIT):
    """Score this file's candidate windows, reusing anything already measured.

    Cheap by design: the window length is fixed, so one probe serves every future
    run whatever length of story it needs.
    """
    entry = dict(entry or {})
    if (abs(float(entry.get("probe_len", 0)) - probe_len) < 0.5
            and abs(entry.get("duration", 0) - round(float(duration), 1)) < 1.0
            and entry.get("windows")):
        return entry, False                     # already paid for

    starts = candidate_starts(duration, probe_len, limit)
    windows, started, probed = [], time.time(), 0
    for s in starts:
        if time.time() - started > budget:
            print(f"[clipq] probe budget {budget:.0f}s reached after {probed}/"
                  f"{len(starts)} window(s) — using what was measured")
            break
        w = measure_window(path, s, probe_len)
        if w:
            windows.append(w)
            probed += 1
    if not windows:
        return entry, False
    entry.update({"duration": round(float(duration), 1),
                  "probe_len": float(probe_len),
                  "windows": windows,
                  "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return entry, True


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def choose(entry, need, avoid=None):
    """Pick the start with the best score among this file's own windows.

    Returns (start, info) or (None, reason). The bar is the file's OWN median, so
    the comparison is between stretches of the same footage, never between files.
    """
    windows = [w for w in (entry or {}).get("windows") or [] if w.get("score")]
    if not windows:
        return None, "no measurements for this file"
    scores = sorted(w["score"] for w in windows)
    median = scores[len(scores) // 2]
    floor = median * REL_FLOOR
    avoid = list(avoid or [])

    ranked = sorted(windows, key=lambda w: -w["score"])
    reasons = []
    for w in ranked:
        if w["score"] < floor:
            reasons.append(f"best score {w['score']:.2f} is below the file's own "
                           f"median {floor:.2f}")
            break
        if w["motion"] > MOTION_CAP:
            reasons.append(f"motion {w['motion']:.1f} > cap {MOTION_CAP:.0f}")
            continue
        if w["motion"] < MOTION_FLOOR:
            reasons.append(f"motion {w['motion']:.1f} < floor {MOTION_FLOOR:.0f} "
                           f"(near-static)")
            continue
        if any(abs(w["start"] - a) < AVOID_WINDOW_SEC for a in avoid):
            continue
        return w["start"], {"chosen": w, "median": median, "floor": floor,
                            "best": ranked[0], "n": len(windows)}
    # nothing fresh: allow a repeat rather than fall back to the cursor
    for w in ranked:
        if w["score"] >= floor and MOTION_FLOOR <= w["motion"] <= MOTION_CAP:
            info = {"chosen": w, "median": median, "floor": floor,
                    "best": ranked[0], "n": len(windows), "repeat": True}
            return w["start"], info
    return None, "; ".join(reasons) or "no window passed the gates"


def recent_starts(entry, k=RECENT_KEPT):
    return [u["start"] for u in (entry or {}).get("used", [])][-k:]


def note_used(entry, start):
    entry.setdefault("used", []).append(
        {"start": round(float(start), 2), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                              time.gmtime())})
    entry["used"] = entry["used"][-40:]


def plan_start(file_id, path, need, duration, cache_path=CACHE_PATH, log=print):
    """Convenience used by the planner: probe, choose, record. None = keep the cursor.

    Returns (start, entry, info) or (None, None, reason).
    """
    if not ENABLED:
        return None, None, "CLIP_QUALITY is off"
    if duration <= need + 1.0:
        return None, None, "file is no longer than the footage needed (no choice to make)"
    data = load_map(cache_path)
    entry = data["files"].get(file_id)
    entry, fresh = probe_file(path, duration, entry)
    if not (entry or {}).get("windows"):
        return None, None, "no windows could be measured"
    start, info = choose(entry, need, avoid=recent_starts(entry))
    data["files"][file_id] = entry
    if start is not None:
        note_used(entry, start)
    save_map(data, cache_path)
    if start is None:
        return None, entry, info
    return start, entry, info


def describe(start, info, need):
    """One-line human summary of a decision, for the pipeline log."""
    if start is None:
        return f"kept the rotation cursor — {info}"
    c = info["chosen"]
    overlap = " (a repeat: every fresh window was rejected)" if info.get("repeat") else ""
    return (f"start {start:.1f}s for {need:.0f}s — scored {c['score']:.2f} "
            f"(detail {c['detail']:.2f}, motion {c['motion']:.2f}; file median "
            f"{info['median']:.2f}, best of {info['n']} windows){overlap}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Score a background file's windows")
    ap.add_argument("video")
    ap.add_argument("--need", type=float, default=60.0,
                    help="footage length a video needs (choice only; probing is fixed)")
    ap.add_argument("--budget", type=float, default=BUDGET_SEC)
    args = ap.parse_args()
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "default=nw=1:nk=1", args.video], capture_output=True, text=True).stdout.strip())
    entry, fresh = probe_file(args.video, dur, budget=args.budget)
    print(f"{os.path.basename(args.video)}: {dur:.1f}s, windows of {PROBE_LEN:.0f}s"
          f"{' (fresh)' if fresh else ' (cached)'}, {len(entry['windows'])} measured")
    print("%9s %8s %8s %8s" % ("start", "detail", "motion", "score"))
    for w in sorted(entry["windows"], key=lambda w: -w["score"]):
        print("%9.1f %8.2f %8.2f %8.2f" % (w["start"], w["detail"], w["motion"], w["score"]))
    start, info = choose(entry, args.need)
    print(f"\nneeds {args.need:.0f}s -> {describe(start, info, args.need)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
