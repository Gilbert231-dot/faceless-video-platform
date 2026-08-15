"""
make_thumbnail.py — render the YouTube custom thumbnail for a generated video.

Our videos are VERTICAL 9:16 Shorts with the animated reddit-card intro burned
into the first seconds. YouTube's docs: vertical videos with 16:9 custom
thumbnails get REPLACED by auto-generated 4:5 thumbnails on the home/explore/
subscription pages — so the custom thumbnail must be vertical too (9:16,
recommended 2160x3840 for Shorts).

Design: the thumbnail IS the intro — we grab a frame from the finished video
at the card-hold moment (the card is fully on screen, over the gameplay), so
the thumbnail and the video's first seconds match exactly. No compositing.

Fallback: if ffmpeg isn't available (or frame grab fails), we composite the
reddit card PNG over a blurred, darkened still — same 9:16 output.

Output is a JPEG (YouTube's thumbnails.set accepts image/jpeg, max 2 MB).

Usage:
    python make_thumbnail.py <video.mp4> <card.png> <out.jpg>

Requires a verified channel + the youtube.upload scope (already in the
pipeline). The uploader calls this and handles a 403 (unverified channel)
gracefully — video still posts, thumbnail skipped.
"""

import os
import subprocess
import sys

THUMB_W, THUMB_H = 1080, 1920        # 9:16 — the Shorts-correct format
HOLD_TIME = 1.6                       # card is fully on screen by ~1.2-1.8s

CARD_MAX_W = 0.9 * THUMB_W           # fallback composite: card ~90% of width
CARD_MAX_H = 0.9 * THUMB_H


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


def _extract_frame(video_path, t, out_path, size=(THUMB_W, THUMB_H)):
    """Grab one frame at t seconds, scaled to `size`. Returns True on success."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
             "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]}",
             "-q:v", "2", out_path],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0 and os.path.exists(out_path)
    except (OSError, subprocess.SubprocessError):
        # ffmpeg not on PATH — caller falls back to the composite.
        return False


def _pick_hold_time(video_path):
    """Where the card is fully on screen: ~1.6s in, earlier for tiny videos."""
    dur = _ffprobe_duration(video_path)
    if dur and dur < 2.5:
        return max(dur / 3, 0.3)
    return HOLD_TIME


def _composite_fallback(video_path, card_path, out_path):
    """Blurred video still + sharp card — used only if frame grab fails."""
    from PIL import Image, ImageFilter, ImageDraw, ImageEnhance

    frame_path = out_path + ".frame.png"
    t = _pick_hold_time(video_path)
    have_frame = _extract_frame(video_path, t, frame_path)

    canvas = Image.new("RGB", (THUMB_W, THUMB_H), (18, 18, 26))
    if have_frame:
        bg = Image.open(frame_path).convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(28))
        bg = ImageEnhance.Brightness(bg).enhance(0.5)
        canvas = bg
    else:
        ImageDraw.Draw(canvas).rectangle([0, 0, THUMB_W, THUMB_H], fill=(24, 24, 34))

    if os.path.exists(card_path):
        card = Image.open(card_path).convert("RGBA")
        scale = min(CARD_MAX_W / card.width, CARD_MAX_H / card.height, 1.0)
        card = card.resize(
            (round(card.width * scale), round(card.height * scale)), Image.LANCZOS
        )
        x = (THUMB_W - card.width) // 2
        y = (THUMB_H - card.height) // 2
        shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [8, 10, card.width - 8, card.height + 10], radius=16, fill=(0, 0, 0, 110)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(12))
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(shadow, (x - 4, y - 6))
        canvas.alpha_composite(card, (x, y))
        canvas = canvas.convert("RGB")

    canvas.save(out_path, "JPEG", quality=90)
    if os.path.exists(frame_path):
        os.remove(frame_path)
    return True


def _make_thumbnail(video_path, card_path, out_path):
    """Primary: a straight frame of the video at the card-hold moment — the
    thumbnail and the video intro are then the SAME image, guaranteed."""
    t = _pick_hold_time(video_path)
    if _extract_frame(video_path, t, out_path):
        return True
    # ffmpeg missing or the grab failed — composite instead.
    return _composite_fallback(video_path, card_path, out_path)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    _make_thumbnail(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"✅ Thumbnail saved: {sys.argv[3]}")
