"""
performance_tracker.py - fetch per-video performance metrics for every upload.

Purpose: spot winning formats EARLY. For every YouTube video in
video_history.json this fetches:

  views              (YouTube Data API v3 - videos.list, stats)
  avg_view_duration  (YouTube Analytics API - averageViewDuration, seconds)
  watch_time_min     (YouTube Analytics API - estimatedMinutesWatched)
  completion_pct     avg_view_duration / actual video duration * 100

and merges the results back into video_history.json (new fields: views,
avg_view_duration_sec, watch_time_min, completion_pct, stats_at), so the
dashboard's "Latest videos" panel can rank them.

Auth: uses the SAME YouTube OAuth secrets as the pipeline
(YOUTUBE_REFRESH_TOKEN / YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET).

IMPORTANT: the analytics fields need the youtubeAnalytics.readonly scope
on the refresh token. The scope list lives in youtube_setup.py; if the
token was minted WITHOUT it, the analytics part degrades gracefully to
views/likes/duration only (videos.list stats) and prints a hint.

Usage:
  python performance_tracker.py                 # all YouTube videos in history
  python performance_tracker.py --top 10        # just print the ranking, don't save
  python performance_tracker.py --days 7        # analytics range = last 7 days
"""

import json
import os
import re
import sys
import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_history.json")

# youtube.upload is included so the refresh token works exactly like the
# pipeline's; youtube.readonly powers videos.list; youtubeAnalytics.readonly
# powers the analytics endpoint (falls back gracefully when absent).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtubeAnalytics.readonly",
]

UA = {"User-Agent": "faceless-performance-tracker/1.0"}


def _safe(text):
    return str(text).encode("ascii", "replace").decode("ascii")


def get_services():
    """Return (youtube_v3, youtube_analytics) clients or raise."""
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    if not all([refresh_token, client_id, client_secret]):
        missing = [n for n, v in {
            "YOUTUBE_REFRESH_TOKEN": refresh_token,
            "YOUTUBE_CLIENT_ID": client_id,
            "YOUTUBE_CLIENT_SECRET": client_secret,
        }.items() if not v]
        raise RuntimeError("Missing YouTube OAuth secrets in environment: " + ", ".join(missing))

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    yt = build("youtube", "v3", credentials=creds)
    ya = build("youtubeAnalytics", "v2", credentials=creds, developerKey=None)
    return yt, ya


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def parse_duration(iso):
    """PT1M23S -> 83. Return None when unparseable."""
    try:
        d = iso.replace("PT", "").replace("P", "")
        mh = re.match(r"(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", d)
        if not mh:
            return None
        h = int(mh.group(1) or 0)
        m = int(mh.group(2) or 0)
        s = float(mh.group(3) or 0)
        return h * 3600 + m * 60 + s
    except Exception:
        return None


def fetch_video_details(yt, video_ids):
    """videos.list -> {video_id: {views, likes, comments, duration_sec}}."""
    out = {}
    # API caps ids at 50 per call
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = yt.videos().list(part="statistics,contentDetails", id=",".join(batch)).execute()
        except HttpError as e:
            print(_safe(f"  videos.list failed for batch ({e.resp.status}): {e.reason}"))
            continue
        for item in resp.get("items", []):
            st = item.get("statistics", {})
            cd = item.get("contentDetails", {})
            out[item["id"]] = {
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "duration_sec": parse_duration(cd.get("duration", "")),
            }
    return out


