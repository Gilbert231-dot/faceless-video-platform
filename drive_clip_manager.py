import os
import json
import requests
import shutil
import tempfile
import subprocess
from pathlib import Path

from config import codec_needs_staging

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

# Background-source rotation (ON by default). Each video moves on to the
# NEXT footage file instead of continuing inside the one the previous video
# used. Why it matters: the offset cursor alone never ran a file dry — a
# ~30-minute source feeds ~10 minutes of footage for four 2-3 minute
# stories — so every video of the day came off the SAME file with only the
# offset changed, which is the signature duplicate-content detection looks
# for. OFF (CLIP_ROTATE_FILES=false) restores the old behaviour exactly.
ROTATE_FILES = os.environ.get("CLIP_ROTATE_FILES", "true").lower() not in (
    "false", "0", "no", "off")
# Videos a batch produces; used only for the "you need more files" note.
VIDEOS_PER_BATCH = int(os.environ.get("VIDEOS_PER_BATCH", "4"))

# Rotation must not trade a different background for a softer one. Some
# files in a footage folder are pre-cropped (884x1920 was one), and a
# 9:16 crop of one of those only gives the renderer ~884px of real width
# for a 1440px frame. The bar here is the renderer's own "native-quality"
# line (video_compile.py prints the same 1.25x figure): a candidate must
# fill the frame within that. 0 disables the check.
MAX_UPSCALE = float(os.environ.get("CLIP_MAX_UPSCALE", "1.25"))
OUTPUT_W = 1440                  # keep in sync with video_compile.OUTPUT_W

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
# SOURCE PROBE
# ================================
def _probe_source(path):
    """Return {codec, width, height, fps} for a footage file, or {} on failure."""
    import json as _json
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=codec_name,width,height,avg_frame_rate',
             '-of', 'json', path],
            capture_output=True, text=True, timeout=30,
        )
        streams = (_json.loads(r.stdout or '{}').get('streams') or [])
        if not streams:
            return {}
        s = streams[0]
        return {
            "codec": (s.get('codec_name') or '').lower(),
            "width": int(s.get('width') or 0),
            "height": int(s.get('height') or 0),
            "fps": s.get('avg_frame_rate') or '?',
        }
    except Exception:
        return {}


# Which codecs the renderer decodes directly is ONE shared rule in config.py
# (DIRECT_RENDER_CODECS / codec_needs_staging), so this file's log and the
# renderer's actual decision can never contradict each other. VP9 and AV1 are
# in that set: staging them measured ~5.6x slower than decoding them, and added
# a lossy generation on top. Note `force_staged` below is reported for
# diagnosis only — video_compile re-derives the decision from the spans'
# codecs, which is what actually selects the path.


