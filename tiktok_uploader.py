"""
TikTok Content Posting API uploader (official API — no browser automation).

Flow per video:
  1. Refresh the user access token from TIKTOK_REFRESH_TOKEN.
     (Access tokens last 24 h; refresh tokens last 365 days, so every run
     refreshes first. The refresh token may rotate — see
     save_refresh_token_via_gh().)
  2. Query creator info -> privacy_level_options, max duration.
  3. POST /video/init/  ->  publish_id + upload_url
  4. PUT the mp4 to upload_url (single streaming PUT with Content-Range).
  5. Poll /status/fetch/ until PUBLISH_COMPLETE or FAILED.

Important notes (verified against TikTok docs, 2026):
  - Until your app passes TikTok's audit, ALL API posts are restricted to
    private viewing mode (error: unaudited_client_can_only_post_to_private_accounts).
    That suits our review-first flow: the workflow posts with privacy
    SELF_ONLY by default. After the audit passes you may set
    TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE in the workflow.
  - The video.publish scope must be APPROVED (app review) before posting works.
  - is_aigc=True is always sent so TikTok labels the video as AI-generated
    content (required disclosure for this kind of faceless content).
"""

import json
import logging
import os
import subprocess
import sys
import time

import requests

logger = logging.getLogger("tiktok_uploader")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
VIDEO_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

HTTP_TIMEOUT = 60
UPLOAD_TIMEOUT = 900  # a ~300 MB file over a slow runner link can take a while
POLL_MAX_SECONDS = 600
POLL_INTERVAL = 5

DEFAULT_REPO = "Gilbert231-dot/faceless-video-platform"

# Transient-ish failures worth retrying
RETRYABLE_HTTP = {500, 502, 503, 504, 429}
RETRYABLE_FAIL_REASONS = {"internal", "video_pull_failed", "file_format_check_failed"}


class TikTokError(RuntimeError):
    """Raised for deterministic, non-retryable TikTok API failures."""


def _secrets():
    client_key = os.getenv("TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET")
    refresh_token = os.getenv("TIKTOK_REFRESH_TOKEN")
    missing = [
        name
        for name, value in {
            "TIKTOK_CLIENT_KEY": client_key,
            "TIKTOK_CLIENT_SECRET": client_secret,
            "TIKTOK_REFRESH_TOKEN": refresh_token,
        }.items()
        if not value
    ]
    if missing:
        raise TikTokError(
            "Missing TikTok secrets in environment: "
            + ", ".join(missing)
            + ". Add them as GitHub Actions secrets (see TIKTOK_SETUP.md)."
        )
    return client_key, client_secret, refresh_token


def refresh_access_token(client_key, client_secret, refresh_token):
    """Exchange a refresh token for a fresh access token (and possibly a NEW
    refresh token — TikTok rotates it; the caller decides how to persist)."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=HTTP_TIMEOUT,
    )
    payload = resp.json()
    if resp.status_code != 200 or payload.get("error"):
        raise TikTokError(
            f"TikTok token refresh failed (HTTP {resp.status_code}): {payload}"
        )
    return payload  # access_token, refresh_token (may rotate), open_id, expires_in


def query_creator_info(access_token):
    resp = requests.post(
        CREATOR_INFO_URL,
        headers=_bearer(access_token),
        json={},
        timeout=HTTP_TIMEOUT,
    )
    payload = _check(resp, "creator info query")
    return payload["data"]


def _bearer(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def _check(resp, what):
    """Parse a TikTok JSON response and raise on a non-ok error code."""
    try:
        payload = resp.json()
    except ValueError:
        raise TikTokError(f"TikTok {what} returned non-JSON (HTTP {resp.status_code})")
    err = payload.get("error") or {}
    code = err.get("code")
    if code not in (None, "", "ok"):
        message = err.get("message") or code
        if code == "unaudited_client_can_only_post_to_private_accounts":
            raise TikTokError(
                "TikTok: your app hasn't passed TikTok's audit yet, so posts are "
                "restricted to private accounts. Use privacy SELF_ONLY until the "
                "audit passes (see TIKTOK_SETUP.md)."
            )
        if code == "scope_not_authorized":
            raise TikTokError(
                "TikTok: the access token does not include video.publish. "
                "Re-run tiktok_setup.py and re-authorize, or wait for scope approval."
            )
        raise TikTokError(f"TikTok {what} failed ({code}): {message}")
    return payload


def probe_duration(video_path):
    """Return the video duration in seconds (via ffprobe), or None."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", video_path,
            ],
            timeout=30,
        )
        return float(out.strip())
    except Exception:
        return None


def _init_publish(access_token, video_path, title, privacy_level):
    size = os.path.getsize(video_path)
    body = {
        "post_info": {
            "title": title[:2200],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            # Required disclosure: this is AI-generated content.
            "is_aigc": True,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,       # single PUT — whole file in one chunk
            "total_chunk_count": 1,
        },
    }
    resp = requests.post(
        VIDEO_INIT_URL,
        headers=_bearer(access_token),
        json=body,
        timeout=HTTP_TIMEOUT,
    )
    payload = _check(resp, "video init")
    return payload["data"]["publish_id"], payload["data"]["upload_url"]


