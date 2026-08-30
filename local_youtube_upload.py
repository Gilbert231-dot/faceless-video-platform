"""
local_youtube_upload.py — Download videos from GitHub Actions artifacts and
upload to YouTube from your LOCAL machine (residential IP).

This solves the problem where YouTube doesn't push videos uploaded from
GitHub Actions (data center IP) the same way it pushes videos uploaded
from a residential IP.

Usage:
    python local_youtube_upload.py              # Check for new videos and upload
    python local_youtube_upload.py --dry-run    # Just list what would be uploaded
    python local_youtube_upload.py --force      # Re-upload even if already uploaded

Requirements:
    pip install requests google-api-python-client google-auth
"""

import os
import sys
import json
import time
import argparse
import zipfile
import io
from pathlib import Path
from datetime import datetime, timezone

# GitHub API
GITHUB_API = "https://api.github.com"

# Local directories
DOWNLOAD_DIR = Path("local_downloads")
UPLOADED_FILE = Path("local_uploaded_ids.json")
CONFIG_FILE = Path("local_config.json")


def load_config():
    """Load local configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    """Save local configuration."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_uploaded_ids():
    """Load list of already-uploaded artifact IDs."""
    if UPLOADED_FILE.exists():
        with open(UPLOADED_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"uploaded_artifacts": [], "uploaded_videos": []}


def save_uploaded_ids(data):
    """Save uploaded IDs."""
    with open(UPLOADED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_github_headers(config):
    """Get GitHub API headers with authentication."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = config.get("github_token") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def list_workflow_runs(config, limit=5):
    """List recent completed workflow runs."""
    repo = config.get("github_repo")
    if not repo:
        print("❌ github_repo not set in local_config.json")
        print("   Set it with: python local_youtube_upload.py --setup")
        return []

    headers = get_github_headers(config)
    url = f"{GITHUB_API}/repos/{repo}/actions/runs"
    params = {"status": "completed", "per_page": limit, "branch": "main"}

    print(f"📡 Checking GitHub for recent workflow runs...")
    try:
        import requests
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        runs = data.get("workflow_runs", [])
        print(f"   Found {len(runs)} completed runs")
        return runs
    except Exception as e:
        print(f"❌ Failed to list workflow runs: {e}")
        return []


def list_artifacts(config, run_id):
    """List artifacts for a specific workflow run."""
    repo = config.get("github_repo")
    headers = get_github_headers(config)
    url = f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}/artifacts"

    try:
        import requests
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("artifacts", [])
    except Exception as e:
        print(f"   ❌ Failed to list artifacts for run {run_id}: {e}")
        return []


def download_artifact(config, artifact_id, artifact_name):
    """Download and extract an artifact."""
    repo = config.get("github_repo")
    headers = get_github_headers(config)
    url = f"{GITHUB_API}/repos/{repo}/actions/artifacts/{artifact_id}/zip"

    print(f"   ⬇️  Downloading artifact: {artifact_name}...")
    try:
        import requests
        resp = requests.get(url, headers=headers, timeout=300, stream=True)
        resp.raise_for_status()

        # Extract to download directory
        artifact_dir = DOWNLOAD_DIR / artifact_name
        artifact_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(artifact_dir)

        print(f"   ✅ Extracted to: {artifact_dir}")
        return artifact_dir
    except Exception as e:
        print(f"   ❌ Failed to download artifact: {e}")
        return None


def find_videos(artifact_dir):
    """Find video files and their metadata in extracted artifact."""
    videos = []
    artifact_path = Path(artifact_dir)

    # Find all mp4 files
    for mp4 in artifact_path.rglob("*.mp4"):
        # Look for matching metadata file
        metadata_file = mp4.with_name(mp4.stem.replace("_captioned_", "_") + "_metadata.json")
        if not metadata_file.exists():
            # Try alternative naming
            metadata_file = mp4.parent / f"{mp4.stem}_metadata.json"

        # Try to load metadata
        metadata = {}
        if metadata_file.exists():
            try:
                with open(metadata_file, encoding="utf-8") as f:
                    metadata = json.load(f)
            except:
                pass

        # Also look for thumbnail
        thumbnail = None
        for ext in [".jpg", ".jpeg", ".png"]:
            thumb_file = mp4.parent / f"{mp4.stem}_thumb{ext}"
            if thumb_file.exists():
                thumbnail = str(thumb_file)
                break

        videos.append({
            "video_path": str(mp4),
            "metadata": metadata,
            "thumbnail": thumbnail,
        })

    return videos


def upload_to_youtube(video_path, metadata=None, thumbnail_path=None, config=None):
    """Upload a video to YouTube using local OAuth credentials."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials

    # Get credentials from config or environment
    config = config or {}
    client_id = config.get("youtube_client_id") or os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = config.get("youtube_client_secret") or os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = config.get("youtube_refresh_token") or os.getenv("YOUTUBE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        print("❌ YouTube credentials not configured!")
        print("   Run: python local_youtube_upload.py --setup-youtube")
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
    )

    youtube = build("youtube", "v3", credentials=creds)

    # Prepare metadata
    meta = metadata or {}
    title = meta.get("title", Path(video_path).stem)
    description = meta.get("description", "Reddit story narrated with premium voice. Subscribe for more!")
    tags = meta.get("tags", ["RedditStories", "Storytime", "Reddit", "TrueStory"])

    # Build upload body
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:15],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
            "embeddable": True,
            "publicStatsViewable": True,
        },
    }

    print(f"   📤 Uploading: {title[:50]}...")
    media = MediaFileUpload(video_path, chunksize=1024 * 1024 * 8, resumable=True)

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=False,
        )
        response = request.execute()
        video_id = response["id"]
        print(f"   ✅ Uploaded! Video ID: {video_id}")
        print(f"   🔗 https://youtu.be/{video_id}")

        # Set thumbnail if provided
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
                ).execute()
                print(f"   🖼️  Thumbnail set")
            except Exception as e:
                print(f"   ⚠️  Thumbnail failed (non-critical): {e}")

        return video_id
    except Exception as e:
        print(f"   ❌ Upload failed: {e}")
        return None


