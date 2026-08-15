"""
make_thumbnail.py — render the YouTube custom thumbnail for a generated video.

Our videos are VERTICAL 9:16 Shorts with the animated reddit-card intro burned
into the first seconds. YouTube replaces 16:9 custom thumbnails on vertical
videos with auto-generated 4:5 ones on the browse surfaces — so the custom
thumbnail must be vertical. We output at 2160x3840 (4K), the resolution
YouTube recommends for Shorts thumbnails.

SHARPER THAN THE VIDEO: instead of downscaling a frame of the finished video,
we rebuild the shot at higher resolution than the 1440p encode:

  - background: a clean frame grabbed BEFORE the card fades in, upscaled to
    2160x3840 with lanczos + a strong unsharp mask (looks crisper than the
    original 1440p still)
  - foreground: the reddit card drawn from its NATIVE 2190px PNG (rescaled to
    2160 wide, nearly 1:1) — genuinely higher resolution than the 1440px card
    inside the video, so text edges are cleaner than in the video itself

The result: the thumbnail text is sharper than the video frame, and the
background is visibly crisper than a straight 1440p crop.

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

THUMB_W, THUMB_H = 2160, 3840        # 4K 9:16 — YouTube's recommended Shorts size
PRE_CARD_T = 0.15                     # frame grabbed BEFORE the card fades in (0.35s)
CARD_TOP_RATIO = 0.14                 # card top edge sits at 14% of height (matches the intro)


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
    """Grab one frame at t seconds, scaled+sharpened to `size`.

    Returns True on success. The frame is never blurred — lanczos upscale
    plus a strong unsharp mask so the background looks crisper than the
    original encode.
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
             "-frames:v", "1",
             "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos,"
                     "unsharp=5:5:1.0:5:5:0.0",
             "-q:v", "1", out_path],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0 and os.path.exists(out_path)
    except (OSError, subprocess.SubprocessError):
        # ffmpeg not on PATH — caller falls back to the flat-background composite.
        return False


def _load_card(card_path):
    """Return the reddit card cropped to its visible white rect (transparent
    margins removed), so we can place it exactly where it appears in the
    video intro and scale it 1:1 to the 4K canvas."""
    from PIL import Image
    if not os.path.exists(card_path):
        return None
    im = Image.open(card_path).convert("RGBA")
    w, h = im.size
    px = im.load()
    # Find the white card bounds (the nontransparent white region).
    left = right = top = bottom = None
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 40 and r > 235 and g > 235 and b > 235:
                if left is None or x < left:
                    left = x
                if right is None or x > right:
                    right = x
                if top is None or y < top:
                    top = y
                if bottom is None or y > bottom:
                    bottom = y
    if None in (left, right, top, bottom):
        return im  # fallback: whole image
    return im.crop((left, top, right + 1, bottom + 1))


def _make_thumbnail(video_path, card_path, out_path):
    """Rebuild the intro shot at 4K: sharpened background + native-res card.

    This is sharper than a frame of the finished video: the card PNG is 2190px
    native (vs 1440px inside the video) and the background gets a lanczos +
    unsharp upscale pass instead of a plain crop.
    """
    from PIL import Image, ImageDraw, ImageEnhance

    # 1) Background: a frame grabbed BEFORE the card fades in, so the shot is
    #    clean — the card is re-added from its native PNG in step 2.
    bg_path = out_path + ".bg.png"
    have_bg = _extract_frame(video_path, PRE_CARD_T, bg_path)

    canvas = Image.new("RGB", (THUMB_W, THUMB_H), (18, 18, 26))
    if have_bg:
        bg = Image.open(bg_path).convert("RGB")
        if bg.size != (THUMB_W, THUMB_H):
            bg = bg.resize((THUMB_W, THUMB_H), Image.LANCZOS)
        bg = ImageEnhance.Brightness(bg).enhance(0.62)  # slightly darker for pop
        canvas = bg
    else:
        ImageDraw.Draw(canvas).rectangle([0, 0, THUMB_W, THUMB_H], fill=(24, 24, 34))

    # 2) Foreground: the card from its native PNG, scaled to (nearly) full
    #    width — crisper text than the card rendered inside the 1440p video.
    card = _load_card(card_path)
    if card is not None:
        # Card spans the full width in the video intro; scale native -> 4K width.
        scale = THUMB_W / card.width
        card = card.resize((THUMB_W, round(card.height * scale)), Image.LANCZOS)
        x = 0
        y = round(CARD_TOP_RATIO * THUMB_H)
        # soft drop shadow behind the card for separation from the footage
        from PIL import ImageFilter
        shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [6, 8, card.width - 6, card.height + 8], radius=20, fill=(0, 0, 0, 120)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(shadow, (x, y - 4))
        canvas.alpha_composite(card, (x, y))
        canvas = canvas.convert("RGB")

    # 3) Save as a high-quality JPEG (must stay under 2 MB for the API).
    canvas.save(out_path, "JPEG", quality=92)
    if os.path.exists(bg_path):
        os.remove(bg_path)
    return True


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    _make_thumbnail(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"✅ Thumbnail saved: {sys.argv[3]}")
