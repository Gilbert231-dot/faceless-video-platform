"""
schedule_public.py — one-off tool: clean up a test video and send it public.

Usage (via the schedule_public.yml workflow, which supplies the OAuth
secrets):  python schedule_public.py <VIDEO_ID>

  - Strips a "[TEST] " prefix from the title (testing is done — the prefix
    must not ship to the public channel).
  - Schedules the video PUBLIC at the next free pipeline slot (12:00/20:00
    UTC, same logic as youtube_schedule.next_publish_times) unless
    --publish-at is given.
  - Optional: --title "..." to set an exact title instead of stripping.

Runs from GitHub Actions so the refresh token stays in secrets; the
script itself only needs the standard YOUTUBE_* env vars.
"""

import argparse
import sys

from googleapiclient.errors import HttpError

from youtube_schedule import next_publish_times
from youtube_uploader import get_authenticated_service

TEST_PREFIX = "[TEST] "


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video_id", help="YouTube video ID to update")
    ap.add_argument("--title", help="exact new title (default: strip '[TEST] ' prefix)")
    ap.add_argument("--publish-at", help="ISO8601 UTC publish time (default: next free slot)")
    args = ap.parse_args()

    youtube = get_authenticated_service()

    # fetch current snippet/status so the update preserves everything else
    resp = youtube.videos().list(part="snippet,status", id=args.video_id).execute()
    items = resp.get("items", [])
    if not items:
        print(f"❌ Video {args.video_id} not found or not accessible.")
        sys.exit(1)
    video = items[0]

    old_title = video["snippet"]["title"]
    if args.title:
        new_title = args.title
    elif old_title.startswith(TEST_PREFIX):
        new_title = old_title[len(TEST_PREFIX):]
    else:
        new_title = old_title
    video["snippet"]["title"] = new_title

    publish_at = args.publish_at or next_publish_times(1)[0]
    video["status"]["privacyStatus"] = "public"
    video["status"]["publishAt"] = publish_at

    print(f"📝 Title: '{old_title}'")
    print(f"        -> '{new_title}'")
    print(f"🕐 Scheduling PUBLIC at {publish_at}")

    try:
        youtube.videos().update(part="snippet,status", body=video).execute()
    except HttpError as e:
        print(f"❌ YouTube API error: {e}")
        sys.exit(1)

    print(f"✅ Updated -> https://youtu.be/{args.video_id} (goes public {publish_at})")


if __name__ == "__main__":
    main()
