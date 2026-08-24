#!/usr/bin/env python3
"""create_bouncing_shadow.py — Generate a VISIBLE bouncing shadow MOV."""
import os, subprocess, tempfile, shutil, math
from PIL import Image, ImageFilter

FPS = 30
DURATION = 6.0
W = 600
H = 60


def make_shadow(w, h, alpha):
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for y in range(h):
        a = int(alpha * (1 - y / max(h, 1)))
        for x in range(w):
            img.putpixel((x, y), (0, 0, 0, a))
    return img.filter(ImageFilter.GaussianBlur(radius=12))


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base, "assets", "overlays")
    os.makedirs(out_dir, exist_ok=True)

    tmpdir = tempfile.mkdtemp()
    frames = os.path.join(tmpdir, "f")
    os.makedirs(frames)
    total = int(DURATION * FPS)

    for i in range(total):
        t = i / FPS
        if t < 1.5:
            bounce = math.sin(t * 12) * math.exp(-t * 3)
            wf = 0.4 + 0.6 * (1 + bounce) / 2
            wf = max(0.3, min(1.0, wf))
        else:
            wf = 1.0

        if t < 0.05:
            a = int(255 * t / 0.05)
        elif t > DURATION - 0.3:
            a = int(255 * (DURATION - t) / 0.3)
        else:
            a = 255

        cw = max(10, int(W * wf))
        ch = max(5, int(H * wf))
        sh = make_shadow(cw, ch, a)

        canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ox = (W - cw) // 2
        oy = (H - ch) // 2
        canvas.paste(sh, (ox, oy), sh)
        canvas.save(os.path.join(frames, f"{i:04d}.png"))

    out = os.path.join(out_dir, "bouncing_shadow.mov")
    subprocess.run([
        'ffmpeg', '-y', '-framerate', str(FPS),
        '-i', os.path.join(frames, '%04d.png'),
        '-c:v', 'qtrle', '-pix_fmt', 'argb', out
    ], check=True, capture_output=True, timeout=120)

    shutil.rmtree(tmpdir)
    print(f"[DONE] {out} ({W}x{H})")


if __name__ == "__main__":
    main()
