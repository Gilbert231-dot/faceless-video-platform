"""
schedule_public.py — one-off tool: clean up a test video and send it public.

Usage (via the schedule_public.yml workflow, which supplies the OAuth
secrets):  python schedule_public.py <VIDEO_ID>

  - Strips a "[TEST] " prefix from the title (testing is done — the prefix
    must not ship to the public channel).
  - Schedules the video PUBLIC at the next free pipeline slot (12:00/20:00
    UTC, same logic as youtube_schedule.next_publish_times) unless
    --publish-at is given, or makes it public immediately with --now.
  - Optional: --title "..." to set an exact title instead of stripping.

Runs from GitHub Actions so the refresh token stays in secrets.

Scope note: videos().update requires youtube.force-ssl — youtube.upload
and youtube.readonly alone are rejected with "insufficient authentication
scopes". This module builds its own client with all three scopes, so the
refresh token MUST have been minted with force-ssl (see youtube_setup.py).
"""

import argparse
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from youtube_schedule import next_publish_times

TEST_PREFIX = "[TEST] "

# videos().update needs youtube.force-ssl; list needs youtube.readonly.
# The uploader module keeps its narrower scope so plain uploads keep
# working with older tokens, but a token used here must have all three.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def get_client():
    """Build a YouTube client with the full scope set (incl. force-ssl)."""
    refresh_token = __import__("os").getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = __import__("os").getenv("YOUTUBE_CLIENT_ID")
    client_secret = __import__("os").getenv("YOUTUBE_CLIENT_SECRET")
    if not all([refresh_token, client_id, client_secret]):
        raise RuntimeError("Missing YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video_id", help="YouTube video ID to update")
    ap.add_argument("--title", help="exact new title (default: strip '[TEST] ' prefix)")
    ap.add_argument("--publish-at", help="ISO8601 UTC publish time (default: next free slot)")
    ap.add_argument("--now", action="store_true", help="make public immediately (no schedule)")
    args = ap.parse_args()

    youtube = get_client()

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

    if args.now:
        publish_at = None
        video["status"]["privacyStatus"] = "public"
        video["status"].pop("publishAt", None)
    else:
        publish_at = args.publish_at or next_publish_times(1)[0]
        # publishAt is only accepted while the video is PRIVATE; YouTube
        # flips it to public itself at that time (same rule as uploads).
        video["status"]["privacyStatus"] = "private"
        video["status"]["publishAt"] = publish_at

    print(f"📝 Title: '{old_title}'")
    print(f"        -> '{new_title}'")
    if publish_at:
        print(f"🕐 Scheduling PUBLIC at {publish_at} (privacy stays private until then)")
    else:
        print("🕐 Making PUBLIC immediately")

    try:
        youtube.videos().update(part="snippet,status", body=video).execute()
    except HttpError as e:
        print(f"❌ YouTube API error: {e}")
        sys.exit(1)

    if publish_at:
        print(f"✅ Updated -> https://youtu.be/{args.video_id} (goes public {publish_at})")
    else:
        print(f"✅ Updated -> https://youtu.be/{args.video_id} (public now)")


if __name__ == "__main__":
    main()
