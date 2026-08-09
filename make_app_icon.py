"""Generate the app icon for the TikTok developer app submission.

One-off script — run with:  python make_app_icon.py
Writes app_icon.png (1024x1024) to the repo root so you can download it and
upload it in the TikTok "App icon" field of the form.

Design: dark indigo→violet gradient, white rounded chat bubble, play triangle
(read the story, press play). Kept flat and simple so it stays legible at
small sizes, as TikTok requires.
"""
from PIL import Image, ImageDraw

SIZE = 1024

img = Image.new("RGBA", (SIZE, SIZE))
draw = ImageDraw.Draw(img)

# --- Vertical gradient background: #1e1b4b -> #4c1d95 ---
top = (30, 27, 75)
bottom = (76, 29, 149)
for y in range(SIZE):
    t = y / (SIZE - 1)
    color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    draw.line([(0, y), (SIZE, y)], fill=color + (255,))

# --- Rounded chat bubble (white), slightly inset ---
bubble_margin = 120
bubble = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
bd = ImageDraw.Draw(bubble)
bd.rounded_rectangle(
    [bubble_margin, bubble_margin, SIZE - bubble_margin, SIZE - bubble_margin],
    radius=110,
    fill=(255, 255, 255, 255),
)
# small tail at the bottom-left
bd.polygon(
    [(bubble_margin + 150, SIZE - bubble_margin - 30),
     (bubble_margin + 280, SIZE - bubble_margin - 30),
     (bubble_margin + 150, SIZE - bubble_margin + 90)],
    fill=(255, 255, 255, 255),
)
img = Image.alpha_composite(img, bubble)
draw = ImageDraw.Draw(img)

# --- Play triangle (indigo-violet gradient) ---
tri = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
td = ImageDraw.Draw(tri)
td.polygon(
    [(400, 340), (400, 684), (700, 512)],
    fill=(124, 58, 237, 255),
)
# subtle lighter edge
td.polygon(
    [(400, 340), (400, 684), (700, 512)],
    outline=(167, 139, 250, 255),
    width=8,
)
img = Image.alpha_composite(img, tri)

img.convert("RGB").save("app_icon.png", "PNG")
print("✅ app_icon.png written (1024x1024)")
