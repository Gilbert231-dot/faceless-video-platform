"""
check_drive.py — verify the background-footage Drive folder is reachable
=========================================================================
Confirms the GDRIVE_FOLDER_ID + GDRIVE_API_KEY used by drive_clip_manager.py
actually list the folder with the 4K background videos in it, before you
spend a GitHub Actions run finding out the hard way.

Run it on your laptop:
    python check_drive.py

It reads GDRIVE_API_KEY and GDRIVE_FOLDER_ID from the environment or
.env file, or prompts for them. You can also paste the full folder link
(https://drive.google.com/drive/folders/<ID>) as the folder value.

Exit codes: 0 = PASS (folder lists, files found)
            2 = FAIL (it prints the exact cause: bad key, API not enabled,
                      wrong folder ID, or folder not shared "Anyone with
                      the link")
"""

import os
import re
import sys
import urllib.parse

try:
    import requests
except ImportError:
    print("requests is not installed — run:  pip install requests")
    sys.exit(2)


def _load_env(path=".env"):
    """Minimal .env loader (no python-dotenv dependency)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _extract_folder_id(value: str) -> str:
    """Accept either a bare folder ID or a drive.google.com/drive/folders/<ID> link."""
    m = re.search(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_\-]+)", value)
    if m:
        return m.group(1)
    return value.strip()


def main():
    _load_env()

    api_key = os.environ.get("GDRIVE_API_KEY", "").strip()
    folder = os.environ.get("GDRIVE_FOLDER_ID", "").strip()

    if not api_key:
        api_key = input("GDRIVE_API_KEY (from console.cloud.google.com → APIs & Services → Credentials): ").strip()
    if not folder:
        folder = input("GDRIVE_FOLDER_ID (or the full folder link): ").strip()

    folder_id = _extract_folder_id(folder)
    masked_key = api_key[:6] + "..." if api_key else "(empty)"
    print(f"GDRIVE_API_KEY (starts with AIza...): {masked_key}")
    print(f"GDRIVE_FOLDER_ID: {folder_id}")

    if not api_key or not folder_id:
        print("\n❌ FAILED: both the API key and the folder ID are required.")
        sys.exit(2)

    try:
        r = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, size)",
                "pageSize": 200,
                "key": api_key,
            },
            timeout=30,
        )
        data = r.json()
    except requests.exceptions.RequestException as e:
        print(f"\n❌ FAILED: could not reach the Drive API: {e}")
        print("   Check your internet connection and try again.")
        sys.exit(2)

    if r.status_code == 403:
        msg = data.get("error", {}).get("message", "")
        if "API key" in msg or "apiKey" in msg:
            print("\n❌ FAILED: the API key was rejected.")
            print("   Re-copy it from console.cloud.google.com → APIs & Services → Credentials,")
            print("   and make sure it's NOT restricted to a website/app that excludes Drive.")
        elif "Drive API" in msg:
            print("\n❌ FAILED: the Google Drive API is not enabled for this project.")
            print("   console.cloud.google.com → APIs & Services → Library →")
            print("   'Google Drive API' → Enable.")
        else:
            print(f"\n❌ FAILED: permission error ({r.status_code}): {msg[:300]}")
        sys.exit(2)
    elif r.status_code == 404:
        print("\n❌ FAILED: Drive API error 404 — folder not found.")
        print("   Likely causes, in order:")
        print("     1. Wrong folder ID — copy it from the URL:")
        print("        drive.google.com/drive/folders/<THIS-IS-THE-ID>")
        print("     2. The folder is inside another folder you linked to.")
        sys.exit(2)
    elif r.status_code != 200:
        print(f"\n❌ FAILED: Drive API error {r.status_code}: {data.get('error', {}).get('message', '')[:300]}")
        sys.exit(2)

    files = data.get("files", [])
    videos = [f for f in files if f.get("mimeType") == "video/mp4" or (f.get("name") or "").lower().endswith(".mp4")]

    print(f"\n✅ PASS — folder lists successfully with {len(files)} file(s), "
          f"{len(videos)} video(s).")
    if files:
        for f in files:
            size = f.get("size")
            size_mb = int(size) / 1e6 if size else 0.0
            kind = "🎬 video" if f in videos else "📄 other"
            print(f"   {kind:>8}  {f['name']}  ({size_mb:.0f} MB)")
    if not videos:
        print("\n⚠️  The folder has NO .mp4 files yet — upload the 4K background")
        print("    videos, then re-run this check (the key + folder are proven good).")
    else:
        print(f"\n✅ Ready: {len(videos)} background video(s) will be picked up automatically.")
    sys.exit(0)


if __name__ == "__main__":
    main()
