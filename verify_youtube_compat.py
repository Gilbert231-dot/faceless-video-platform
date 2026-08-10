"""
verify_youtube_compat.py
========================
Pre-upload safety check: confirms every finished video matches YouTube's
processing requirements BEFORE it is uploaded, so a bad encode is caught
here instead of showing up as "Processing abandoned" on YouTube.

Checks (per file):
  * video codec   == h264
  * video profile == High (or Constrained High)
  * pixel format  == yuv420p
  * audio codec   == aac
  * faststart layout: the 'moov' box appears before the 'mdat' box
    (equivalent to what ffmpeg's -movflags +faststart produces).

Exits non-zero (and prints every violation) if any file fails. Usage:

    python verify_youtube_compat.py                  # check output/*_captioned_*.mp4
    python verify_youtube_compat.py file1.mp4 ...    # check specific files
"""

import glob
import json
import os
import subprocess
import sys

# Same discovery rule as the upload step in generate_video.yml.
DEFAULT_PATTERN = "output/*_captioned_*.mp4"
FALLBACK_PATTERN = "output/*.mp4"

ALLOWED_PROFILES = {"high", "constrained high"}
REQUIRED_PIX_FMT = "yuv420p"
REQUIRED_VCODEC = "h264"
REQUIRED_ACODEC = "aac"


def ffprobe_json(path, args):
    """Run ffprobe with the given args and return parsed JSON."""
    cmd = ["ffprobe", "-v", "error", "-of", "json"] + args + [path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("ffprobe not found on PATH — cannot verify YouTube "
                           "compatibility (install ffmpeg/ffprobe)")
    if result.returncode != 0:
        raise RuntimeError("ffprobe failed on %s: %s" % (path, result.stderr.strip()))
    return json.loads(result.stdout or "{}")


def probe_video_stream(path):
    """Return (codec, profile, pix_fmt) for the first video stream, or None."""
    data = ffprobe_json(path, ["-select_streams", "v:0",
                               "-show_entries", "stream=codec_name,profile,pix_fmt"])
    streams = data.get("streams") or []
    if not streams:
        return None
    s = streams[0]
    return (s.get("codec_name", ""), s.get("profile", ""), s.get("pix_fmt", ""))


def probe_audio_stream(path):
    """Return the codec of the first audio stream, or None if no audio."""
    data = ffprobe_json(path, ["-select_streams", "a:0",
                               "-show_entries", "stream=codec_name"])
    streams = data.get("streams") or []
    if not streams:
        return None
    return streams[0].get("codec_name", "")


def find_moov_mdat(path):
    """
    Walk the top-level MP4 boxes and return the byte offsets of the first
    'moov' and first 'mdat' boxes (or None for either). With +faststart,
    moov is written before mdat; without it, moov sits at the end of the file.
    """
    moov = mdat = None
    with open(path, "rb") as f:
        off = 0
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            size = int.from_bytes(header[:4], "big")
            box_type = header[4:8].decode("latin1", errors="replace")
            header_len = 8
            if size == 1:  # 64-bit extended size
                ext = f.read(8)
                if len(ext) < 8:
                    break
                size = int.from_bytes(ext, "big")
                header_len = 16
            elif size == 0:  # box runs to end of file
                f.seek(0, 2)
                size = f.tell() - off
            if box_type == "moov" and moov is None:
                moov = off
            if box_type == "mdat" and mdat is None:
                mdat = off
            if moov is not None and mdat is not None:
                break
            if size < header_len:
                break
            off += size
            f.seek(off)
    return moov, mdat


def check_file(path):
    """Return (ok, [problems]) for one video file."""
    problems = []

    # 1) Video stream: codec + profile + pixel format
    try:
        vs = probe_video_stream(path)
    except RuntimeError as e:
        return False, ["ffprobe failed: %s" % e]
    if vs is None:
        problems.append("no video stream found")
    else:
        codec, profile, pix_fmt = vs
        if codec.lower() != REQUIRED_VCODEC:
            problems.append("video codec is %r, expected %r" % (codec, REQUIRED_VCODEC))
        if profile.lower() not in ALLOWED_PROFILES:
            problems.append("video profile is %r, expected High" % (profile or "(none)"))
        if pix_fmt.lower() != REQUIRED_PIX_FMT:
            problems.append("pixel format is %r, expected %r" % (pix_fmt, REQUIRED_PIX_FMT))

    # 2) Audio stream: must exist and be AAC
    try:
        acodec = probe_audio_stream(path)
    except RuntimeError as e:
        return False, ["ffprobe failed: %s" % e]
    if acodec is None:
        problems.append("no audio stream found, expected AAC")
    elif acodec.lower() != REQUIRED_ACODEC:
        problems.append("audio codec is %r, expected %r" % (acodec, REQUIRED_ACODEC))

    # 3) faststart layout: moov before mdat
    try:
        moov, mdat = find_moov_mdat(path)
    except Exception as e:
        problems.append("could not scan mp4 layout: %s" % e)
    else:
        if moov is None:
            problems.append("no 'moov' box found (not a valid MP4?)")
        elif mdat is not None and moov > mdat:
            problems.append("'moov' box is AFTER 'mdat' (not faststart; "
                            "YouTube may fail to process)")

    return (len(problems) == 0, problems)


def discover_videos(explicit):
    """Resolve the files to check (explicit args, else the glob rule)."""
    if explicit:
        return explicit
    files = sorted(glob.glob(DEFAULT_PATTERN))
    if not files:
        files = sorted(glob.glob(FALLBACK_PATTERN))
    return files


def main():
    files = discover_videos(sys.argv[1:])
    if not files:
        print("No videos found to verify (%s or %s)." % (DEFAULT_PATTERN, FALLBACK_PATTERN))
        return 0

    failed = False
    for path in files:
        ok, problems = check_file(path)
        name = os.path.basename(path)
        if ok:
            print("PASS  %s  (H.264 High, yuv420p, AAC, faststart)" % name)
        else:
            failed = True
            print("FAIL  %s" % name)
            for p in problems:
                print("        - %s" % p)

    if failed:
        print("\n%d file(s) are NOT YouTube-compatible — fix before uploading." % sum(
            1 for _ in files))
        return 1
    print("\nAll %d file(s) passed — safe to upload." % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
