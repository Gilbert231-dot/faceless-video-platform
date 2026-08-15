"""generate_reddit_frame.py

Draws the reddit post card used as the animated intro frame of every video,
matching the Postfully reddit-post template design the user chose:

    [snoo avatar]  AITAH            <- subreddit (bold)
                   StoryLab [badges] <- channel username + flair badges
    This is where the story title goes...
    [▲ 100K+ ▼] [💬 45K+] [award] [↗ Share]

The card is drawn from scratch with PIL (no third-party site or API): every
video gets a fresh card with the REAL story data (subreddit, title, score,
comment count), the card grows to fit the title, and it renders identically
on the GitHub Actions runner and locally on Windows.

The title hold-time / fade / slide animation is applied later by
video_compile.py when the card is overlaid on segment 0.
"""
import os
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# --- Brand colors (match the Postfully template / Reddit) ---
SNOO_ORANGE = (255, 86, 0)      # reddit snoo / upvote orange
TEXT_DARK = (26, 26, 27)        # post text
TEXT_GRAY = (120, 124, 126)     # username
CARD_WHITE = (255, 255, 255)
PILL_BG = (234, 237, 239)       # action pills
PILL_ICON = (60, 64, 68)
DOWNVOTE_BLUE = (88, 89, 255)
VERIFIED_BLUE = (0, 121, 211)

# --- Layout (drawn at the same resolution as the Postfully download) ---
CARD_W = 2190                   # full card width
MARGIN_X = 64                   # card padding left/right
TOP_MARGIN = 144                # transparent space above the card (like the template)
BOTTOM_MARGIN = 144             # transparent space below the card

HEADER_TOP = 210                # y where the avatar/subreddit header starts
BODY_TOP = 352                  # y where the title body starts
BODY_FONT = 58                  # title font size
BODY_FONT_MAX = 68              # title font grows this big for short titles
BODY_FONT_MIN = 44              # title font shrinks this small for long titles
BODY_LINE_H = 82                # title line height at BODY_FONT
MAX_BODY_LINES = 4              # long titles shrink until they fit this many lines
FOOTER_GAP = 56                 # gap between body and the pill bar
PILL_H = 92                     # pill bar height

AVATAR_R = 48                   # avatar radius
AVATAR_PATH = "assets/storylab_avatar.png"  # the channel's profile picture

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]
_font_cache = {}


def _font(size, bold=True):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    found = None
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            found = p
            break
    if found is None:
        raise RuntimeError("No TTF font found for the reddit frame - install DejaVu fonts")
    f = ImageFont.truetype(found, size)
    _font_cache[key] = f
    return f


def _rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _format_count(n):
    """1000000 -> '1M+', 123000 -> '123K+', 4321 -> '4.3K+', 42 -> '42'.

    The '+' ("at least this much") matches the Postfully template style.
    """
    n = max(int(n or 0), 0)
    if n >= 1_000_000:
        v = n / 1_000_000
        return (f"{v:.1f}M+" if v < 10 and v != round(v) else f"{round(v)}M+")
    if n >= 1_000:
        v = n / 1_000
        return (f"{v:.1f}K+" if v < 100 and v != round(v) else f"{round(v)}K+")
    return str(n)


def _wrap_lines(text, font, max_width, draw):
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Icons (drawn with PIL primitives so they render identically on every OS)
# ---------------------------------------------------------------------------
def _paste_avatar(img, avatar_path, cx, cy, r):
    """Paste the channel's profile picture as a circular avatar."""
    try:
        av = Image.open(avatar_path).convert("RGBA")
    except Exception:
        av = None
    if av is None:
        # fallback: plain orange circle with the subreddit initial
        d = ImageDraw.Draw(img)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SNOO_ORANGE)
        return
    # cover the circle with a square crop of the image
    side = 2 * r
    av = av.resize((side, side), Image.LANCZOS)
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, side - 1, side - 1], fill=255)
    img.paste(av, (cx - r, cy - r), mask)


def _draw_icon_upvote(draw, cx, cy, s):
    draw.polygon([(cx - s, cy + s * 0.5), (cx + s, cy + s * 0.5), (cx, cy - s * 0.6)], fill=SNOO_ORANGE)


def _draw_icon_downvote(draw, cx, cy, s):
    draw.polygon([(cx - s, cy - s * 0.5), (cx + s, cy - s * 0.5), (cx, cy + s * 0.6)], fill=DOWNVOTE_BLUE)