# ================================
# FOOTAGE PLANNING (no re-encode)
# ================================
def plan_footage(duration_needed, peek_only=False):
    """Plan which source footage to use WITHOUT re-encoding any of it.

    peek_only=True returns ONLY the file this run will use and stops before
    any download and before any state write. The workflow calls it before it
    restores the footage cache, so the cache can be keyed by that exact file
    id (the old single-key cache saved an empty folder, so every run
    re-downloaded the whole multi-GB file four times a day).

    Returns {"spans": [...], "force_staged": bool}. Each span is
      {"path", "start", "duration", "file_id", "codec", "width", "height"}

    The renderer then decodes straight from these sources in ONE pass, so the
    footage is compressed exactly once at the final settings instead of being
    re-encoded at 4K first (see FOOTAGE_MODE in video_compile.py).

    Rotation semantics are identical to get_next_segment(): the offset
    advances through each file in name order, moves to the next file when one
    is exhausted, loops back to the first, and clip_state.json stays the
    single source of truth.

    NOTE: the walk below intentionally mirrors get_next_segment() rather than
    sharing code with it. That function is the STAGED path and is left
    byte-for-byte unchanged, so FOOTAGE_MODE=staged stays a true rollback.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    files = get_footage_files()
    state = load_state()
    current_id = state.get("video_id", "")
    offset = state["offset"]

    # per-file cursors: rotation means each file must remember where IT
    # stopped, not just where the last video ended.
    file_offsets = state.get("file_offsets")
    if not isinstance(file_offsets, dict):
        file_offsets = {}
        if current_id:
            file_offsets[current_id] = float(offset)
    else:
        file_offsets = {k: float(v) for k, v in file_offsets.items()
                        if isinstance(v, (int, float))}

    current_pos = 0
    known = False
    for i, f in enumerate(files):
        if f["id"] == current_id:
            current_pos = i
            known = True
            break
    else:
        offset = 0.0

    # Each file's real size, learned from the probe the first time it is
    # loaded, kept in the state so a file that cannot fill the frame is
    # skipped next run WITHOUT paying for its multi-GB download again.
    source_meta = state.get("source_meta")
    source_meta = dict(source_meta) if isinstance(source_meta, dict) else {}

    def _crop_width(w, h):
        """Real width a 9:16 crop of this source yields.

        Mirrors the render filter crop=min(iw,ih*9/16):ih in
        video_compile.py (the copy test in forge_ab checks they agree).
        """
        if not w or not h:
            return 0
        return int(min(w, h * 9.0 / 16.0))

    def _source_ok(fid):
        """True/False for a measured file, None when it has never been seen."""
        m = source_meta.get(fid) or {}
        cw = _crop_width(m.get("width"), m.get("height"))
        if not cw:
            return None
        if not MAX_UPSCALE:
            return True
        return (OUTPUT_W / cw) <= MAX_UPSCALE + 1e-9

    # Rotate the FILE. Only when the previous file is still known, so a
    # fresh state (or a replaced folder) still starts at the first file.
    rotated = False
    prev_pos = current_pos
    if ROTATE_FILES and len(files) > 1 and known:
        prev_name = files[current_pos]["name"]
        picked = None
        for step in range(1, len(files)):
            cand = (current_pos + step) % len(files)
            ok = _source_ok(files[cand]["id"])
            if ok is False:
                m = source_meta.get(files[cand]["id"], {})
                cw = _crop_width(m.get("width"), m.get("height"))
                print(f"[drive] not rotating onto {files[cand]['name']!r}: "
                      f"{m.get('width')}x{m.get('height')} gives only {cw}px "
                      f"of real width for a {OUTPUT_W}px frame "
                      f"({OUTPUT_W / cw:.2f}x upscale, above the "
                      f"{MAX_UPSCALE:.2f}x bar)")
                continue
            picked = cand
            break
        if picked is None:
            print(f"[drive] every other footage file is below the "
                  f"{MAX_UPSCALE:.2f}x bar, so this video stays on "
                  f"{prev_name!r} — add more 4K sources to the folder to "
                  f"rotate between them")
        else:
            current_pos = picked
            rotated = True
            next_name = files[current_pos]["name"]
            offset = float(file_offsets.get(files[current_pos]["id"], 0.0))
            print(f"[drive] File rotation: this video uses "
                  f"{next_name!r} from {offset:.1f}s "
                  f"(the previous video drew from {prev_name!r})")
            if len(files) < VIDEOS_PER_BATCH:
                print(f"[drive] NOTE: the folder holds {len(files)} footage "
                      f"file(s) for {VIDEOS_PER_BATCH} videos a batch, so they "
                      f"must be reused (A,B,A,B). Add more finished videos to "
                      f"the Drive folder and every video gets its own source.")

    # READ-ONLY PEEK: the workflow asks which file this run will use BEFORE it
    # restores the footage cache, so the cache can be keyed by that exact file
    # id. This is the same decision the full call would make — it is the code
    # above, not a copy of it — and it downloads nothing, writes no state.
    if peek_only:
        print(f"[drive] This run will draw footage from "
              f"{files[current_pos]['name']!r} (file id {files[current_pos]['id']})")
        return {"file_id": files[current_pos]["id"],
                "name": files[current_pos]["name"],
                "peek": True}

    spans = []
    taken = 0.0
    force_staged = False
    used = {}
    durs = {}
    # Guard against an infinite loop if every file in the folder is tiny.
    guard_iterations = 0
    max_iterations = max(len(files), 1) * 200

    def _advance(pos):
        return (pos + 1) % len(files)

    def _load(pos):
        """Download + probe the file at `pos`; returns (path, duration, info)."""
        fid = files[pos]["id"]
        path = os.path.join(CACHE_DIR, f"video_{fid}.mp4")
        if not os.path.exists(path):
            download_file(fid, path)
        try:
            dur = get_video_duration(path)
        except RuntimeError as e:
            print(f"[drive] Downloaded file is invalid ({e}); re-downloading")
            try:
                os.remove(path)
            except OSError:
                pass
            download_file(fid, path)
            dur = get_video_duration(path)
        info = _probe_source(path)
        if info.get("width") and info.get("height"):
            source_meta[files[pos]["id"]] = {
                "width": int(info["width"]), "height": int(info["height"]),
                "codec": str(info.get("codec") or ""),
            }
        return path, dur, info

    def _quality_start(pos, path, dur):
        """Best MEASURED start inside this file, or None to keep the cursor.

        The walk below uses whatever the cursor points at, so the delivered
        sharpness depends on where the cursor happens to be. Measured on the real
        footage, detail varies ~1.6x inside a single file. This asks clip_quality
        for the best-scoring window of THIS file; anything unexpected returns
        None and the old behaviour stands unchanged.
        """
        try:
            import clip_quality
        except Exception as e:
            print(f"[clipq] unavailable ({e}) - keeping the rotation cursor")
            return None
        try:
            start, _entry, result = clip_quality.plan_start(
                files[pos]["id"], path, duration_needed, dur)
        except Exception as e:
            print(f"[clipq] skipped ({e.__class__.__name__}: {e}) - "
                  f"keeping the rotation cursor")
            return None
        print(f"[clipq] {os.path.basename(path)}: "
              f"{clip_quality.describe(start, result, duration_needed)}")
        return start

    cache_path, duration, info = _load(current_pos)
    if rotated and _source_ok(files[current_pos]["id"]) is False:
        # First sight of this file and it is below the bar: undo the
        # rotation rather than ship a softer video. The size just learned is
        # remembered, so next run skips it before downloading anything.
        m = source_meta.get(files[current_pos]["id"], {})
        print(f"[drive] {files[current_pos]['name']!r} is "
              f"{m.get('width')}x{m.get('height')} — below the "
              f"{MAX_UPSCALE:.2f}x bar; keeping "
              f"{files[prev_pos]['name']!r} for this video")
        current_pos = prev_pos
        offset = float(file_offsets.get(files[current_pos]["id"],
                                       state.get("offset", 0.0)))
        cache_path, duration, info = _load(current_pos)
    _qs = _quality_start(current_pos, cache_path, duration)
    if _qs is not None:
        offset = _qs
    file_mb = os.path.getsize(cache_path) / (1024 * 1024) if os.path.exists(cache_path) else 0
    print(f"[drive] Cached video: {os.path.basename(cache_path)} ({file_mb:.1f} MB)")
    if info:
        print(f"[drive] Source video: {info.get('codec','?')} "
              f"{info.get('width','?')}x{info.get('height','?')} "
              f"fps={info.get('fps','?')} ({os.path.basename(cache_path)})")
        if codec_needs_staging(info.get("codec")):
            print(f"[drive] ⚠️ Source codec '{info['codec']}' is not in "
                  f"DIRECT_RENDER_CODECS — it will be normalized to H.264 first "
                  f"(one extra re-encode)")
            force_staged = True

    while taken < duration_needed - 0.05:
        guard_iterations += 1
        if guard_iterations > max_iterations:
            raise RuntimeError(
                f"[drive] Could not assemble {duration_needed:.1f}s of footage after "
                f"{guard_iterations} attempts — are the folder's files extremely short?"
            )

        remaining_in_file = duration - offset
        if remaining_in_file <= 0:
            current_pos = _advance(current_pos)
            offset = 0.0
            cache_path, duration, info = _load(current_pos)
            _qs = _quality_start(current_pos, cache_path, duration)
            if _qs is not None:
                offset = _qs
            if info and codec_needs_staging(info.get("codec")):
                force_staged = True
            continue

        take = min(remaining_in_file, duration_needed - taken)
        spans.append({
            "path": cache_path,
            "start": float(offset),
            "duration": float(take),
            "file_id": files[current_pos]["id"],
            "codec": (info or {}).get("codec", ""),
            "width": (info or {}).get("width", 0),
            "height": (info or {}).get("height", 0),
        })
        offset += take
        taken += take
        # where THIS file stopped, so a later rotation resumes here
        used[files[current_pos]["id"]] = offset
        durs[files[current_pos]["id"]] = duration

        if offset >= duration - 0.1:
            current_pos = _advance(current_pos)
            offset = 0.0
            if taken < duration_needed - 0.05:
                cache_path, duration, info = _load(current_pos)
                _qs = _quality_start(current_pos, cache_path, duration)
                if _qs is not None:
                    offset = _qs
                if info and codec_needs_staging(info.get("codec")):
                    force_staged = True

    new_offset = offset
    if new_offset >= duration - 0.1:
        current_pos = _advance(current_pos)
        new_offset = 0.0
    state["video_id"] = files[current_pos]["id"]
    state["offset"] = new_offset
    # Persist a cursor per file drawn from. A file run to its end is wrapped
    # to 0 so it stays usable next time rotation comes back round to it
    # (otherwise it would be skipped forever). Merged, never replaced: a Drive
    # listing hiccup that falls back to the hardcoded list must not wipe the
    # real folder's cursors.
    for _fid, _pos in used.items():
        _dur = durs.get(_fid)
        file_offsets[_fid] = 0.0 if (_dur and _pos >= _dur - 0.1) else _pos
    file_offsets.setdefault(files[current_pos]["id"], 0.0)
    merged = state.get("file_offsets")
    merged = dict(merged) if isinstance(merged, dict) else {}
    merged.update(file_offsets)
    state["file_offsets"] = merged
    seen = state.get("source_meta")
    seen = dict(seen) if isinstance(seen, dict) else {}
    seen.update(source_meta)
    state["source_meta"] = seen
    save_state(state)

    total = sum(s["duration"] for s in spans)
    print(f"[drive] Planned {len(spans)} footage span(s) totalling {total:.1f}s "
          f"across {len({s['file_id'] for s in spans})} file(s) — no re-encode")
    for s in spans:
        print(f"[drive]    {os.path.basename(s['path'])} @ {s['start']:.1f}s for {s['duration']:.1f}s")

    return {"spans": spans, "force_staged": force_staged}


# ================================
# MAIN FUNCTION: get_next_segment (STAGED path)
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

        # Extract in 60-second batches to avoid timeout on large files.
        # IMPORTANT (quality): this re-encode is LOSSY and its output feeds
        # the final CRF 15 veryslow render — the render can never recover
        # detail a low-quality intermediate throws away. ultrafast + CRF 18
        # visibly degraded fast-moving gameplay (blocking, softness), so we
        # encode near-transparent: CRF 15 + veryfast (the preset matters as
        # much as the CRF for motion). 480s per 60s batch keeps headroom on
        # a 2-core runner (veryfast is slower than ultrafast); a full-story
        # run can need 10+ batches.
        BATCH_SIZE = 60  # seconds per batch
        temp_dir = tempfile.mkdtemp(prefix="drive_seg_")
        batch_files = []
        taken = 0

        while taken < duration_needed:
            batch_remaining_in_file = duration - offset
            if batch_remaining_in_file <= 0:
                current_pos = (current_pos + 1) % len(files)
                offset = 0.0
                file_id = files[current_pos]["id"]
                cache_path = os.path.join(CACHE_DIR, f"video_{file_id}.mp4")
                if not os.path.exists(cache_path):
                    download_file(file_id, cache_path)
                duration = get_video_duration(cache_path)
                continue

            batch_take = min(BATCH_SIZE, duration_needed - taken, batch_remaining_in_file)
            batch_path = os.path.join(temp_dir, f"batch_{len(batch_files)}.mp4")
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(offset),
                '-i', cache_path,
                '-t', str(batch_take),
                '-c:v', 'libx264',
                '-preset', 'veryfast',
                '-crf', '15',
                '-pix_fmt', 'yuv420p',
                '-an',
                batch_path
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=480)

            if not os.path.exists(batch_path) or os.path.getsize(batch_path) < 1024:
                raise RuntimeError(f"Batch extraction failed for {cache_path} at offset {offset}")

            batch_files.append(batch_path)
            offset += batch_take
            taken += batch_take

            if offset >= duration - 0.1:
                current_pos = (current_pos + 1) % len(files)
                offset = 0.0

        # Concatenate all batches into one segment
        output_segment = f"/tmp/segment_{current_pos}_{int(offset)}_{int(offset+taken)}.mp4"
        if len(batch_files) == 1:
            os.rename(batch_files[0], output_segment)
        else:
            concat_file = os.path.join(temp_dir, "concat.txt")
            with open(concat_file, "w") as cf:
                for bf in batch_files:
                    cf.write(f"file '{bf}'\n")
            subprocess.run([
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', concat_file, '-c', 'copy', output_segment
            ], check=True, capture_output=True, timeout=60)

        shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Validate extracted segment
        if not os.path.exists(output_segment) or os.path.getsize(output_segment) < 1024:
            raise RuntimeError(
                f"[drive] Segment extraction produced invalid output "
                f"({os.path.getsize(output_segment) if os.path.exists(output_segment) else 0} bytes). "
                f"Source: {cache_path}"
            )

        # Update state
        new_offset = offset
        if new_offset >= duration - 0.1:
            current_pos = (current_pos + 1) % len(files)
            new_offset = 0.0

        state["video_id"] = files[current_pos]["id"]
        state["offset"] = new_offset
        save_state(state)

        return output_segment


if __name__ == "__main__":
    # Manual check: python drive_clip_manager.py peek
    #   -- prints the file id this run would use, downloading nothing.
    # The workflow calls this before restoring the footage cache so the cache
    # can be keyed per file instead of per pipeline version.
    if len(os.sys.argv) > 1 and os.sys.argv[1] == "peek":
        _p = plan_footage(0, peek_only=True)
        print(_p.get("file_id", ""))
    # Manual check: python drive_clip_manager.py list
    elif len(os.sys.argv) > 1 and os.sys.argv[1] == "list":
        files = get_footage_files()
        print(f"{len(files)} footage file(s):")
        for f in files:
            print(f"   {f['name']}  ->  {f['id']}")
    else:
        print(get_next_segment(10))