def fetch_analytics(ya, video_id, days):
    """averageViewDuration + estimatedMinutesWatched for one video (best effort)."""
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    end = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        resp = ya.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics="averageViewDuration,estimatedMinutesWatched",
            filters=f"video=={video_id}",
        ).execute()
        rows = resp.get("rows") or []
        if not rows:
            return {}
        avg, mins = rows[0]
        return {"avg_view_duration_sec": round(float(avg), 1),
                "watch_time_min": round(float(mins), 1)}
    except HttpError as e:
        if e.resp.status in (403, 400):
            # scope missing / channel not eligible
            raise
        return {}
    except Exception:
        return {}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=0,
                    help="print the ranking of the N best videos and exit (don't save)")
    ap.add_argument("--days", type=int, default=3650,
                    help="analytics date range in days (default: lifetime)")
    args = ap.parse_args()

    history = load_history()
    yt_ids = [e["video_id"] for e in history if e.get("platform") == "youtube" and e.get("video_id")]
    if not yt_ids:
        print("[performance] No YouTube videos in history yet.")
        return 0

    print(_safe(f"[performance] Fetching stats for {len(yt_ids)} YouTube videos..."))
    yt, ya = None, None
    try:
        yt, ya = get_services()
    except RuntimeError as e:
        print(_safe(f"[performance] {e}"))
        return 1

    details = fetch_video_details(yt, yt_ids)
    print(_safe(f"[performance] Got basic stats for {len(details)} videos."))

    analytics_ok = True
    for e in history:
        if e.get("platform") != "youtube" or not e.get("video_id"):
            continue
        vid = e["video_id"]
        d = details.get(vid)
        if not d:
            continue
        e["views"] = d["views"]
        e["likes"] = d["likes"]
        e["comments"] = d["comments"]
        dur = d["duration_sec"]
        if dur:
            e["duration_sec"] = dur
        try:
            a = fetch_analytics(ya, vid, args.days)
            if a:
                e["avg_view_duration_sec"] = a["avg_view_duration_sec"]
                e["watch_time_min"] = a["watch_time_min"]
                if dur and a["avg_view_duration_sec"]:
                    e["completion_pct"] = round(min(100.0, a["avg_view_duration_sec"] / dur * 100), 1)
        except HttpError as err:
            if err.resp.status in (403, 400):
                analytics_ok = False
                break  # scope/eligibility issue affects every video - stop trying
            # 401/410/429 etc: continue with the rest

    if not analytics_ok:
        print(_safe("\n[performance] NOTE: analytics unavailable (missing "
                    "youtubeAnalytics.readonly scope or channel not eligible)."))
        print(_safe("            Showing views/likes only. Re-run youtube_setup.py after"))
        print(_safe("            adding 'youtubeAnalytics.readonly' to SCOPES to unlock"))
        print(_safe("            average view duration + completion %."))

    stats_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for e in history:
        if e.get("platform") == "youtube" and e.get("views") is not None:
            e["stats_at"] = stats_at

    if args.top:
        rows = [e for e in history
                if e.get("platform") == "youtube" and e.get("views") is not None]
        rows.sort(key=lambda e: (e.get("completion_pct") or 0) if e.get("views", 0) >= 50 else -1,
                  reverse=True)
        print("\n=== TOP PERFORMERS (completion % among videos with >=50 views, "
              "then by views) ===")
        rows.sort(key=lambda e: (
            e.get("completion_pct") if e.get("views", 0) >= 50 else -1,
            e.get("views", 0)), reverse=True)
        for e in rows[:args.top]:
            sub = e.get("subreddit", "")
            print(_safe(f"  {e.get('completion_pct', 0):5.1f}%  {e.get('views', 0):>6} views  "
                        f"{e.get('avg_view_duration_sec', 0):6.1f}s avg  "
                        f"r/{sub:<18} {e.get('title', '')[:55]}"))
        print("\n=== MOST VIEWS ===")
        by_views = sorted([e for e in history if e.get("platform") == "youtube" and e.get("views")],
                          key=lambda e: e["views"], reverse=True)
        for e in by_views[:args.top]:
            print(_safe(f"  {e['views']:>6} views  r/{e.get('subreddit', ''):<18} "
                        f"{e.get('title', '')[:55]}"))
        return 0

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(_safe(f"[performance] Updated {HISTORY_FILE} ({len(history)} entries, "
                f"stats_at={stats_at})."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