def _draw_icon_comment(draw, cx, cy, s):
    draw.ellipse([cx - s, cy - s * 0.55, cx + s, cy + s * 0.55], outline=PILL_ICON, width=max(3, s // 4))
    draw.polygon([(cx + s * 0.45, cy + s * 0.35), (cx + s * 0.95, cy + s * 0.85), (cx + s * 0.1, cy + s * 0.5)],
                 fill=PILL_ICON)


def _draw_icon_award(draw, cx, cy, s):
    # medal: circle + ribbon tails
    draw.ellipse([cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7], outline=PILL_ICON, width=max(3, s // 4))
    draw.line([(cx - s * 0.45, cy + s * 0.45), (cx - s * 0.7, cy + s * 1.2)], fill=PILL_ICON, width=max(3, int(s // 4)))
    draw.line([(cx + s * 0.45, cy + s * 0.45), (cx + s * 0.7, cy + s * 1.2)], fill=PILL_ICON, width=max(3, int(s // 4)))


def _draw_icon_share(draw, cx, cy, s):
    draw.line([(cx - s, cy), (cx + s * 0.8, cy)], fill=PILL_ICON, width=max(3, int(s // 4)))
    draw.polygon([(cx + s * 0.8, cy), (cx + s * 0.2, cy - s * 0.7), (cx + s * 0.2, cy + s * 0.7)],
                 fill=PILL_ICON)


def _draw_icon_check(draw, cx, cy, s):
    # verified badge: blue circle + white check
    draw.ellipse([cx - s, cy - s, cx + s, cy + s], fill=VERIFIED_BLUE)
    draw.line([(cx - s * 0.5, cy + s * 0.05), (cx - s * 0.05, cy + s * 0.45), (cx + s * 0.55, cy - s * 0.4)],
              fill=(255, 255, 255), width=max(3, int(s // 3)))


def _draw_badges(draw, x, cy, s):
    """The small flair badges after the username (test tube, microscope,
    lab coat, verified) — simplified shapes at badge size."""
    # 1. test tube (green)
    draw.ellipse([x, cy - s * 0.5, x + s, cy + s * 0.5], fill=(46, 160, 67))
    draw.rectangle([x + s * 0.35, cy - s * 0.9, x + s * 0.65, cy - s * 0.2], fill=(46, 160, 67))
    # 2. microscope (blue)
    x2 = x + s + 14
    draw.rectangle([x2, cy - s * 0.6, x2 + s * 0.7, cy + s * 0.6], fill=(0, 120, 212))
    draw.ellipse([x2 + s * 0.15, cy - s * 0.95, x2 + s * 0.55, cy - s * 0.55], fill=(0, 120, 212))
    # 3. lab coat / shield (purple)
    x3 = x2 + s + 14
    draw.polygon([(x3, cy + s * 0.6), (x3 + s * 0.5, cy - s * 0.6), (x3 + s, cy + s * 0.6)], fill=(147, 101, 184))
    # 4. verified check
    _draw_icon_check(draw, x3 + s + 14 + s * 0.5, cy, s * 0.75)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def generate_frame(title, subreddit, username="StoryLab", score=0, comments=None,
                   out_path=None, card_w=CARD_W, avatar_path=AVATAR_PATH):
    """Render the reddit post card PNG and save it to out_path.

    title:    story hook / title shown in the card body
    subreddit: e.g. 'AITAH' (displayed as 'r/AITAH' in the header)
    username:  channel name (default 'StoryLab')
    score:     real post score -> the vote pill
    comments:  comment count -> the comment pill (defaults to ~15% of score)
    card_w:    render width (default 2190 like the Postfully download)
    avatar_path: the channel profile picture used as the circular avatar
    """
    subreddit = (subreddit or "AITAH").strip()
    if not subreddit.lower().startswith("r/"):
        subreddit = "r/" + subreddit
    if out_path is None:
        out_path = f"reddit_frame_{int(time.time())}.png"

    draw = ImageDraw.Draw(Image.new("RGBA", (8, 8)))

    # --- Title body: wrap first so we can size the card ---
    # Adaptive font: short titles render BIG, long titles shrink so the
    # whole title still fits the card without overflowing MAX_BODY_LINES.
    body_font_size = BODY_FONT_MAX
    body_font = _font(body_font_size, bold=True)
    max_body_w = card_w - 2 * MARGIN_X
    body_lines = _wrap_lines(title, body_font, max_body_w, draw)
    while len(body_lines) > MAX_BODY_LINES and body_font_size > BODY_FONT_MIN:
        body_font_size -= 4
        body_font = _font(body_font_size, bold=True)
        body_lines = _wrap_lines(title, body_font, max_body_w, draw)
    if not body_lines:
        body_lines = [title]
    line_h = round(BODY_LINE_H * body_font_size / BODY_FONT)  # line height scales with the font
    body_h = len(body_lines) * line_h

    # --- Card geometry ---
    header_h = 120                      # avatar + two header text lines
    footer_y = HEADER_TOP + header_h + 40 + body_h + FOOTER_GAP
    card_h = footer_y + PILL_H + 60
    canvas_h = card_h + TOP_MARGIN + BOTTOM_MARGIN
    card_top = TOP_MARGIN

    img = Image.new("RGBA", (card_w, canvas_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- Soft shadow under the card ---
    shadow = Image.new("RGBA", (card_w, canvas_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([MARGIN_X, card_top + 10, card_w - MARGIN_X, card_top + card_h + 10],
                         radius=26, fill=(0, 0, 0, 46))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    # --- Card body ---
    d.rounded_rectangle([MARGIN_X, card_top, card_w - MARGIN_X, card_top + card_h],
                        radius=26, fill=CARD_WHITE)

    # --- Header: profile avatar + subreddit + username + badges ---
    ax = MARGIN_X + AVATAR_R
    ay = card_top + HEADER_TOP + 60
    _paste_avatar(img, avatar_path, ax, ay, AVATAR_R)

    text_x = MARGIN_X + AVATAR_R * 2 + 28
    d.text((text_x, card_top + HEADER_TOP), subreddit,
           font=_font(54, bold=True), fill=TEXT_DARK)
    d.text((text_x, card_top + HEADER_TOP + 66), username or "StoryLab",
           font=_font(44, bold=False), fill=TEXT_GRAY)
    if username:
        un_len = d.textlength(username or "StoryLab", font=_font(44, bold=False))
        _draw_badges(d, int(text_x + un_len + 30), card_top + HEADER_TOP + 66 + 22, 18)

    # --- Body: the story title ---
    ty = card_top + BODY_TOP
    for line in body_lines:
        d.text((MARGIN_X, ty), line, font=body_font, fill=TEXT_DARK)
        ty += line_h

    # --- Footer pill bar ---
    py = card_top + footer_y + 6
    ph = PILL_H - 8
    icon_s = 20

    # vote pill
    vp_w = 380
    _rounded_rect(d, [MARGIN_X, py, MARGIN_X + vp_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_upvote(d, MARGIN_X + 44, py + ph // 2, icon_s)
    vote_txt = _format_count(score) if score else "10K+"
    d.text((MARGIN_X + 80, py + (ph - 46) // 2), vote_txt,
           font=_font(44, bold=True), fill=TEXT_DARK)
    _draw_icon_downvote(d, MARGIN_X + vp_w - 44, py + ph // 2, icon_s)

    # comment pill
    cx0 = MARGIN_X + vp_w + 22
    cmt = comments if comments is not None else (max(1, round(score * 0.15)) if score else 0)
    cmt_txt = _format_count(cmt) if cmt else "1.2K"
    cmt_w = 120 + d.textlength(cmt_txt, font=_font(44, bold=True))
    _rounded_rect(d, [cx0, py, cx0 + cmt_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_comment(d, cx0 + 40, py + ph // 2, icon_s)
    d.text((cx0 + 74, py + (ph - 46) // 2), cmt_txt, font=_font(44, bold=True), fill=TEXT_DARK)

    # award pill
    aw_w = 96
    aw_x = cx0 + cmt_w + 22
    _rounded_rect(d, [aw_x, py, aw_x + aw_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_award(d, aw_x + aw_w // 2, py + ph // 2, icon_s)

    # share pill
    sh_txt = "Share"
    sh_w = 120 + d.textlength(sh_txt, font=_font(42, bold=True))
    sh_x = aw_x + aw_w + 22
    _rounded_rect(d, [sh_x, py, sh_x + sh_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_share(d, sh_x + 42, py + ph // 2, icon_s)
    d.text((sh_x + 76, py + (ph - 44) // 2), sh_txt, font=_font(42, bold=True), fill=PILL_ICON)

    img.save(out_path, "PNG")
    print(f"   🖼️ Reddit frame generated: {out_path} "
          f"({card_w}x{canvas_h}, title {len(body_lines)} line(s), "
          f"{subreddit}, votes {vote_txt})")
    return out_path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate a reddit post card PNG")
    ap.add_argument("--title", default="My mother-in-law crashed our honeymoon and now she wants an apology")
    ap.add_argument("--subreddit", default="AITAH")
    ap.add_argument("--username", default="StoryLab")
    ap.add_argument("--score", type=int, default=127500)
    ap.add_argument("--comments", type=int, default=None)
    ap.add_argument("--out", default="reddit_frame_test.png")
    args = ap.parse_args()
    generate_frame(args.title, args.subreddit, args.username, args.score, args.comments, args.out)
