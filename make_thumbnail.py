"""
make_thumbnail.py — render a 1280x720 thumbnail that MATCHES the video's
animated reddit-card intro, for the YouTube custom thumbnail.

Design: the intro is the reddit post card over the gameplay footage. The
thumbnail reproduces that exact look as a 16:9 image:

  - background: a blurred, darkened still from the video (so the thumbnail
    looks like the video, not a flat color)
  - foreground: the sharp reddit card, centered, sized to dominate the frame

Output is a JPEG (YouTube's thumbnails.set accepts image/jpeg, max 2 MB).

Usage:
    python make_thumbnail.py <video.mp4> <card.png> <out.jpg>

YouTube custom thumbnails require a verified channel + the youtube.upload
scope (which the pipeline already has). The uploader calls this and handles
a 403 (unverified channel) gracefully — video still posts, thumbnail skipped.
"""

import os
import subprocess
import sys

THUMB_W, THUMB_H = 1280, 720
CARD_MAX_W = 0.62 * THUMB_W      # card occupies ~62% of the thumbnail width
CARD_MAX_H = 0.94 * THUMB_H

_fonts = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]


def _ffprobe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _extract_frame(video_path, t, out_path):
    """Grab one frame at t seconds as a PNG. Returns True on success."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
             "-frames:v", "1", "-vf", "scale=720:-2", out_path],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0 and os.path.exists(out_path)
    except (OSError, subprocess.SubprocessError):
        # ffmpeg not on PATH — caller falls back to the flat background.
        return False


def _pick_hold_time(video_path):
    """Choose a time ~1.2s in where the card should be fully on screen."""
    dur = _ffprobe_duration(video_path)
    if dur and dur < 2.0:
        return max(dur / 3, 0.3)
    return 1.2


def _make_thumbnail(video_path, card_path, out_path):
    from PIL import Image, ImageFilter, ImageDraw, ImageEnhance, ImageFont

    # 1) Frame from the video for the background (best-effort).
    frame_path = out_path + ".frame.png"
    t = _pick_hold_time(video_path)
    have_frame = _extract_frame(video_path, t, frame_path)

    canvas = Image.new("RGB", (THUMB_W, THUMB_H), (18, 18, 26))
    if have_frame:
        bg = Image.open(frame_path).convert("RGB")
        # scale to cover the canvas, center-crop
        bg = bg.resize((THUMB_W, THUMB_H), Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(28))
        bg = ImageEnhance.Brightness(bg).enhance(0.5)
        canvas = bg
    else:
        # fallback: dark gradient-ish background
        d = ImageDraw.Draw(canvas)
        d.rectangle([0, 0, THUMB_W, THUMB_H], fill=(24, 24, 34))

    # 2) The sharp reddit card, centered and dominant.
    if os.path.exists(card_path):
        card = Image.open(card_path).convert("RGBA")
        # fit inside CARD_MAX_W x CARD_MAX_H
        scale = min(CARD_MAX_W / card.width, CARD_MAX_H / card.height, 1.0)
        card = card.resize(
            (round(card.width * scale), round(card.height * scale)), Image.LANCZOS
        )
        x = (THUMB_W - card.width) // 2
        y = (THUMB_H - card.height) // 2
        # subtle drop shadow behind the card
        shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [8, 10, card.width - 8, card.height + 10], radius=16, fill=(0, 0, 0, 110)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(12))
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(shadow, (x - 4, y - 6))
        canvas.alpha_composite(card, (x, y))
        canvas = canvas.convert("RGB")

    # 3) Save as JPEG.
    canvas.save(out_path, "JPEG", quality=90)
    if os.path.exists(frame_path):
        os.remove(frame_path)
    return True


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    _make_thumbnail(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"✅ Thumbnail saved: {sys.argv[3]}")