def _upload_file(upload_url, video_path):
    size = os.path.getsize(video_path)
    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(size),
        "Content-Range": f"bytes 0-{size - 1}/{size}",
    }
    with open(video_path, "rb") as fh:
        resp = requests.put(
            upload_url, headers=headers, data=fh, timeout=UPLOAD_TIMEOUT
        )
    if resp.status_code not in (200, 201, 204):
        raise TikTokError(
            f"TikTok upload PUT failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )


def _poll_status(access_token, publish_id):
    deadline = time.time() + POLL_MAX_SECONDS
    last_status = None
    while time.time() < deadline:
        resp = requests.post(
            VIDEO_STATUS_URL,
            headers=_bearer(access_token),
            json={"publish_id": publish_id},
            timeout=HTTP_TIMEOUT,
        )
        payload = _check(resp, "status fetch")
        data = payload.get("data", {})
        status = data.get("status")
        last_status = status
        if status == "PUBLISH_COMPLETE":
            return data
        if status == "FAILED":
            reason = data.get("fail_reason") or "unknown"
            if reason in RETRYABLE_FAIL_REASONS:
                return {"status": "FAILED", "fail_reason": reason, "retryable": True}
            raise TikTokError(f"TikTok publish failed: {reason}")
        time.sleep(POLL_INTERVAL)
    raise TikTokError(
        f"TikTok publish did not complete within {POLL_MAX_SECONDS}s "
        f"(last status: {last_status})"
    )


def save_refresh_token_via_gh(new_refresh_token):
    """Best-effort persistence of a rotated TikTok refresh token.

    Needs a GitHub fine-grained PAT (with "Secrets" repository permission)
    stored as the GH_PAT secret. Without it, the rotation is lost and the
    original refresh token keeps working until its 365-day expiry — the user
    just re-runs tiktok_setup.py before then.
    """
    pat = os.getenv("GH_PAT")
    repo = os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO)
    if not pat:
        logger.warning(
            "TikTok issued a new refresh token, but the GH_PAT secret is not "
            "configured, so the rotation can't be persisted. The current token "
            "stays valid ~365 days — re-run tiktok_setup.py before it expires, "
            "or add GH_PAT for automatic rotation."
        )
        return
    try:
        subprocess.run(
            ["gh", "secret", "set", "TIKTOK_REFRESH_TOKEN", "--repo", repo],
            input=new_refresh_token.encode("utf-8"),
            check=True,
            capture_output=True,
            env={**os.environ, "GH_TOKEN": pat},
        )
        logger.info("✅ Rotated TikTok refresh token saved as a GitHub secret.")
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not rotate TikTok refresh token: %s", exc)


def build_caption(metadata, max_len=2200):
    """Build a TikTok caption from the story metadata JSON."""
    title = (metadata.get("title") or "").strip()
    subreddit = (metadata.get("subreddit") or "").strip()
    tags = ["#redditstories", "#storytime", "#fyp"]
    if subreddit:
        tags.append("#" + subreddit.replace(" ", "").replace("#", ""))
    caption = f"{title}\n\n{' '.join(tags)}" if title else " ".join(tags)
    return caption[:max_len]


def publish_tiktok(
    video_path,
    title,
    privacy_level="SELF_ONLY",
    max_attempts=3,
    on_new_refresh_token=None,
):
    """Upload + publish one video to TikTok. Returns a result dict."""
    client_key, client_secret, refresh_token = _secrets()

    token_payload = refresh_access_token(client_key, client_secret, refresh_token)
    access_token = token_payload["access_token"]
    new_refresh = token_payload.get("refresh_token")
    if new_refresh and new_refresh != refresh_token and on_new_refresh_token:
        on_new_refresh_token(new_refresh)

    info = query_creator_info(access_token)
    options = info.get("privacy_level_options") or []
    if privacy_level not in options:
        logger.warning(
            "privacy_level %s not offered by this account (%s); using SELF_ONLY",
            privacy_level, options,
        )
        privacy_level = "SELF_ONLY" if "SELF_ONLY" in options else (options[0] if options else "SELF_ONLY")

    max_dur = info.get("max_video_post_duration_sec")
    duration = probe_duration(video_path)
    if max_dur and duration and duration > max_dur:
        raise TikTokError(
            f"Video is {duration:.0f}s but TikTok direct posts cap at {max_dur}s."
        )

    attempt = 0
    last_error = None
    while attempt < max_attempts:
        attempt += 1
        try:
            publish_id, upload_url = _init_publish(
                access_token, video_path, title, privacy_level
            )
            logger.info(
                "Uploading %s (%.1f MB) to TikTok as %s...",
                os.path.basename(video_path),
                os.path.getsize(video_path) / (1024 * 1024),
                privacy_level,
            )
            _upload_file(upload_url, video_path)
            data = _poll_status(access_token, publish_id)
            if data.get("retryable"):
                raise TikTokError(
                    f"TikTok publish failed with retryable reason: {data.get('fail_reason')}"
                )
            post_ids = (
                data.get("publicaly_available_post_id")
                or data.get("publicly_available_post_id")
                or []
            )
            post_id = post_ids[0] if post_ids else None
            result = {"status": "PUBLISH_COMPLETE", "privacy_level": privacy_level}
            if post_id:
                result["post_id"] = post_id
                result["url"] = f"https://www.tiktok.com/@{info.get('creator_username', 'video')}/video/{post_id}"
            logger.info("✅ TikTok publish complete: %s", result)
            return result
        except TikTokError as exc:
            last_error = exc
            # Deterministic failures are not worth retrying blindly, but
            # give transient ones (5xx, rate-limit) a couple of shots.
            if "retryable" not in str(exc) and attempt < max_attempts:
                if not any(code in str(exc) for code in ("HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "HTTP 429")):
                    raise
            logger.warning("Attempt %s/%s failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(10 * attempt)
    raise last_error or TikTokError("TikTok publish failed")


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import glob

    videos = sorted(glob.glob("output/*_captioned_*.mp4")) or sorted(glob.glob("output/*.mp4"))
    if not videos:
        print("No captioned videos found in output/")
        sys.exit(1)
    video = videos[-1]
    meta_path = video[:-4] + "_metadata.json"
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    print(publish_tiktok(video, title=build_caption(meta)))
