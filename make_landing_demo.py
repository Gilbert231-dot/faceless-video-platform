"""Render the landing-page demo clip from the newest generated video.

The app's website (index.html) embeds a short, high-quality loop cut from a
real generated video (assets/demo/real_demo.mp4 + real_poster.jpg). The
GitHub workflow runs this after every generation so the demo always shows
the newest video automatically — the user never has to touch it.

Also runnable locally:
    python make_landing_demo.py                       # newest output/*.mp4
    python make_landing_demo.py --input x.mp4 --output-dir out_dir
"""
import argparse
import glob
import os
import re
import subprocess

FRAME_W, FRAME_H = 1080, 1920
CLIP_SECONDS = 9.0
# Start at the very beginning so the demo shows the animated reddit-card
# intro (fade-in, hold while the title is narrated) — the first thing a
# TikTok reviewer sees on the landing page is the frame, matching the videos.
WINDOW_OFFSET_RATIO = 0.0
CRF = "18"        # same quality bar as the pipeline's caption pass
PRESET = "slow"


def get_duration(path):
    """Video length in seconds. Prefers ffprobe; falls back to parsing
    `ffmpeg -i` output so the script also works on machines with only ffmpeg
    (the GitHub runner has ffprobe installed by the system-deps step)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except OSError:
        pass  # ffprobe not installed — fall through to ffmpeg

    result = subprocess.run(
        ["ffmpeg", "-i", path], capture_output=True, text=True, timeout=60,
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError(f"Could not read duration from {path}")
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def newest_video():
    candidates = sorted(glob.glob("output/*_captioned_*.mp4")) or sorted(glob.glob("output/*.mp4"))
    if not candidates:
        raise SystemExit("No generated videos found in output/ — nothing to make a demo from.")
    return candidates[-1]


def main():
    parser = argparse.ArgumentParser(description="Refresh the landing-page demo clip.")
    parser.add_argument("--input", default=None, help="Video to sample (default: newest output/*.mp4)")
    parser.add_argument("--output-dir", default="assets/demo")
    args = parser.parse_args()

    video = args.input or newest_video()
    out_dir = args.output_dir
    os.makedirs(out_dir, exist_ok=True)
    out_mp4 = os.path.join(out_dir, "real_demo.mp4")
    out_poster = os.path.join(out_dir, "real_poster.jpg")

    duration = get_duration(video)
    # Clamp the window so it never runs past the end of the video.
    start = max(0.0, min(duration * WINDOW_OFFSET_RATIO, max(0.0, duration - CLIP_SECONDS)))
    length = min(CLIP_SECONDS, duration - start)
    if length < 2.5:
        raise SystemExit(f"Video too short for a demo clip ({duration:.1f}s).")

    # The loop: 1080x1920, H.264 CRF 18 (crisp, still fast to encode).
    print(f"Building landing demo from {os.path.basename(video)} "
          f"(window {start:.1f}s, {length:.1f}s long)...")
    cmd_clip = [
        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", video,
        "-vf", f"scale={FRAME_W}:{FRAME_H}:flags=lanczos", "-r", "30",
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", out_mp4,
    ]
    subprocess.run(cmd_clip, check=True, capture_output=True, timeout=600)

    # Poster frame from the start of the window.
    cmd_poster = [
        "ffmpeg", "-y", "-ss", f"{start + 0.4:.2f}", "-i", video,
        "-vf", f"scale={FRAME_W}:{FRAME_H}:flags=lanczos",
        "-frames:v", "1", "-q:v", "2", out_poster,
    ]
    subprocess.run(cmd_poster, check=True, capture_output=True, timeout=120)

    print(f"OK {out_mp4} ({os.path.getsize(out_mp4) / 1e6:.1f} MB)")
    print(f"OK {out_poster}")


if __name__ == "__main__":
    main()
