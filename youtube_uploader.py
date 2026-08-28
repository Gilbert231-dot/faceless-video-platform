import os
import json
import time
import logging

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

logger = logging.getLogger("youtube_uploader")
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

# Scopes for uploading videos AND reading/updating video metadata.
# youtube.readonly is required by videos().list / playlistItems() — used by
# schedule_public.py to fetch a video's current snippet/status before
# updating it. The refresh tokens were minted with BOTH scopes (see
# youtube_setup.py's consent URL), so refreshing with this superset works;
# without readonly the access token gets youtube.upload only and every
# videos().list call fails with "insufficient authentication scopes".
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Fields that the uploader will wait-and-retry on (transient failures)
RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}


def get_authenticated_service():
    """Build an authenticated YouTube API client from OAuth credentials."""
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if not all([refresh_token, client_id, client_secret]):
        missing = [
            name
            for name, value in {
                "YOUTUBE_REFRESH_TOKEN": refresh_token,
                "YOUTUBE_CLIENT_ID": client_id,
                "YOUTUBE_CLIENT_SECRET": client_secret,
            }.items()
            if not value
        ]
        raise RuntimeError(
            "Missing YouTube OAuth secrets in environment: "
            + ", ".join(missing)
            + ". Add them as GitHub Actions secrets."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def build_upload_body(title, description, tags, privacy_status, publish_at=None):
    """
    Build the videos.insert body.

    NOTE (verified against the YouTube Data API v3 reference):
    - status.madeForKids is read-only; the writable field is
      status.selfDeclaredMadeForKids.
    - status.selfDeclaredContentType / status.contentType DO NOT exist.
      Sending them (e.g. "ai") makes the API reject the request with
      HTTP 400.
    - The real AI/altered-content disclosure field is
      status.containsSyntheticMedia (boolean).
    - Scheduled publishing: when publish_at (ISO 8601 UTC) is given, the
      video is uploaded as PRIVATE and YouTube automatically flips it to
      PUBLIC at that time. The API only accepts publishAt on private/
      unlisted uploads, so privacyStatus is forced to "private" here and
      the public flip happens by itself later.
    """
    status = {
        "privacyStatus": privacy_status,          # public | unlisted | private
        "selfDeclaredMadeForKids": False,         # not made for kids
        "containsSyntheticMedia": True,           # AI-generated/voice content
        "embeddable": True,
        "publicStatsViewable": True,
    }
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    return {
        "snippet": {
            "title": title[:100],          # YouTube caps titles at 100 chars
            "description": description[:5000],  # caps at 5000 bytes
            "tags": tags[:15],             # keep the tag list sane
            "categoryId": "22",            # People & Blogs
        },
        "status": status,
    }


def upload_to_youtube(
    video_path,
    metadata_path=None,
    privacy_status="public",
    publish_at=None,
    max_attempts=4,
):
    """Upload a video to YouTube with retries on transient errors.

    If publish_at (ISO 8601 UTC) is set, the video uploads as private and
    YouTube makes it public automatically at that time.

    Returns the YouTube video ID.
    """
    youtube = get_authenticated_service()

    # --- Default metadata ---
    title = os.path.basename(video_path).replace(".mp4", "")
    description = "Reddit story narrated with premium voice. Subscribe for more!"
    tags = []

    # --- Load metadata from JSON if available ---
    thumbnail_path = None
    if metadata_path and os.path.exists(metadata_path):
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        title = metadata.get("title", title)
        description = metadata.get("description", description)
        tags = metadata.get("tags", tags)
        thumb = metadata.get("thumbnail")
        if thumb:
            if os.path.exists(thumb):
                thumbnail_path = thumb
                logger.info("🖼️ Thumbnail found: %s (%d bytes)", thumb, os.path.getsize(thumb))
            else:
                logger.warning("⚠️ Thumbnail path in metadata but file missing: %s", thumb)
        else:
            logger.warning("⚠️ No thumbnail in metadata (thumbnail=%s)", thumb)

    body = build_upload_body(title, description, tags, privacy_status, publish_at)
    if publish_at:
        logger.info(
            "Uploading %s as '%s' (scheduled public at %s)",
            video_path, title, publish_at,
        )
    else:
        logger.info("Uploading %s as '%s' (privacy=%s)", video_path, title, privacy_status)

    media = MediaFileUpload(video_path, chunksize=1024 * 1024 * 8, resumable=True)

    for attempt in range(1, max_attempts + 1):
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=False,
        )
        try:
            response = request.execute()
            video_id = response["id"]
            logger.info(
                "✅ Video uploaded! Video ID: %s -> https://youtu.be/%s",
                video_id,
                video_id,
            )
            # Set the custom thumbnail (the reddit card) so the intro and the
            # thumbnail match. Requires a phone-verified channel + the
            # youtube.upload scope (already in SCOPES). Fails SOFTLY: an
            # unverified channel returns 403, and the video is already live
            # by then — the upload itself must never fail because of it.
            if thumbnail_path:
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
                    ).execute()
                    logger.info("🖼️ Custom thumbnail set: %s", thumbnail_path)
                except HttpError as te:
                    status = te.resp.status
                    if status == 403:
                        logger.warning(
                            "⚠️ Custom thumbnail skipped: the channel isn't verified "
                            "for custom thumbnails (HTTP 403). Verify your channel in "
                            "YouTube Studio to enable them — the video itself is fine."
                        )
                    else:
                        logger.warning(
                            "⚠️ Custom thumbnail failed (HTTP %s), continuing without it: %s",
                            status, te.reason,
                        )
                except Exception as te:
                    logger.warning("⚠️ Custom thumbnail failed, continuing: %s", te)
            return video_id
        except HttpError as e:
            status = e.resp.status
            if status in RETRYABLE_STATUS_CODES and attempt < max_attempts:
                wait = 2 ** attempt * 5  # 10s, 20s, 40s backoff
                logger.warning(
                    "HTTP %s on attempt %s/%s, retrying in %ss: %s",
                    status, attempt, max_attempts, wait, e.reason,
                )
                # For resumable uploads, rebuild the media object before retrying
                media = MediaFileUpload(
                    video_path, chunksize=1024 * 1024 * 8, resumable=True
                )
                time.sleep(wait)
                continue
            if status == 400:
                raise RuntimeError(
                    "YouTube rejected the video metadata (HTTP 400). "
                    "This usually means an invalid field was sent. "
                    f"Details: {e.reason}"
                ) from e
            raise RuntimeError(
                f"YouTube upload failed (HTTP {status}): {e.reason}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"YouTube upload failed unexpectedly: {e}") from e
