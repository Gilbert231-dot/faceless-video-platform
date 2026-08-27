import os
import json
import requests
import subprocess
from pathlib import Path

# gdown is imported lazily inside download_file(): it is only needed when a
# file actually has to be downloaded (the runner installs it via
# requirements.txt), and importing the module shouldn't require it.

# ================================
# CONFIGURATION
# ================================
STATE_FILE = "clip_state.json"
CACHE_DIR = "cached_videos"

# Fallback footage list (used only when dynamic folder listing is not
# configured). New files added to the Drive folder are picked up
# automatically once GDRIVE_FOLDER_ID + GDRIVE_API_KEY are set (see
# get_footage_files / list_folder_files).
DRIVE_URLS = [
    "1QjdFKRf1PmmQncLGrI59hD7yKngqui_r",
    "1csHaO2EUANXLexMSxG-ltI77dvCIlH2P",
    "1XSwwDED61z2MbM7QSEGhH0W9I7qoSd-Z",
    "1JIy54c7ljm4njW7lqaHzlpOhIMVplUzs",
    "1183ENgEB0H55gwVYDFzqJ4bFrOwXo5OM",
    "1CcysUW40RnBFV4LEpLHv66NKsXOh_NU_",
]

# Dynamic listing: set both in the workflow env (GitHub secrets):
#   GDRIVE_FOLDER_ID — the cloud folder's ID (from its URL:
#       drive.google.com/drive/folders/<THIS_PART>) — folder must be shared
#       "Anyone with the link" (Viewer) for API-key listing to see it.
#   GDRIVE_API_KEY   — a Google Cloud API key with the Drive API enabled
#       (console.cloud.google.com → APIs & Services → Credentials).
DRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
GDRIVE_API_KEY = os.environ.get("GDRIVE_API_KEY", "").strip()

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
    import gdown  # lazy: only needed when actually downloading
    print(f"[drive] Downloading file ID {file_id} to {dest_path} ...")
    url = f"https://drive.google.com/uc?id={file_id}"
    gdown.download(url, dest_path, quiet=False)
    print(f"[drive] Download complete: {dest_path}")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        # Migrate old index-based state to new ID-based state
        if "video_index" in state and "video_id" not in state:
            files = get_footage_files()
            idx = state["video_index"]
            if idx < len(files):
                state["video_id"] = files[idx]["id"]
            else:
                state["video_id"] = files[0]["id"]
            state.pop("video_index")
        return state
    return {"video_id": "", "offset": 0.0}
  
def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

# ================================
# DYNAMIC FOLDER LISTING
# ================================
def list_folder_files(folder_id, api_key):
    """List playable videos in a public Drive folder via the Drive API.

    API-key only (no OAuth): works when the folder is shared "Anyone with
    the link" (Viewer). Returns [{id, name}, ...] sorted by name so the
    rotation order stays stable when files are added or removed.
    """
    params = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "nextPageToken, files(id, name, mimeType)",
        "pageSize": 200,
        "key": api_key,
    }
    files = []
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            params=params, timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Drive API error {r.status_code}: {r.text[:200]}")
        data = r.json()
        for f in data.get("files", []):
            name = f.get("name", "")
            mime = f.get("mimeType", "")
            if mime == "video/mp4" or name.lower().endswith(".mp4"):
                files.append({"id": f["id"], "name": name})
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    files.sort(key=lambda f: f["name"].lower())
    return files

def get_footage_files():
    """The ordered footage list: dynamic folder listing when configured,
    otherwise the hardcoded DRIVE_URLS (kept as a zero-setup fallback)."""
    if DRIVE_FOLDER_ID and GDRIVE_API_KEY:
        try:
            files = list_folder_files(DRIVE_FOLDER_ID, GDRIVE_API_KEY)
            if files:
                print(f"[drive] {len(files)} footage files via dynamic folder "
                      f"listing (folder {DRIVE_FOLDER_ID[:8]}...)")
                return files
            print("[drive] folder listing returned no files - using hardcoded list")
        except Exception as e:
            print(f"[drive] folder listing failed ({e}) - using hardcoded list")
    else:
        if not DRIVE_FOLDER_ID:
            print("[drive] GDRIVE_FOLDER_ID not set - using hardcoded footage list "
                  "(set it + GDRIVE_API_KEY for automatic folder pickup)")
    return [{"id": fid, "name": f"video_{i}.mp4"} for i, fid in enumerate(DRIVE_URLS)]

