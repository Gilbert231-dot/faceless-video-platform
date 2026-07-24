import os
import json
import subprocess
from pathlib import Path

STATE_FILE = "clip_state.json"
VIDEO_FOLDER = "assets/videos"   # place your large .mp4 files here

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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"video_index": 0, "offset": 0.0}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_next_segment(duration_needed):
    """
    Returns the path to a temporary video file containing the next segment
    of the required duration, and advances the offset.
    """
    state = load_state()
    video_files = sorted([f for f in os.listdir(VIDEO_FOLDER) if f.endswith('.mp4')])
    if not video_files:
        raise RuntimeError(f"No video files found in {VIDEO_FOLDER}")

    current_idx = state["video_index"]
    offset = state["offset"]

    # Loop until we find a valid segment
    while current_idx < len(video_files):
        video_path = os.path.join(VIDEO_FOLDER, video_files[current_idx])
        duration = get_video_duration(video_path)

        # If offset is beyond the end of this video, move to next
        if offset >= duration:
            current_idx += 1
            offset = 0.0
            continue

        # Calculate how much we can take from this video
        remaining = duration - offset
        take = min(duration_needed, remaining)

        # Extract the segment
        output_segment = f"/tmp/segment_{current_idx}_{int(offset)}_{int(offset+take)}.mp4"
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(offset),
            '-i', video_path,
            '-t', str(take),
            '-c', 'copy',          # no re-encoding, fast
            output_segment
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # Update state
        new_offset = offset + take
        if new_offset >= duration - 0.1:  # close to end, move to next video
            current_idx += 1
            new_offset = 0.0

        state["video_index"] = current_idx
        state["offset"] = new_offset
        save_state(state)

        return output_segment

    raise RuntimeError("No more video footage available (all videos consumed).")
