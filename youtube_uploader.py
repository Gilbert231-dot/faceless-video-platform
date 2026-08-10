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

# Scope for uploading videos
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

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
    if metadata_path and os.path.exists(metadata_path):
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        title = metadata.get("title", title)
        description = metadata.get("description", description)
        tags = metadata.get("tags", tags)

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
