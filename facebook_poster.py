"""
facebook_poster.py — post generated videos to a Facebook Page via the official
Graph API Video API (no unofficial bots, nothing that can get the account
banned).

Two posting paths, chosen per video:

  REELS (<= REEL_MAX_SECONDS, ~90s) — posted through the video_reels API so
  they appear in the Facebook Reels discovery feed (regular video posts do
  not). Flow: POST /{PAGE_ID}/video_reels upload_phase=start -> upload_url,
  PUT the mp4 to rupload.facebook.com, then upload_phase=finish with
  video_state DRAFT/SCHEDULED/PUBLISHED. is_ai_generated is officially
  documented here.

  REGULAR VIDEO (longer stories, or when the Reels path fails) — the classic
  flow:
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
import subprocess
import sys
import time

import requests

from config import platform_tags

logger = logging.getLogger("facebook_poster")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

API_VERSION = "25.0"
GRAPH = f"https://graph.facebook.com/v{API_VERSION}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/v{API_VERSION}"

HTTP_TIMEOUT = 60
UPLOAD_TIMEOUT = 900  # a ~300 MB file over a slow runner link can take a while

# The official Resumable Upload flow uploads the whole file in one POST and
# only resumes (from the server-reported byte offset) if that request is
# interrupted — see _upload_file. Subdividing the file into many small chunk
# POSTs makes the server silently stop recording partway, so we never do that.

# Transient failures worth retrying (Graph API HTTP status codes)
RETRYABLE_HTTP = {429, 500, 502, 503, 504}

# Facebook Reels cap at 90 seconds (official spec: 3-90s, 9:16). Videos at or
# under this are posted as Reels so they appear in the Reels discovery feed;
# longer stories fall back to a regular page video post.
REEL_MAX_SECONDS = 90

# Meta-side bug (documented across the developer community): a video handle
# returned by the Resumable Upload API is sometimes REJECTED at the publish
# step with code 6000/subcode 1363019 ("There was a problem uploading your
# video file") even though the upload itself succeeded — the video then can't
# be posted no matter how often you retry. A widely-confirmed workaround is
# to send the same handle under the field name 'fbuploader_video_file_chunk1'
# (with the trailing '1'). 390/1363030 is the same rejection when the file
# arrived truncated, so it gets the same fallback treatment.
HANDLE_REJECT_CODES = {6000, 390}


class FacebookError(RuntimeError):
    """Raised for deterministic Facebook API failures.

    Carries the HTTP status plus the Graph API error code/subcode so
    callers can branch on the exact failure (e.g. the handle-rejection bug).
    """

    def __init__(self, message, status=None, code=None, subcode=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode


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
        subcode = err.get("error_subcode")
        message = err.get("message") or code
        if code == 190:  # Invalid/expired OAuth access token
            raise FacebookError(
                "Facebook access token invalid or expired — re-run "
                "facebook_setup.py and update the FB_PAGE_ACCESS_TOKEN secret.",
                status=resp.status_code, code=code, subcode=subcode,
            )
        if code == 200:  # Permission error
            raise FacebookError(
                f"Facebook permission error ({what}): {message} "
                "(check the app has pages_show_list, pages_read_engagement, "
                "pages_manage_posts for the page).",
                status=resp.status_code, code=code, subcode=subcode,
            )
        raise FacebookError(
            f"Facebook {what} failed ({code}/{subcode}): {message}",
            status=resp.status_code, code=code, subcode=subcode,
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
    """Upload the mp4 to the session per the documented Resumable Upload flow.

    The official protocol (developers.facebook.com/docs/graph-api/guides/upload)
    uploads the WHOLE file in a single POST at file_offset 0 and returns the
    handle 'h'. There is no multi-chunk variant: subdividing the file into
    many small chunk POSTs (as some third-party docs suggest) makes the server
    stop recording after a while — we learned that the hard way (Facebook
    recorded exactly 80 MB of a 306 MB file).

    If that single POST is interrupted, we GET the authoritative byte offset
    and POST only the remaining bytes from there, repeating until the server
    returns the handle. We never publish a truncated file: if no handle ever
    arrives and the recorded offset is short of the real size, we fail loudly.
    """
    size = os.path.getsize(video_path)

    def _post_from(offset):
        with open(video_path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        headers = {
            "Authorization": f"OAuth {token}",
            "file_offset": str(offset),
            "Content-Type": "video/mp4",
        }
        resp = requests.post(
            f"{GRAPH}/{session_id}",
            headers=headers,
            data=data,
            timeout=UPLOAD_TIMEOUT,
        )
        return _check(resp, "upload file")

    # 1) The documented single-shot upload of the entire file.
    payload = _post_from(0)
    if payload.get("h"):
        return payload["h"]

    # 2) The single shot didn't complete (interrupted/truncated mid-flight).
    #    Resume from wherever the server actually recorded bytes.
    for _ in range(10):  # bounded resume loop
        resp = requests.get(
            f"{GRAPH}/{session_id}",
            headers={"Authorization": f"OAuth {token}"},
            timeout=HTTP_TIMEOUT,
        )
        payload = _check(resp, "upload status")
        offset = int(payload.get("file_offset") or 0)
        if offset >= size:
            break
        payload = _post_from(offset)
        if payload.get("h"):
            return payload["h"]

    # 3) Never publish a truncated file — fail loudly instead.
    resp = requests.get(
        f"{GRAPH}/{session_id}",
        headers={"Authorization": f"OAuth {token}"},
        timeout=HTTP_TIMEOUT,
    )
    payload = _check(resp, "upload status")
    recorded = int(payload.get("file_offset") or 0)
    if recorded != size:
        raise FacebookError(
            f"Upload incomplete: Facebook recorded {recorded}/{size} bytes — "
            "refusing to publish a truncated video."
        )
    raise FacebookError("Upload finished but no file handle was returned.")


def _to_unix(scheduled):
    """Accept an ISO 8601 UTC string (or already-unix number) -> unix int."""
    if not scheduled:
        return None
    if isinstance(scheduled, (int, float)):
        return int(scheduled)
    dt = datetime.datetime.fromisoformat(str(scheduled).replace("Z", "+00:00"))
    return int(dt.timestamp())


def _publish(page_id, token, handle, title, description, published, scheduled_unix,
             chunk_field="fbuploader_video_file_chunk"):
    """Publish an already-uploaded file by its handle.

    chunk_field is parameterized because Meta has a known bug where the
    documented field name is rejected for some handles — sending the SAME
    handle under 'fbuploader_video_file_chunk1' is the community-confirmed
    workaround (see HANDLE_REJECT_CODES).
    """
    data = {
        "access_token": token,
        "title": title[:255],            # FB caps video titles at 255 chars
        "description": description[:5000],
        chunk_field: handle,
        "published": "true" if published else "false",
        # Self-disclosure that the video was created with AI (the Facebook
        # equivalent of YouTube's containsSyntheticMedia). The Graph API
        # documents it for Reels/Stories/Instagram; Meta also accepts it here
        # on regular Page videos (verified live — the video then carries the
        # "AI-generated" label). Our whole pipeline is AI-generated, so this
        # is always true.
        "is_ai_generated": "true",
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


def _publish_source(page_id, token, video_path, title, description, published, scheduled_unix):
    """Last-resort publish: the classic non-resumable multipart upload.

    Facebook accepts the raw file via the 'source' form field. It has its own
    size ceiling (our ~100-300 MB outputs are usually fine), so it's a useful
    escape hatch when the resumable-handle path is broken for a given file.
    """
    data = {
        "access_token": token,
        "title": title[:255],
        "description": description[:5000],
        "published": "true" if published else "false",
        "is_ai_generated": "true",
    }
    if not published:
        data["unpublished_content_type"] = "DRAFT"
    if scheduled_unix:
        data["scheduled_publish_time"] = str(scheduled_unix)
    with open(video_path, "rb") as fh:
        resp = requests.post(
            f"{GRAPH_VIDEO}/{page_id}/videos",
            data=data,
            files={"source": (os.path.basename(video_path), fh, "video/mp4")},
            timeout=UPLOAD_TIMEOUT,
        )
    payload = _check(resp, "publish video (source upload)")
    return payload["id"]


def _video_duration(video_path):
    """Return the video length in seconds via ffprobe, or None if unknown.

    Used to decide Reels (<= REEL_MAX_SECONDS) vs regular video post. The
    runner installs ffprobe as a system dependency, so this is reliable in
    GitHub Actions; on machines without it we return None and safely fall
    back to a regular video post.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _publish_reel(page_id, token, video_path, title, description, published,
                  scheduled_unix):
    """Publish the video as a Facebook Reel via the video_reels API.

    Flow (per Meta's Reels Publishing docs):
      1. POST /{page_id}/video_reels upload_phase=start -> video_id + upload_url
      2. PUT the mp4 to the upload_url (rupload.facebook.com), resuming from
         bytes_transfered if interrupted
      3. POST /{page_id}/video_reels upload_phase=finish with video_state
         DRAFT / SCHEDULED / PUBLISHED, title, description and is_ai_generated

    Reels appear in the Reels discovery feed — regular video posts do not.
    """
    size = os.path.getsize(video_path)
    logger.info(
        "Uploading %s (%.1f MB) as a Reel...",
        os.path.basename(video_path), size / (1024 * 1024),
    )

    # 1) Start the upload session.
    resp = requests.post(
        f"{GRAPH}/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": token},
        timeout=HTTP_TIMEOUT,
    )
    payload = _check(resp, "start reel upload")
    video_id = payload["video_id"]
    upload_url = payload["upload_url"]

    # 2) PUT the file. If the server didn't record everything, resume from
    #    the reported bytes_transfered instead of starting over.
    offset = 0
    for _ in range(5):
        if offset >= size:
            break
        with open(video_path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        headers = {
            "Authorization": f"OAuth {token}",
            "file_size": str(size),
            "offset": str(offset),
            "Content-Type": "application/octet-stream",
        }
        resp = requests.post(upload_url, headers=headers, data=data,
                             timeout=UPLOAD_TIMEOUT)
        payload = _check(resp, "upload reel file")
        if payload.get("success"):
            break
        # Partial/interrupted upload — ask where to resume.
        status = requests.get(
            f"{GRAPH}/{video_id}",
            params={"fields": "status", "access_token": token},
            timeout=HTTP_TIMEOUT,
        )
        sp = _check(status, "reel upload status")
        phase = sp.get("status", {}).get("uploading_phase", {})
        new_offset = int(phase.get("bytes_transfered") or 0)
        if new_offset <= offset:
            raise FacebookError(
                f"Reel upload stalled at {new_offset}/{size} bytes."
            )
        offset = new_offset

    # 3) Finish + publish.
    state = "SCHEDULED" if scheduled_unix else ("PUBLISHED" if published else "DRAFT")
    data = {
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": state,
        "title": title[:255],
        "description": description[:5000],
        # Officially documented on the video_reels endpoint (even cleaner than
        # the page-videos route): the video is AI-generated, always.
        "is_ai_generated": "true",
        "access_token": token,
    }
    if scheduled_unix:
        data["scheduled_publish_time"] = str(scheduled_unix)
    resp = requests.post(f"{GRAPH}/{page_id}/video_reels", data=data,
                         timeout=UPLOAD_TIMEOUT)
    payload = _check(resp, "publish reel")
    post_id = payload.get("post_id") or video_id
    result = {
        "id": post_id,
        "url": f"https://www.facebook.com/reel/{video_id}",
        "published": published,
        "reel": True,
    }
    if scheduled_unix:
        result["scheduled_publish_time"] = scheduled_unix
    logger.info("✅ Reel publish complete: %s", result)
    return result


def build_description(metadata, max_len=5000):
    """Build a Facebook video description from the story metadata JSON.

    The title is a separate field on Facebook, so the description is the
    hashtag line only (FB shows the title as its own line above it). Uses the
    curated Facebook tag set (config.PLATFORM_TAGS) — the '#'-prefixed list
    that Facebook recognizes, not the YouTube or TikTok lists.
    """
    subreddit = (metadata.get("subreddit") or "").strip()
    tags = metadata.get("facebook_tags") or platform_tags("facebook", subreddit)
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
            # Reels when the video fits Facebook's 90s Reel limit (discovery
            # in the Reels feed). If the Reel path fails for ANY reason
            # (wrong duration, spec mismatch, transient error), fall back to
            # a regular video post below so the video always lands.
            duration = _video_duration(video_path)
            if duration is not None and duration <= REEL_MAX_SECONDS:
                try:
                    return _publish_reel(
                        page_id, token, video_path, title, description,
                        published, scheduled_unix,
                    )
                except FacebookError as exc:
                    last_error = exc
                    logger.warning(
                        "Reels publish failed (%s) — falling back to a "
                        "regular video post.", exc,
                    )

            logger.info(
                "Uploading %s (%.1f MB) via Resumable Upload (single-shot, resumable)...",
                os.path.basename(video_path),
                os.path.getsize(video_path) / (1024 * 1024),
            )
            session_id = _start_upload_session(app_id, token, video_path)
            handle = _upload_file(session_id, token, video_path)

            # Publish the handle under the documented field name first, then
            # under the community workaround name if Meta rejects it — no
            # re-upload needed (same handle).
            post_id = None
            for field in ("fbuploader_video_file_chunk",
                          "fbuploader_video_file_chunk1"):
                try:
                    post_id = _publish(
                        page_id, token, handle, title, description,
                        published, scheduled_unix, chunk_field=field,
                    )
                    break
                except FacebookError as exc:
                    last_error = exc
                    if exc.code not in HANDLE_REJECT_CODES:
                        raise
                    logger.warning(
                        "Publish with '%s' rejected (%s) — trying the "
                        "workaround field...", field, exc,
                    )

            if post_id is None:
                # Last resort: plain multipart source upload (its own limits,
                # but independent of the resumable-handle bug entirely).
                logger.warning(
                    "Handle publish rejected by both field names — falling "
                    "back to non-resumable multipart source upload..."
                )
                post_id = _publish_source(
                    page_id, token, video_path, title, description,
                    published, scheduled_unix,
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
