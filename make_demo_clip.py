"""Build the demo clip used in the TikTok app-review screen recording.

One-off script — run with:  python make_demo_clip.py
Writes assets/demo/tiktok_demo_clip.mp4 (1080x1920, ~9s) — a short branded
clip that the review demo can post through tiktok_demo.py. The point of the
demo is the POSTING flow (OAuth -> upload -> publish), not the video itself,
so this is intentionally simple: three title cards with a subtle zoom.
"""
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

SIZE = (1080, 1920)
SCENE_SECONDS = 3

SCENES = [
    ("Faceless Video Creator Poster", "AI stories  \u2192  voiceover  \u2192  captions  \u2192  TikTok"),
    ("Demo: official Content Posting API", "video/init \u00b7 direct upload \u00b7 status poll"),
    ("Posts PRIVATE to your own account", "user.info.basic + video.publish \u00b7 is_aigc disclosure on"),
]


def _font(size: int, bold: bool = False):
    for name in ("arialbd.ttf" if bold else "arial.ttf",):
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _scene(title: str, subtitle: str, index: int) -> str:
    img = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(img)
    top = (30, 27, 75)
    bottom = (76, 29, 149)
    for y in range(SIZE[1]):
        t = y / (SIZE[1] - 1)
        draw.line([(0, y), (SIZE[0], y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))

    t_font = _font(72, bold=True)
    s_font = _font(40)

    # title, wrapped if needed
    lines = []
    current = ""
    for word in title.split():
        if draw.textlength(current + " " + word, font=t_font) < SIZE[0] - 160:
            current = (current + " " + word).strip()
        else:
            lines.append(current)
            current = word
    lines.append(current)

    y = SIZE[1] // 2 - 90 * len(lines)
    for ln in lines:
        w = draw.textlength(ln, font=t_font)
        draw.text(((SIZE[0] - w) / 2, y), ln, font=t_font, fill=(255, 255, 255))
        y += 96
    y += 30
    for ln in subtitle.split("\n"):
        w = draw.textlength(ln, font=s_font)
        draw.text(((SIZE[0] - w) / 2, y), ln, font=s_font, fill=(196, 181, 253))
        y += 56

    path = os.path.join("assets", "demo", f"scene_{index}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path


def main():
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    scenes = [_scene(t, s, i) for i, (t, s) in enumerate(SCENES)]

    inputs = []
    for i, sc in enumerate(scenes):
        inputs += ["-loop", "1", "-t", str(SCENE_SECONDS), "-i", sc]

    # Static title cards concatenated (no zoompan — it's slow and memory-hungry
    # on a laptop, and the demo is about the POSTING flow, not the video).
    concat_in = "".join(f"[{i}:v]" for i in range(len(scenes)))
    out = os.path.join("assets", "demo", "tiktok_demo_clip.mp4")
    cmd = [ffmpeg, "-y"] + inputs + [
        "-filter_complex", f"{concat_in}concat=n={len(scenes)}:v=1:a=0[vout]",
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    for sc in scenes:
        os.unlink(sc)
    size = os.path.getsize(out)
    print(f"OK: {out} ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
