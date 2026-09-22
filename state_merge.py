#!/usr/bin/env python3
"""
state_merge.py -- fold main's copy of a pipeline state file into the copy this
run just wrote, so neither side's records are thrown away.

WHY THIS EXISTS
---------------
The pipeline writes its state back to main at the end of every run: which
stories are already used (used_story_ids.json), which background file and
window each video took (clip_state.json / clip_quality.json), the video
history, the schedule counters.

Another writer can commit to main while a run is rendering -- the daily
performance tracker does exactly that.  On 2026-09-22 it did:

    09:46:56  ! [rejected]  main -> main (fetch first)
    09:50:08  ! [rejected]  main -> main (non-fast-forward)

Batch 3's push was rejected, the failure was swallowed by
`git push origin main || echo "..."`, and the run still reported success.  So
that batch's used-story mark was lost (the story can be picked and published a
second time) and its rotation memory was lost (Batch 4 read stale state and
re-picked the same background window, which is the near-duplicate signature the
rotation work exists to remove).

Retrying the push is only half the fix.  After fetching the other writer's
commit the two versions of each state file still have to be COMBINED, or one
side's marks are discarded -- silently losing the marks a second way.  That
combination is this file.

MERGE POLICY (deliberately conservative: never delete, never invent)
-------------------------------------------------------------------
    dict    every key from both sides is kept; shared keys merge recursively
    list    the two sides' items are unioned (ours first), where "the same
            item" means the same identity field when the item has one
            (video_history.json records are matched on video_file) and
            otherwise byte-identical JSON
    scalar  ours wins -- we are the writer that just ran

One documented exception, in FILE_POLICY: for video_history.json the daily
performance tracker is the authority on the metrics it writes, so a row that
exists on both sides is rebuilt from MAIN with only our extra fields filled in.
Without that, a pipeline copy of the file would revert a view count the tracker
had just refreshed (self-healing, since the tracker runs daily, but wrong).

Anything unparseable is left EXACTLY as we wrote it, untouched and unchanged.

USAGE
-----
    python state_merge.py <our-file> <their-file>      # merge in place
    python state_merge.py --check <our-file> <their-file>

Prints one line:
    SAME <path>                 nothing to fold in; file untouched
    MERGED <path> (folded in N entries from main)
    SKIP <path> (<reason>)      not JSON / unreadable; file untouched

Exit status is always 0 for SAME/MERGED/SKIP -- a state file that cannot be
merged must never block the push; it just keeps this run's version.
"""

from __future__ import annotations

import json
import os
import sys

# Which field identifies a record in a JSON list, per file.  Without this a list
# is unioned by exact equality, which still never drops anything but does leave
# a duplicate entry when the other side merely updated a record in place (the
# performance tracker adds views/likes to an existing row).
LIST_IDENTITY_FIELD = {
    "video_history.json": "video_file",
}

# Which side settles a value that BOTH sides carry, per file.  "ours" (default)
# means this run's value wins; "theirs" means main's does, with our
# extra/only-present fields still filled in.
FILE_POLICY = {
    # The tracker owns these numbers: it re-fetches views/likes every day, and a
    # pipeline copy taken at checkout time is by definition older.
    "video_history.json": "theirs",
}

# Cap on how many added entries we bother to count for the log line.  Counting
# is cosmetic; it must never be the reason a merge fails.
COUNT_LIMIT = 100000


def _identity(item, key):
    """A hashable identity for a list item."""
    if key and isinstance(item, dict) and key in item:
        return ("id", str(item[key]))
    return ("json", json.dumps(item, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")))


def merge(ours, theirs, key=None, prefer="ours"):
    """Combine two decoded JSON values under the policy in the module docstring."""
    if isinstance(ours, dict) and isinstance(theirs, dict):
        out = {}
        for k, v in ours.items():
            out[k] = merge(v, theirs[k], None, prefer) if k in theirs else v
        for k, v in theirs.items():
            if k not in ours:
                out[k] = v
        return out

    if isinstance(ours, list) and isinstance(theirs, list):
        out = []
        seen = set()
        for item in ours:
            ident = _identity(item, key)
            if ident in seen:
                continue
            seen.add(ident)
            # A record that also exists on main is merged field-wise under the
            # same policy, so "theirs" keeps their metric fields and still
            # picks up a field only we have.
            match = next((t for t in theirs if _identity(t, key) == ident), None)
            out.append(merge(item, match, None, prefer) if match is not None
                       else item)
        for item in theirs:
            ident = _identity(item, key)
            if ident not in seen:
                seen.add(ident)
                out.append(item)
        return out

    # Scalars (and type mismatches) are settled by `prefer`.
    return theirs if prefer == "theirs" else ours


def _added(merged, ours):
    """How many records main contributed (cosmetic, for the log line)."""
    if isinstance(ours, list) and isinstance(merged, list):
        return max(0, len(merged) - len(ours))
    if isinstance(ours, dict) and isinstance(merged, dict):
        return max(0, len(merged) - len(ours))
    return 0 if merged == ours else 1


def _load(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8-sig")          # tolerate a BOM
    return raw, json.loads(text)


def main(argv):
    check = False
    args = []
    for a in argv:
        if a == "--check":
            check = True
        else:
            args.append(a)

    if len(args) != 2:
        sys.stderr.write("usage: state_merge.py [--check] <our-file> <their-file>\n")
        return 2

    ours_path, theirs_path = args
    name = os.path.basename(ours_path)

    try:
        raw, ours = _load(ours_path)
    except Exception as exc:                                  # noqa: BLE001
        print("SKIP %s (our copy is not JSON: %s)" % (ours_path, exc))
        return 0

    try:
        _, theirs = _load(theirs_path)
    except Exception as exc:                                  # noqa: BLE001
        print("SKIP %s (main's copy is not JSON: %s)" % (ours_path, exc))
        return 0

    merged = merge(ours, theirs, LIST_IDENTITY_FIELD.get(name),
                   FILE_POLICY.get(name, "ours"))

    # Nothing on main that we do not already have: leave the bytes alone.  This
    # is the common case and it keeps the file's original formatting (and its
    # line endings) exactly as written, so we never churn a file for nothing.
    if merged == ours:
        print("SAME %s" % ours_path)
        return 0

    added = _added(merged, ours)

    # Preserve the file's own line endings and trailing-newline style.
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    data = json.dumps(merged, indent=2, ensure_ascii=False).encode("utf-8")
    if eol == b"\r\n":
        data = data.replace(b"\n", b"\r\n")
    if raw.endswith(b"\n"):
        data += eol
    if check:
        print("WOULD-MERGE %s (would fold in %d from main)" % (ours_path, added))
        return 0
    tmp = ours_path + ".merge.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, ours_path)          # atomic: never a half-written state file
    print("MERGED %s (folded in %d from main)" % (ours_path, added))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:                                  # noqa: BLE001
        # A merge that blows up on an unexpected shape must not block the push.
        print("SKIP %s (merge failed: %s)" % (" ".join(sys.argv[1:]), exc))
        sys.exit(0)
