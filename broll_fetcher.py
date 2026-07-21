import os
import json
import tempfile
import requests
import gdown
import yt_dlp
from config import PEXELS_API_KEY, PROGRESS_FILE

# --- Progress file for sequential video usage ---

def load_progress():
    """Load the B-roll progress tracker. Create it if missing."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    # File doesn't exist, create default
    default = {"current_index": 0, "used_files": []}
    save_progress(default)
    return default

def save_progress(progress):
    """Save the B-roll progress tracker."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)

def get_next_video_file(file_ids):
    """
    Returns the next video file ID in sequence.
    Handles:
    - New files added later: they are appended to the end of the list.
    - Deleted files: automatically skipped if missing.
    - Looping: when all files are used, starts from the beginning.
    """
    progress = load_progress()
    current_index = progress.get("current_index", 0)
    used_files = progress.get("used_files", [])

    # Clean up used_files list: remove any files that are no longer in file_ids
    # This handles deletion of videos from the cloud folder.
    valid_used_files = [f for f in used_files if f in file_ids]
    if len(valid_used_files) != len(used_files):
        used_files = valid_used_files
        progress["used_files"] = used_files
        save_progress(progress)

    # If all files have been used, reset the list and start from the beginning.
    if len(used_files) >= len(file_ids):
        used_files = []
        current_index = 0
        progress["used_files"] = used_files
        progress["current_index"] = current_index
        save_progress(progress)

    # Find the next file that hasn't been used yet.
    # Start from the current index and move forward.
    for i in range(len(file_ids)):
        idx = (current_index + i) % len(file_ids)
        candidate = file_ids[idx]
        if candidate not in used_files:
            # Mark as used and update progress
            used_files.append(candidate)
            progress["current_index"] = (idx + 1) % len(file_ids)
            progress["used_files"] = used_files
            save_progress(progress)
            return candidate

    # Fallback: if all are used (shouldn't happen due to reset logic), reset and return first.
    used_files = []
    progress["used_files"] = used_files
    progress["current_index"] = 0
    save_progress(progress)
    return file_ids[0]

# ================================================
# GOOGLE DRIVE (Primary Source)
# ================================================
def download_from_gdrive(file_id, output_path):
    """Download a file from Google Drive using file ID."""
    url = f"https://drive.google.com/uc?id={file_id}"
    gdown.download(url, output_path, quiet=False)
    return output_path

def fetch_cloud_gameplay(topic=None):
    """Fetch the next gameplay video from Google Drive (sequentially)."""
    
    # ---- REPLACE THESE WITH YOUR ACTUAL FILE IDs ----
    file_ids = [
        "1183ENgEB0H55gwVYDFzqJ4bFrOwXo5OM",  # Replaced with your actual file IDs
        "1QjdFKRf1PmmQncLGrI59hD7yKngqui_r",
        "1csHaO2EUANXLexMSxG-ltI77dvCIlH2P",
        "1XSwwDED61z2MbM7QSEGhH0W9I7qoSd-Z",
        "1JIy54c7ljm4njW7lqaHzlpOhIMVplUzs",
        # ... add more
    ]
    # -------------------------------------------------

    if not file_ids:
        print("   ⚠️ No file IDs configured. Falling back to Pexels.")
        return None

    # Get the next file ID in sequence
    selected_id = get_next_video_file(file_ids)
    print(f"🎮 Fetching gameplay from Google Drive (sequential): {selected_id}")

    # Download to temp file
    temp_path = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4').name
    try:
        download_from_gdrive(selected_id, temp_path)
        print(f"   ✅ Downloaded: {temp_path}")
        return [temp_path]
    except Exception as e:
        print(f"   ⚠️ Google Drive download failed: {e}")
        return None

# ================================================
# YOUTUBE (Fallback Source)
# ================================================
def download_youtube_gameplay(query="Minecraft no commentary", max_duration=300):
    """Download a gameplay video from YouTube based on search query."""
    ydl_opts = {
        'format': 'best[height<=1080]',  # 1080p for quality
        'max_duration': max_duration,
        'quiet': False,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': False,
        'outtmpl': '/tmp/gameplay_%(id)s.%(ext)s',
        'restrictfilenames': True,
        'cookiefile': 'www.youtube.com_cookies.txt',  # Optional
    }
    
    search_query = f"ytsearch1:{query} gameplay no commentary"
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(search_query, download=True)
            if info and 'entries' in info:
                entry = info['entries'][0]
                filepath = ydl.prepare_filename(entry)
                return filepath
            else:
                raise Exception("No video found")
        except Exception as e:
            print(f"   ⚠️ YouTube download failed: {e}")
            return None


# ================================================
# PEXELS (Final Fallback)
# ================================================
def fetch_gameplay_pexels(topic=None):
    """Fetch gameplay footage from Pexels."""
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    
    if topic and "game" in topic.lower():
        query = topic
    else:
        query = "gaming gameplay"
    
    params = {
        "query": query,
        "per_page": 5,
        "orientation": "portrait"
    }
    
    print(f"🔍 Searching Pexels for gameplay footage: {query}")
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    
    video_paths = []
    for video in data.get('videos', []):
        for file in video.get('video_files', []):
            if file.get('height') >= 720 and file.get('width') <= file.get('height'):
                video_url = file['link']
                video_data = requests.get(video_url)
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
                temp_file.write(video_data.content)
                temp_file.close()
                video_paths.append(temp_file.name)
                break
    
    if not video_paths:
        print("   ⚠️ No gaming footage found. Falling back to 'action' footage...")
        params["query"] = "action"
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        for video in data.get('videos', []):
            for file in video.get('video_files', []):
                if file.get('height') >= 720 and file.get('width') <= file.get('height'):
                    video_url = file['link']
                    video_data = requests.get(video_url)
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
                    temp_file.write(video_data.content)
                    temp_file.close()
                    video_paths.append(temp_file.name)
                    break
    
    print(f"   ✅ Downloaded {len(video_paths)} clips from Pexels")
    return video_paths


# ================================================
# MAIN FETCHER (Tries Cloud → YouTube → Pexels)
# ================================================
def fetch_gameplay_footage(topic=None):
    """Fetch gameplay footage from: Google Drive → YouTube → Pexels (fallback)."""
    
    # 1. Try Google Drive first
    print("🎬 Fetching gameplay from Google Drive...")
    cloud_videos = fetch_cloud_gameplay(topic)
    if cloud_videos:
        return cloud_videos
    
    # 2. Try YouTube
    print("🎬 Fetching gameplay from YouTube...")
    youtube_video = download_youtube_gameplay(topic if topic else "Minecraft survival")
    if youtube_video:
        return [youtube_video]
    
    # 3. Final fallback: Pexels
    print("🎬 Falling back to Pexels...")
    return fetch_gameplay_pexels(topic)