def process_videos(videos, config, dry_run=False, force=False):
    """Process and upload videos."""
    uploaded_data = load_uploaded_ids()
    schedule_times = config.get("schedule_times", ["23:00", "03:00", "15:00"])
    privacy = config.get("youtube_privacy", "public")

    uploaded_count = 0

    for i, video in enumerate(videos):
        video_path = video["video_path"]
        metadata = video["metadata"]
        thumbnail = video["thumbnail"]

        # Check if already uploaded
        video_key = os.path.basename(video_path)
        if not force and video_key in uploaded_data.get("uploaded_videos", []):
            print(f"   ⏭️  Skipping (already uploaded): {video_key}")
            continue

        # Schedule time
        schedule_time = None
        if privacy == "public" and schedule_times:
            # Simple scheduling: use the times in order
            schedule_time = schedule_times[i % len(schedule_times)]

        if dry_run:
            print(f"   📋 Would upload: {video_path}")
            print(f"      Title: {metadata.get('title', 'Unknown')}")
            if schedule_time:
                print(f"      Schedule: {schedule_time}")
            continue

        # Upload
        video_id = upload_to_youtube(
            video_path,
            metadata=metadata,
            thumbnail_path=thumbnail,
            config=config,
        )

        if video_id:
            uploaded_data["uploaded_videos"].append(video_key)
            uploaded_data["uploaded_videos"] = uploaded_data["uploaded_videos"][-100:]  # Keep last 100
            save_uploaded_ids(uploaded_data)
            uploaded_count += 1

            # Record in video history
            try:
                from video_history import record_video
                record_video(
                    video_path,
                    metadata=metadata,
                    video_id=video_id,
                    status="posted",
                    platform="youtube",
                )
            except Exception as e:
                print(f"   ⚠️  Could not update video_history.json: {e}")

        # Small delay between uploads
        if i < len(videos) - 1:
            print("   ⏳ Waiting 5 seconds before next upload...")
            time.sleep(5)

    return uploaded_count


