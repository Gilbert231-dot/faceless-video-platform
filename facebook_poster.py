"""
facebook_poster.py — post generated videos to a Facebook Page via the official
Graph API Video API (no unofficial bots, nothing that can get the account
banned).

Flow per video (Graph API v25.0):
  1. Resumable Upload API: POST /{APP_ID}/uploads  ->  upload:<session_id>
  2. POST /upload:<session_id> with the mp4 bytes (file_offset 0) -> file handle
  3. POST graph-video.facebook.com /{PAGE_ID}/videos with the handle,
     title, description, published, and optional scheduled_publish_time.

Key facts (verified against Meta docs, 2026):
  - A Page access token minted from a long-lived user token does NOT expire.
    The one-time facebook_setup.py token works forever — invalidated only by
    a Facebook password change or removing the app. No 7-day refresh dance
    like YouTube/TikTok.
  - published=false + unpublished_content_type=DRAFT = only page admins can
    see the video — our "private test" equivalent.
  - scheduled_publish_time (unix UTC) drops the video in the page's Scheduled
    queue; it goes public automatically at that time.
  - Needs the app permissions pages_show_list, pages_read_engagement,
    pages_manage_posts (see FACEBOOK_SETUP.md).
"""

import datetime
import json
import logging
import os
import sys
import time

import requests

logger = logging.getLogger("facebook_poster")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

API_VERSION = "25.0"
GRAPH = f"https://graph.facebook.com/v{API_VERSION}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/v{API_VERSION}"

HTTP_TIMEOUT = 60
UPLOAD_TIMEOUT = 900  # a ~300 MB file over a slow runner link can take a while

