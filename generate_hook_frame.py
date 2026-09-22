"""
generate_hook_frame.py — render the ANIMATED HOOK CARD that opens every video.

Why this exists (replacing the reddit post mock-up as the intro)
---------------------------------------------------------------
TikTok's own Content Check Lite flagged the uploads under "Unoriginal,
low-quality, and QR code content" and pointed at the opening seconds. The old
intro was a rendered screenshot of the Reddit app UI — a still image of another
platform's post, complete with a vote pill, a comment pill and an "award"
badge. That reads to a platform as imported/reposted content AND as a static
image, which is exactly the two things that policy names. Worse, once the
stories are forged rather than fetched, those engagement numbers describe
nothing at all.

This card carries only what we can stand behind:
  - the channel wordmark,
  - the story title,
  - a thin accent track that video_compile.py fills as the title is narrated
    (so the card MOVES for its whole life instead of sitting pixel-identical).

No subreddit, no username, no vote/comment/award/share pills, no counters.

How the accent track fills
--------------------------
This file writes THREE images, because of what ffmpeg can and cannot animate:
on this build `drawbox` and `crop` evaluate their geometry once and never
again, while `overlay` re-evaluates x/y every frame (that is already what makes
the card drift and swipe away). So the bar is built out of overlays:

  hook_card.png          the panel, with a TRANSPARENT hole where the track is
  hook_card_track.png    the empty groove, drawn UNDER the card
  hook_card_fill.png     the accent fill, also UNDER the card

The fill starts entirely off to the left of the hole and slides right across the
hold, so the card's hole is what clips it into a bar that grows. Both strips
move with the card, so the drift carries them along.

Geometry lands in a JSON sidecar next to the PNG (`<out_path>.json`) so the
renderer can place all of it without duplicating these numbers in
video_compile.py. A card with no sidecar (the old reddit frame) renders with no
bar at all.

Usage (CLI):
    python generate_hook_frame.py --title "..." --out hook_card.png
"""

import argparse
import json
import os

from PIL import Image, ImageDraw

from generate_reddit_frame import _font, _balanced_wrap

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
# The panel is OPAQUE. Two reasons: the accent fill spends most of the hold
# hanging to the LEFT of the groove, where only the panel can hide it (at 236 it
# let 7.5% of the fill bleed through as a warm tint), and an opaque panel cannot
# show a seam against the strips underneath it.
NEAR_BLACK = (14, 15, 17, 255)      # panel
TITLE_WHITE = (247, 247, 248)
# The groove is its own image now, so it is painted as one nearly-opaque colour
# instead of a translucent white over the panel: it sits UNDER the card, and a
# translucent strip there would show the gameplay through it.
TRACK_FILL = (54, 54, 56, 240)
ACCENT = (255, 178, 44)             # channel amber — also the bar's fill colour
STRIP_PAD = 2                       # overhang that hides any 1px scale seam
BRAND = "STORY LAB"

HOOK_LAYOUT = {
    "card_w": 2190,        # same canvas width as the reddit frame so the
                           # renderer's scale-to-output maths is unchanged
    # The panel runs the FULL canvas width, with the old 110px inset folded into
    # the content padding so the wordmark, title and track keep their positions.
    # This is what makes the fill work: it slides in from the left, and the only
    # thing hiding it before it arrives is the panel - a panel that stopped 110px
    # short of the frame edge left the fill visibly peeking out beside the card.
    "card_inset": 0,       # panel edge inset from the canvas
    "margin_x": 172,       # content padding inside the panel (was 110 + 62)
    "top_margin": 130,     # transparent space above the panel
    "bottom_margin": 96,
    "panel_radius": 26,
    "brand_top": 56,       # brand row, from the panel's top edge
    "brand_font": 54,
    "brand_track": 6,      # letterspacing for the wordmark
    "gap_brand_title": 52,
    "title_font_max": 88,
    "title_font_min": 58,
    "title_line_factor": 1.30,
    "title_max_lines": 5,
    "gap_title_bar": 58,
    "bar_h": 14,
    "bar_radius": 7,
    "bar_bottom_pad": 54,  # panel padding below the track
}