def setup_wizard():
    """Interactive setup wizard."""
    print("=" * 60)
    print("🎬 Local YouTube Upload Setup Wizard")
    print("=" * 60)
    print()

    config = load_config()

    # GitHub repo
    print("📋 Step 1: GitHub Repository")
    print("   Your repo URL (format: owner/repo)")
    current = config.get("github_repo", "")
    repo = input(f"   GitHub repo [{current}]: ").strip()
    if repo:
        config["github_repo"] = repo
    elif not current:
        print("   ❌ GitHub repo is required!")
        return

    # GitHub token (optional but recommended)
    print()
    print("📋 Step 2: GitHub Token (optional)")
    print("   A personal access token lets you access private repos")
    print("   and avoids rate limits. Create one at:")
    print("   https://github.com/settings/tokens")
    token = input(f"   GitHub token [{config.get('github_token', '')[:10]}...]: ").strip()
    if token:
        config["github_token"] = token

    # YouTube credentials
    print()
    print("📋 Step 3: YouTube OAuth Credentials")
    print("   These are the same credentials you use for GitHub Actions")
    config["youtube_client_id"] = input(
        f"   Client ID [{config.get('youtube_client_id', '')[:20]}...]: "
    ).strip() or config.get("youtube_client_id", "")
    config["youtube_client_secret"] = input(
        f"   Client Secret [{config.get('youtube_client_secret', '')[:10]}...]: "
    ).strip() or config.get("youtube_client_secret", "")
    config["youtube_refresh_token"] = input(
        f"   Refresh Token [{config.get('youtube_refresh_token', '')[:20]}...]: "
    ).strip() or config.get("youtube_refresh_token", "")

    # Schedule settings
    print()
    print("📋 Step 4: Upload Settings")
    config["youtube_privacy"] = input(
        f"   Privacy (public/private/unlisted) [{config.get('youtube_privacy', 'public')}]: "
    ).strip() or config.get("youtube_privacy", "public")

    times = input(
        f"   Schedule times (comma-separated HH:MM) [{config.get('schedule_times', ['23:00', '03:00', '15:00'])}]: "
    ).strip()
    if times:
        config["schedule_times"] = [t.strip() for t in times.split(",")]

    # Save
    save_config(config)
    print()
    print("=" * 60)
    print("✅ Setup complete! Configuration saved to local_config.json")
    print()
    print("Next steps:")
    print("1. Run: python local_youtube_upload.py --dry-run")
    print("2. Run: python local_youtube_upload.py")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Download videos from GitHub and upload to YouTube from your laptop"
    )
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--dry-run", action="store_true", help="Just list what would be uploaded")
    parser.add_argument("--force", action="store_true", help="Re-upload even if already uploaded")
    parser.add_argument("--limit", type=int, default=3, help="Number of recent runs to check")
    args = parser.parse_args()

    if args.setup:
        setup_wizard()
        return

    print("=" * 60)
    print("🎬 Local YouTube Upload")
    print("=" * 60)
    print()

    # Load config
    config = load_config()
    if not config.get("github_repo"):
        print("❌ Not configured yet! Run: python local_youtube_upload.py --setup")
        return

    # Create download directory
    DOWNLOAD_DIR.mkdir(exist_ok=True)

    # List recent runs
    runs = list_workflow_runs(config, limit=args.limit)
    if not runs:
        print("   No completed runs found")
        return

    # Process each run
    uploaded_data = load_uploaded_ids()
    total_uploaded = 0

    for run in runs:
        run_id = run["id"]
        run_name = run.get("name", "unknown")
        run_created = run.get("created_at", "")

        print(f"\n📋 Run: {run_name} (ID: {run_id})")
        print(f"   Created: {run_created}")

        # Check if already processed
        if str(run_id) in uploaded_data.get("uploaded_artifacts", []):
            print(f"   ⏭️  Already processed, skipping")
            continue

        # List artifacts
        artifacts = list_artifacts(config, run_id)
        video_artifacts = [a for a in artifacts if a["name"].startswith("videos-")]

        if not video_artifacts:
            print(f"   ℹ️  No video artifacts found")
            continue

        for artifact in video_artifacts:
            artifact_id = artifact["id"]
            artifact_name = artifact["name"]

            print(f"\n   📦 Artifact: {artifact_name}")

            # Download
            artifact_dir = download_artifact(config, artifact_id, artifact_name)
            if not artifact_dir:
                continue

            # Find videos
            videos = find_videos(artifact_dir)
            if not videos:
                print(f"   ℹ️  No videos found in artifact")
                continue

            print(f"   🎬 Found {len(videos)} video(s)")

            # Upload
            uploaded = process_videos(videos, config, dry_run=args.dry_run, force=args.force)
            total_uploaded += uploaded

            # Mark as processed
            uploaded_data["uploaded_artifacts"].append(str(run_id))
            uploaded_data["uploaded_artifacts"] = uploaded_data["uploaded_artifacts"][-50:]
            save_uploaded_ids(uploaded_data)

    # Summary
    print()
    print("=" * 60)
    if args.dry_run:
        print("📋 Dry run complete — no videos were uploaded")
    else:
        print(f"✅ Done! Uploaded {total_uploaded} video(s) to YouTube")
    print("=" * 60)


if __name__ == "__main__":
    main()