# Transient failures worth retrying (Graph API HTTP status codes)
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class FacebookError(RuntimeError):
    """Raised for deterministic Facebook API failures (carries HTTP status)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _secrets():
    app_id = os.getenv("FB_APP_ID")
    page_id = os.getenv("FB_PAGE_ID")
    page_token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    missing = [
        name
        for name, value in {
            "FB_APP_ID": app_id,
            "FB_PAGE_ID": page_id,
            "FB_PAGE_ACCESS_TOKEN": page_token,
        }.items()
        if not value
    ]
    if missing:
        raise FacebookError(
            "Missing Facebook secrets in environment: "
            + ", ".join(missing)
            + ". Add them as GitHub Actions secrets (see FACEBOOK_SETUP.md)."
        )
    return app_id, page_id, page_token


def _check(resp, what):
    """Parse a Graph API response and raise FacebookError on a non-ok payload."""
    try:
        payload = resp.json()
    except ValueError:
        raise FacebookError(
            f"Facebook {what} returned non-JSON (HTTP {resp.status_code})",
            status=resp.status_code,
        )
    err = payload.get("error")
    if err:
        code = err.get("code")
        message = err.get("message") or code
        if code == 190:  # Invalid/expired OAuth access token
            raise FacebookError(
                "Facebook access token invalid or expired — re-run "
                "facebook_setup.py and update the FB_PAGE_ACCESS_TOKEN secret.",
                status=resp.status_code,
            )
        if code == 200:  # Permission error
            raise FacebookError(
                f"Facebook permission error ({what}): {message} "
                "(check the app has pages_show_list, pages_read_engagement, "
                "pages_manage_posts for the page).",
                status=resp.status_code,
            )
        raise FacebookError(
            f"Facebook {what} failed ({code}): {message}",
            status=resp.status_code,
        )
    return payload


def _start_upload_session(app_id, token, video_path):
    """Create a Resumable Upload session -> 'upload:<session_id>'."""
    size = os.path.getsize(video_path)
    resp = requests.post(
        f"{GRAPH}/{app_id}/uploads",
        params={
            "file_name": os.path.basename(video_path),
            "file_length": size,
            "file_type": "video/mp4",
            "access_token": token,
        },
        timeout=HTTP_TIMEOUT,
    )
    payload = _check(resp, "start upload session")
    return payload["id"]


def _upload_file(session_id, token, video_path):
    """Upload the mp4 bytes to the session (single chunk) -> file handle."""
    size = os.path.getsize(video_path)
    headers = {
        "Authorization": f"OAuth {token}",
        "file_offset": "0",
        "Content-Type": "video/mp4",
        "Content-Length": str(size),
    }
    with open(video_path, "rb") as fh:
        resp = requests.post(
            f"{GRAPH}/{session_id}",
            headers=headers,
            data=fh,
            timeout=UPLOAD_TIMEOUT,
        )
    payload = _check(resp, "upload file")
    return payload["h"]


def _to_unix(scheduled):
    """Accept an ISO 8601 UTC string (or already-unix number) -> unix int."""
    if not scheduled:
        return None
    if isinstance(scheduled, (int, float)):
        return int(scheduled)
    dt = datetime.datetime.fromisoformat(str(scheduled).replace("Z", "+00:00"))
    return int(dt.timestamp())


def _publish(page_id, token, handle, title, description, published, scheduled_unix):
    data = {
        "access_token": token,
        "title": title[:255],            # FB caps video titles at 255 chars
        "description": description[:5000],
        "fbuploader_video_file_chunk": handle,
        "published": "true" if published else "false",
    }
    if not published:
        # DRAFT = only people who can manage the page see the video (the
        # Facebook equivalent of YouTube's "private").
        data["unpublished_content_type"] = "DRAFT"
    if scheduled_unix:
        # Video lands in the page's Scheduled queue until this unix UTC time.
        data["scheduled_publish_time"] = str(scheduled_unix)
    resp = requests.post(
        f"{GRAPH_VIDEO}/{page_id}/videos",
        data=data,
        timeout=UPLOAD_TIMEOUT,
    )
    payload = _check(resp, "publish video")
    return payload["id"]


def build_description(metadata, max_len=5000):
    """Build a Facebook video description from the story metadata JSON.

    The title is a separate field on Facebook, so the description is the
    hashtag line only (FB shows the title as its own line above it).
    """
    subreddit = (metadata.get("subreddit") or "").strip()
    tags = ["#redditstories", "#storytime", "#fyp"]
    if subreddit:
        tags.append("#" + subreddit.replace(" ", "").replace("#", ""))
    return " ".join(tags)[:max_len]


def publish_to_facebook(
    video_path,
    title=None,
    description=None,
    published=True,
    scheduled_publish_time=None,
    max_attempts=3,
):
    """Upload + publish one video to a Facebook Page.

    published=True            -> visible on the page now (or at the scheduled
                                 time if scheduled_publish_time is set).
    published=False           -> DRAFT, only page admins can see it (test mode).
    scheduled_publish_time    -> ISO 8601 UTC string or unix int; the video
                                 goes public automatically at that moment.

    Returns a dict with the post id + url.
    """
    app_id, page_id, token = _secrets()

    title = title or os.path.basename(video_path).replace(".mp4", "")
    description = description or "Reddit story narrated with premium voice."
    scheduled_unix = _to_unix(scheduled_publish_time)

    if scheduled_unix:
        logger.info(
            "Publishing %s as '%s' (scheduled public at %s)",
            os.path.basename(video_path), title, scheduled_publish_time,
        )
    elif published:
        logger.info("Publishing %s as '%s' (public now)", os.path.basename(video_path), title)
    else:
        logger.info("Publishing %s as '%s' (DRAFT — admin-only)", os.path.basename(video_path), title)

    attempt = 0
    last_error = None
    while attempt < max_attempts:
        attempt += 1
        try:
            logger.info(
                "Uploading %s (%.1f MB) via Resumable Upload...",
                os.path.basename(video_path),
                os.path.getsize(video_path) / (1024 * 1024),
            )
            session_id = _start_upload_session(app_id, token, video_path)
            handle = _upload_file(session_id, token, video_path)
            post_id = _publish(
                page_id, token, handle, title, description, published, scheduled_unix
            )
            result = {
                "id": post_id,
                "url": f"https://www.facebook.com/{page_id}/videos/{post_id}",
                "published": published,
            }
            if scheduled_unix:
                result["scheduled_publish_time"] = scheduled_publish_time
            logger.info("✅ Facebook publish complete: %s", result)
            return result
        except FacebookError as exc:
            last_error = exc
            retryable = exc.status in RETRYABLE_HTTP
            if not retryable or attempt >= max_attempts:
                raise
            logger.warning("Attempt %s/%s failed: %s", attempt, max_attempts, exc)
            time.sleep(10 * attempt)
    raise last_error or FacebookError("Facebook publish failed")


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import glob

    videos = sorted(glob.glob("output/*_captioned_*.mp4")) or sorted(glob.glob("output/*.mp4"))
    if not videos:
        print("No captioned videos found in output/")
        sys.exit(1)
    video = videos[-1]
    meta_path = video[:-4] + "_metadata.json"
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    print(
        publish_to_facebook(
            video,
            title=meta.get("title") or os.path.basename(video)[:-4],
            description=build_description(meta),
            published=False,  # safe default: draft so you can review it
        )
    )