def _letterspaced(draw, xy, text, font, fill, track):
    """Draw text with manual letterspacing (PIL has no tracking setting)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + track
    return x


def generate_hook_frame(title, out_path, brand=BRAND, accent=ACCENT, layout=None):
    """Render the hook card PNG + its geometry sidecar. Returns the geometry.

    title:    the story title as it is spoken at the top of the video
    out_path: where the PNG goes (the sidecar lands beside it as .json)
    brand:    the wordmark shown above the title
    accent:   (r, g, b) of the track fill — the renderer draws it, so the
              colour is recorded in the sidecar rather than hardcoded there
    layout:   geometry override, for previewing variants side by side
    """
    L = dict(HOOK_LAYOUT)
    if layout:
        L.update(layout)

    card_w = L["card_w"]
    inset = L["card_inset"]
    pad = L["margin_x"]
    content_x = inset + pad
    content_w = card_w - 2 * inset - 2 * pad

    # A throwaway draw handle: wrapping needs a textlength measurement.
    measure = ImageDraw.Draw(Image.new("RGBA", (8, 8)))

    # --- Title: shrink until it fits the allowed line count ---
    size = L["title_font_max"]
    font = _font(size, bold=True)
    lines = _balanced_wrap(title, font, content_w, measure)
    while len(lines) > L["title_max_lines"] and size - 4 >= L["title_font_min"]:
        size -= 4
        font = _font(size, bold=True)
        lines = _balanced_wrap(title, font, content_w, measure)
    if not lines:
        lines = [title]
    if len(lines) > L["title_max_lines"]:
        # Even the smallest font cannot hold it: hard-truncate rather than let
        # the panel grow past the space the renderer leaves for it.
        lines = lines[:L["title_max_lines"]]
        lines[-1] = lines[-1].rstrip(" ,;:") + "…"
    line_h = round(size * L["title_line_factor"])
    title_h = len(lines) * line_h

    # --- Vertical stack inside the panel ---
    brand_h = round(L["brand_font"] * 1.25)
    title_top = L["brand_top"] + brand_h + L["gap_brand_title"]
    bar_top = title_top + title_h + L["gap_title_bar"]
    card_h = bar_top - 0 + L["bar_h"] + L["bar_bottom_pad"]

    canvas_h = card_h + L["top_margin"] + L["bottom_margin"]
    panel_top = L["top_margin"]
    panel_left = inset
    panel_right = card_w - inset

    img = Image.new("RGBA", (card_w, canvas_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([panel_left, panel_top, panel_right, panel_top + card_h],
                        radius=L["panel_radius"], fill=NEAR_BLACK)

    # --- Brand row ---
    dot_r = 9
    dot_cx = content_x + dot_r
    dot_cy = panel_top + L["brand_top"] + brand_h // 2
    d.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
              fill=accent + (255,))
    _letterspaced(d, (content_x + dot_r * 2 + 22, panel_top + L["brand_top"]),
                  brand, _font(L["brand_font"], bold=True), accent + (255,),
                  L["brand_track"])

    # --- Title ---
    # title_top is measured from the PANEL's top edge (like brand_top); the
    # canvas offset has to be added, or the title is drawn 130px high and lands
    # on top of the wordmark.
    ty = panel_top + title_top
    for line in lines:
        d.text((content_x, ty), line, font=font, fill=TITLE_WHITE)
        ty += line_h

    # --- Accent track: a HOLE in the panel the bar shows through ---
    # ImageDraw SETS pixel values on an RGBA image (it does not composite), so
    # filling with alpha 0 genuinely punches the shape out of the panel.
    bar_x = content_x
    bar_y = panel_top + bar_top
    bar_w = content_w
    bar_h = L["bar_h"]
    d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                        radius=L["bar_radius"], fill=(0, 0, 0, 0))

    if out_path is None:
        raise ValueError("out_path is required")
    img.save(out_path)

    # --- The two strips that live under the hole ---
    # Slightly larger than the hole, so a sub-pixel difference between the
    # scaled card and the scaled strips can never open a hairline seam.
    strip_w = bar_w + 2 * STRIP_PAD
    strip_h = bar_h + 2 * STRIP_PAD
    base = os.path.splitext(out_path)[0]
    track_path = base + "_track.png"
    fill_path = base + "_fill.png"
    Image.new("RGBA", (strip_w, strip_h), TRACK_FILL).save(track_path)
    Image.new("RGBA", (strip_w, strip_h), accent + (242,)).save(fill_path)

    # Recorded in CANVAS coordinates: the renderer scales these straight onto
    # the frame, so a panel-relative value here would put the strips ~85px off.
    geometry = {
        "canvas_w": card_w,
        "canvas_h": canvas_h,
        "bar": {"x": bar_x, "y": bar_y, "w": bar_w, "h": bar_h},
        "strip": {"x": bar_x - STRIP_PAD, "y": bar_y - STRIP_PAD,
                  "w": strip_w, "h": strip_h},
        "track_file": os.path.basename(track_path),
        "fill_file": os.path.basename(fill_path),
        "accent": "#%02X%02X%02X" % (accent[0], accent[1], accent[2]),
        "brand": brand,
        "title_lines": len(lines),
        "title_font": size,
    }
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(geometry, fh, indent=2)
    return geometry


def main():
    ap = argparse.ArgumentParser(description="Render the animated hook card")
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    g = generate_hook_frame(args.title, args.out)
    print(f"wrote {args.out} ({g['canvas_w']}x{g['canvas_h']}, "
          f"bar {g['bar']['w']}x{g['bar']['h']} @ {g['bar']['x']},{g['bar']['y']})")


if __name__ == "__main__":
    main()
