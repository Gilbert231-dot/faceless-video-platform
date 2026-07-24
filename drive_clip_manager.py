import os
import json
import gdown
import subprocess
from pathlib import Path

# ================================
# CONFIGURATION
# ================================
STATE_FILE = "clip_state.json"
CACHE_DIR = "cached_videos"
DRIVE_URLS = [
    "1QjdFKRf1PmmQncLGrI59hD7yKngqui_r",
    "1csHaO2EUANXLexMSxG-ltI77dvCIlH2P",
    "1XSwwDED61z2MbM7QSEGhH0W9I7qoSd-Z",
    "1JIy54c7ljm4njW7lqaHzlpOhIMVplUzs",
    "1183ENgEB0H55gwVYDFzqJ4bFrOwXo5OM",
    "1CcysUW40RnBFV4LEpLHv66NKsXOh_NU_",
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {result.stderr}")
    return float(result.stdout.strip())

def download_file(file_id, dest_path):
    """Download a file from Google Drive using its file ID."""
    print(f"⬇️ Downloading file ID {file_id} to {dest_path} ...")
    url = f"https://drive.google.com/uc?id={file_id}"
    gdown.download(url, dest_path, quiet=False)
    print(f"✅ Download complete: {dest_path}")

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
        file_id = DRIVE_URLS[current_idx]
        cache_path = os.path.join(CACHE_DIR, f"video_{current_idx}.mp4")

        # Download if not already cached
        if not os.path.exists(cache_path):
            download_file(file_id, cache_path)

        # Verify the file is valid
        try:
            duration = get_video_duration(cache_path)
        except RuntimeError as e:
            print(f"⚠️ Downloaded file is invalid: {e}")
            print("🔄 Deleting corrupt file and retrying...")
            os.remove(cache_path)
            download_file(file_id, cache_path)
            duration = get_video_duration(cache_path)  # try again

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
            '-c', 'copy',
            output_segment
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # Update state
        new_offset = offset + take
        if new_offset >= duration - 0.1:
            current_idx += 1
            new_offset = 0.0

        state["video_index"] = current_idx
        state["offset"] = new_offset
        save_state(state)

        return output_segment

    raise RuntimeError("No more video footage available (all videos consumed).")
