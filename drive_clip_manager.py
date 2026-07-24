import os
import json
import gdown
import requests
import subprocess
from pathlib import Path

# ================================
# CONFIGURATION
# ================================
STATE_FILE = "clip_state.json"
CACHE_DIR = "cached_videos"          # where downloaded files are stored
DRIVE_URLS = [                       # your public Google Drive video URLs
    "https://drive.google.com/uc?export=download&id=1QjdFKRf1PmmQncLGrI59hD7yKngqui_r",
    "https://drive.google.com/uc?export=download&id=1csHaO2EUANXLexMSxG-ltI77dvCIlH2P",
    "https://drive.google.com/uc?export=download&id=1XSwwDED61z2MbM7QSEGhH0W9I7qoSd-Z",
    "https://drive.google.com/uc?export=download&id=1JIy54c7ljm4njW7lqaHzlpOhIMVplUzs",
    "https://drive.google.com/uc?export=download&id=1183ENgEB0H55gwVYDFzqJ4bFrOwXo5OM",
    "https://drive.google.com/uc?export=download&id=1CcysUW40RnBFV4LEpLHv66NKsXOh_NU_",
    # ... add all 6 URLs here
]

# ================================
# HELPERS
# ================================
def get_video_duration(video_path):
    """Return duration in seconds using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def download_file(url, dest_path):
    """
    Download a file from a Google Drive URL using gdown.
    Handles the warning page and large files automatically.
    """
    print(f"⬇️ Downloading {url} to {dest_path} ...")
    # gdown can use the standard share link format:
    # https://drive.google.com/uc?id=FILE_ID
    gdown.download(url, dest_path, quiet=False)
    print(f"✅ Download complete: {dest_path}")

def get_video_duration(video_path):
    """Return duration in seconds using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {result.stderr}")
    return float(result.stdout.strip())

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"video_index": 0, "offset": 0.0}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

# ================================
# MAIN FUNCTION: get_next_segment
# ================================
def get_next_segment(duration_needed):
    """
    Returns the path to a temporary video file containing a segment
    of the required duration, taken from the next available portion of
    the video files in DRIVE_URLS.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    state = load_state()
    current_idx = state["video_index"]
    offset = state["offset"]

    # Loop over videos
    while current_idx < len(DRIVE_URLS):
        url = DRIVE_URLS[current_idx]
        # Determine cache filename (e.g., video_0.mp4)
        cache_path = os.path.join(CACHE_DIR, f"video_{current_idx}.mp4")

        # Download if not already cached
        if not os.path.exists(cache_path):
            download_file(url, cache_path)

        # Get total duration
        duration = get_video_duration(cache_path)

        # If offset exceeds duration, move to next video
        if offset >= duration:
            current_idx += 1
            offset = 0.0
            continue

        # Determine how much we can take from this video
        remaining = duration - offset
        take = min(duration_needed, remaining)

        # Extract segment using FFmpeg (fast, no re-encode)
        output_segment = f"/tmp/segment_{current_idx}_{int(offset)}_{int(offset+take)}.mp4"
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(offset),
            '-i', cache_path,
            '-t', str(take),
            '-c', 'copy',          # copy video & audio streams (no re-encode)
            output_segment
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # Update state
        new_offset = offset + take
        if new_offset >= duration - 0.1:   # close to end, advance to next video
            current_idx += 1
            new_offset = 0.0

        state["video_index"] = current_idx
        state["offset"] = new_offset
        save_state(state)

        return output_segment

    raise RuntimeError("No more video footage available (all videos consumed).")