# ================================
# MAIN FUNCTION: get_next_segment
# ================================
def get_next_segment(duration_needed):
    """
    Returns the path to a temporary video file containing a segment
    of the required duration, taken from the next available portion of
    the footage files (Drive folder if configured, else DRIVE_URLS).

    Rotation: advances an offset through each file, moves to the next file
    when one is exhausted, and loops back to the start once everything is
    consumed. clip_state.json (repo-pushed) is the single source of truth.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    files = get_footage_files()
    state = load_state()
    current_id = state.get("video_id", "")
    offset = state["offset"]

    # Find current video by ID in the sorted lists
    current_pos = 0
    for i, f in enumerate(files):
        if f["id"] == current_id:
            current_pos = i
            break
    else:
        # Current video not found (deleted/replaced) — start from beginning
        offset = 0.0

    while True:
        file_id = files[current_pos]["id"]
        # Cache by FILE ID (not index) so renames/reorders in the folder
        # never make the pipeline re-download or grab the wrong file.
        cache_path = os.path.join(CACHE_DIR, f"video_{file_id}.mp4")

        # Download if not already cached
        if not os.path.exists(cache_path):
            download_file(file_id, cache_path)

        # Log file size and video properties for diagnostics
        file_mb = os.path.getsize(cache_path) / (1024 * 1024) if os.path.exists(cache_path) else 0
        print(f"[drive] Cached video: {os.path.basename(cache_path)} ({file_mb:.1f} MB)")
        if file_mb < 1.0:
            print(f"[drive] ⚠️ WARNING: Video file is suspiciously small ({file_mb:.1f} MB) — may be corrupt or incomplete")
        
        # Probe video properties (codec, resolution) for diagnostics
        try:
            probe_cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name,width,height,pix_fmt',
                '-of', 'json',
                cache_path
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            import json as _json
            probe_data = _json.loads(probe_result.stdout or '{}')
            streams = probe_data.get('streams') or []
            if streams:
                s = streams[0]
                print(f"[drive] Source video: {s.get('codec_name','?')} "
                      f"{s.get('width','?')}x{s.get('height','?')} "
                      f"pix_fmt={s.get('pix_fmt','?')} "
                      f"({os.path.basename(cache_path)})")
        except Exception:
            pass  # non-critical — log but don’t abort
        
        # Verify the file is valid
        try:
            duration = get_video_duration(cache_path)
        except RuntimeError as e:
            print(f"[drive] Downloaded file is invalid: {e}")
            print("[drive] Deleting corrupt file and retrying...")
            os.remove(cache_path)
            download_file(file_id, cache_path)
            duration = get_video_duration(cache_path)  # try again

        # If offset exceeds duration, move to next video
        if offset >= duration:
            current_pos = (current_pos + 1) % len(files)
            offset = 0.0
            continue

        # Determine how much we can take from this video
        remaining = duration - offset
        take = min(duration_needed, remaining)

        # FIXED (exit-234 crash): -c copy preserves the source's original
        # codec (VP9, AV1, etc.) and container metadata. When the source
        # has sparse keyframes or a non-H.264 codec (common with Google
        # Drive's re-encoded uploads), the copy-cut produces a file that
        # passes ffprobe but fails when ffmpeg decodes frames. Re-encoding
        # with ultrafast normalizes to clean H.264 yuv420p.
        output_segment = f"/tmp/segment_{current_pos}_{int(offset)}_{int(offset+take)}.mp4"
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(offset),
            '-i', cache_path,
            '-t', str(take),
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '18',
            '-pix_fmt', 'yuv420p',
            '-an',
            output_segment
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
        
        # Validate extracted segment
        if not os.path.exists(output_segment) or os.path.getsize(output_segment) < 1024:
            raise RuntimeError(
                f"[drive] Segment extraction produced invalid output "
                f"({os.path.getsize(output_segment) if os.path.exists(output_segment) else 0} bytes). "
                f"Source: {cache_path}"
            )

        # Update state
        new_offset = offset + take
        if new_offset >= duration - 0.1:
            current_pos = (current_pos + 1) % len(files)
            new_offset = 0.0

        state["video_id"] = files[current_pos]["id"]
        state["offset"] = new_offset
        save_state(state)

        return output_segment


if __name__ == "__main__":
    # Manual check: python drive_clip_manager.py list
    if len(os.sys.argv) > 1 and os.sys.argv[1] == "list":
        files = get_footage_files()
        print(f"{len(files)} footage file(s):")
        for f in files:
            print(f"   {f['name']}  ->  {f['id']}")
    else:
        print(get_next_segment(10))
