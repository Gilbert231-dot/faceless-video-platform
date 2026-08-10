"""
video_history.py — record every generated/uploaded video for the dashboard.

The workflow's upload step calls record_video() after each YouTube (and
eventually TikTok) upload, appending to video_history.json (most recent
first). dashboard/build_data.py folds it into data.json, and the dashboard
renders the 'Latest videos' panel from it. The state push ships the file
back to the repo on every run.
"""

import json
import os
import time

HISTORY_FILE = "video_history.json"
MAX_ENTRIES = 100


def _safe(text):
    """ASCII-safe for consoles that can't print emoji (Windows cp1252)."""
    return str(text).encode("ascii", "replace").decode("ascii")


def record_video(video_file, metadata=None, video_id=None, status="posted",
                 platform="youtube", url=None, error=None, publish_at=None):
    """Append one video record. Never raises — a history hiccup must not
    fail the pipeline. Dedupes on video_id so re-runs don't duplicate.
    publish_at (ISO 8601 UTC) records a scheduled public time, if any."""
    try:
        entries = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    entries = data

        meta = metadata or {}
        entry = {
            "video_file": os.path.basename(video_file) if video_file else "",
            "title": meta.get("title") or (os.path.basename(video_file) if video_file else ""),
            "subreddit": meta.get("subreddit", ""),
            "platform": platform,
            "status": status,
            "video_id": video_id or "",
            "url": url or (f"https://youtu.be/{video_id}" if video_id else ""),
            "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if error:
            entry["error"] = str(error)[:200]
        if publish_at:
            entry["publish_at"] = publish_at

        if video_id:
            entries = [e for e in entries if e.get("video_id") != video_id]
        entries.insert(0, entry)

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries[:MAX_ENTRIES], f, indent=2, ensure_ascii=False)
        print(_safe(f"[video-history] recorded {status}: {entry['title'][:50]}"))
    except Exception as e:
        print(_safe(f"[video-history] WARNING: could not update {HISTORY_FILE}: {e}"))
