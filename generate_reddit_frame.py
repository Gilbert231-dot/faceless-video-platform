"""
generate_reddit_frame.py — render the reddit post card PNG used as the
animated intro of every generated video (and its thumbnail).

Design follows the Postfully reference frame the channel uses:
  - the profile avatar, subreddit and username sit in the TOP-LEFT corner
  - the story title is the big bold body text, wrapped into balanced lines
  - a pill bar (votes / comments / award / share) closes the card

Everything is drawn with PIL primitives + a TTF font so it renders
identically on the GitHub Actions runner (Linux) and Windows. The layout is
fully configurable through LAYOUT (a dict of geometry), and generate_frame
accepts a `layout` override so variants can be previewed side by side.

Usage (CLI):
    python generate_reddit_frame.py --title "..." --subreddit AITAH \
        --username StoryLab --score 12500 --out frame.png
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Colors (from the Postfully reddit template)
# ---------------------------------------------------------------------------
CARD_WHITE = (255, 255, 255)
TEXT_DARK = (26, 26, 27)
TEXT_GRAY = (120, 124, 126)
SNOO_ORANGE = (255, 69, 0)
DOWNVOTE_BLUE = (88, 89, 255)
VERIFIED_BLUE = (0, 121, 211)
PILL_BG = (242, 242, 242)
PILL_ICON = (72, 72, 74)

# ---------------------------------------------------------------------------
# Layout — same resolution as the Postfully download (2190 wide).
# All sizes live here so generate_frame can take a variant override.
# ---------------------------------------------------------------------------
LAYOUT = {
    "card_w": 2190,          # full card width
    "card_inset": 110,       # how far the card edge sits from the PNG edge (pulled in from left/right)
    "margin_x": 62,          # CONTENT padding measured INSIDE the card's left edge
    "top_margin": 130,       # transparent space above the card (like the template)
    "bottom_margin": 130,    # transparent space below the card
    "avatar_r": 66,          # avatar radius
    # Header block (pushed toward the top-left corner, like the reference)
    "header_top": 50,        # y from card top where the avatar/subreddit starts
    "sub_font": 64,          # subreddit name font
    "sub_y": 12,             # subreddit text offset below header_top
    "user_font": 52,         # username font
    "user_y": 84,            # username text offset below header_top
    # Title body
    "body_top": 300,         # y from card top where the title starts
    "body_font": 72,         # base title font size
    "body_font_max": 84,     # short titles grow up to this
    "body_font_min": 54,     # long titles shrink down to this
    "line_h_factor": 1.42,   # line height = font * factor
    "max_body_lines": 4,     # long titles shrink until they fit this many lines
    # Footer pill bar
    "footer_gap": 68,        # gap between body and pill bar
    "pill_h": 110,           # pill bar height
}

AVATAR_PATH = "assets/storylab_avatar.png"  # the channel's profile picture

# Font preference: Segoe UI Bold is round and modern (matches the reference);
# Linux runners fall back to DejaVu/Liberation. Arial stays as a last resort.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
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
    """Greedy word-wrap (kept for small headers/labels)."""
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


def _balanced_wrap(text, font, max_width, draw):
    """Wrap into BALANCED lines — the raggedness is minimized (minimum
    squared-slack dynamic programming) so every line is a similar width and
    long titles still look tidy instead of one short line + one huge line.
    """
    words = text.split()
    if not words:
        return [text]
    n = len(words)

    # width[i] = pixel width of words[i] (word + trailing space)
    width = [draw.textlength(w + " ", font=font) for w in words]
    width[-1] -= draw.textlength(" ", font=font)  # no trailing space on last

    # cost[i][j] = slack^2 of a line holding words i..j (j exclusive)
    # Break points that fall on natural punctuation get a bonus (lower cost).
    def line_cost(i, j):
        wsum = sum(width[i:j])
        if wsum <= max_width:
            base = (max_width - wsum) ** 2
            # prefer breaking right after a comma, dash or conjunction
            if words[j - 1][-1] in ",;:—" or words[j - 1].lower() in (
                    "and", "but", "or", "so", "yet", "when", "while", "because", "then"):
                base = int(base * 0.65)
            return base
        return float("inf")

    # dp[i] = min total cost wrapping words[i:]
    dp = [float("inf")] * (n + 1)
    nxt = [0] * (n + 1)
    dp[n] = 0
    for i in range(n - 1, -1, -1):
        best, best_k = float("inf"), n
        for j in range(i + 1, n + 1):
            c = line_cost(i, j)
            if c == float("inf"):
                break  # any longer line is even wider
            total = c + dp[j]
            if total < best:
                best, best_k = total, j
        dp[i], nxt[i] = best, best_k

    lines, i = [], 0
    while i < n:
        j = nxt[i] if nxt[i] > i else i + 1
        lines.append(" ".join(words[i:j]))
        i = j
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
        d = ImageDraw.Draw(img)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SNOO_ORANGE)
        return
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
    draw.ellipse([cx - s, cy - s * 0.55, cx + s, cy + s * 0.55], outline=PILL_ICON, width=max(3, int(s // 4)))
    draw.polygon([(cx + s * 0.45, cy + s * 0.35), (cx + s * 0.95, cy + s * 0.85), (cx + s * 0.1, cy + s * 0.5)],
                 fill=PILL_ICON)


def _draw_icon_award(draw, cx, cy, s):
    draw.ellipse([cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7], outline=PILL_ICON, width=max(3, int(s // 4)))
    draw.line([(cx - s * 0.45, cy + s * 0.45), (cx - s * 0.7, cy + s * 1.2)], fill=PILL_ICON, width=max(3, int(s // 4)))
    draw.line([(cx + s * 0.45, cy + s * 0.45), (cx + s * 0.7, cy + s * 1.2)], fill=PILL_ICON, width=max(3, int(s // 4)))


def _draw_icon_share(draw, cx, cy, s):
    draw.line([(cx - s, cy), (cx + s * 0.8, cy)], fill=PILL_ICON, width=max(3, int(s // 4)))
    draw.polygon([(cx + s * 0.8, cy), (cx + s * 0.2, cy - s * 0.7), (cx + s * 0.2, cy + s * 0.7)],
                 fill=PILL_ICON)


def _draw_icon_check(draw, cx, cy, s):
    draw.ellipse([cx - s, cy - s, cx + s, cy + s], fill=VERIFIED_BLUE)
    draw.line([(cx - s * 0.5, cy + s * 0.05), (cx - s * 0.05, cy + s * 0.45), (cx + s * 0.55, cy - s * 0.4)],
              fill=(255, 255, 255), width=max(3, int(s // 3)))


def _draw_badges(draw, x, cy, s):
    """Small flair badges after the username (test tube, microscope,
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
                   out_path=None, card_w=None, avatar_path=AVATAR_PATH,
                   layout=None):
    """Render the reddit post card PNG and save it to out_path.

    title:     story hook / title shown in the card body
    subreddit: e.g. 'AITAH' (displayed as 'r/AITAH' in the header)
    username:  channel name (default 'StoryLab')
    score:     real post score -> the vote pill
    comments:  comment count -> the comment pill (defaults to ~15% of score)
    card_w:    render width (default from LAYOUT, 2190 like the template)
    avatar_path: the channel profile picture used as the circular avatar
    layout:    geometry dict override (see LAYOUT) — used for previewing
               variants side by side
    """
    L = dict(LAYOUT)
    if layout:
        L.update(layout)
    if card_w:
        L["card_w"] = card_w
    card_w = L["card_w"]
    INS = L.get("card_inset", L["margin_x"])   # card edge inset (small = big card)
    M = L["margin_x"]                            # content padding INSIDE the card edge

    subreddit = (subreddit or "AITAH").strip()
    if not subreddit.lower().startswith("r/"):
        subreddit = "r/" + subreddit
    if out_path is None:
        out_path = f"reddit_frame_{int(time.time())}.png"

    draw = ImageDraw.Draw(Image.new("RGBA", (8, 8)))

    # --- Title body: adaptive font + balanced wrap ---
    body_font_size = L["body_font_max"]
    body_font = _font(body_font_size, bold=True)
    card_right = card_w - L.get("card_inset", M)
    # Title starts at the card's left edge + content padding and runs to just
    # inside the card's right edge.
    CX = INS + M
    max_body_w = max(card_right - CX - 40, 200)
    body_lines = _balanced_wrap(title, body_font, max_body_w, draw)
    while len(body_lines) > L["max_body_lines"] and body_font_size - 4 >= L["body_font_min"]:
        body_font_size -= 4
        body_font = _font(body_font_size, bold=True)
        body_lines = _balanced_wrap(title, body_font, max_body_w, draw)
    if not body_lines:
        body_lines = [title]
    line_h = round(body_font_size * L["line_h_factor"])
    body_h = len(body_lines) * line_h

    # --- Card geometry ---
    # The card itself spans nearly the FULL width (only a small inset), so it
    # looks BIG on screen. The content (avatar/title/pills) starts at the
    # right-shifted margin_x. This split is what removes the "card inside a
    # bigger frame" look — the card IS the frame, edge to edge.
    header_h = 130                      # avatar + two header text lines
    footer_y = L["body_top"] + body_h + L["footer_gap"]
    card_h = footer_y + L["pill_h"] + 54
    canvas_h = card_h + L["top_margin"] + L["bottom_margin"]
    card_top = L["top_margin"]
    card_left = INS
    card_right = card_w - INS

    img = Image.new("RGBA", (card_w, canvas_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- Card body ---
    # NOTE: no drop shadow here on purpose — a blurred shadow slightly larger
    # than the card reads as a "second frame" around the card (the user saw
    # it as a bigger frame around the actual frame). The card is the only
    # frame, and it looks bigger without the shadow envelope.
    d.rounded_rectangle([card_left, card_top, card_right, card_top + card_h],
                        radius=26, fill=CARD_WHITE)

    # --- Header: profile avatar + subreddit + username + badges ---
    ax = CX + L["avatar_r"]
    ay = card_top + L["header_top"] + L["avatar_r"]
    _paste_avatar(img, avatar_path, ax, ay, L["avatar_r"])

    text_x = CX + L["avatar_r"] * 2 + 30
    d.text((text_x, card_top + L["header_top"] + L["sub_y"]), subreddit,
           font=_font(L["sub_font"], bold=True), fill=TEXT_DARK)
    d.text((text_x, card_top + L["header_top"] + L["user_y"]), username or "StoryLab",
           font=_font(L["user_font"], bold=False), fill=TEXT_GRAY)
    if username:
        un_len = d.textlength(username or "StoryLab", font=_font(L["user_font"], bold=False))
        _draw_badges(d, int(text_x + un_len + 30),
                     card_top + L["header_top"] + L["user_y"] + L["user_font"] // 2, 18)

    # --- Body: the story title ---
    ty = card_top + L["body_top"]
    for line in body_lines:
        d.text((CX, ty), line, font=body_font, fill=TEXT_DARK)
        ty += line_h

    # --- Footer pill bar ---
    py = card_top + footer_y + 8
    ph = L["pill_h"] - 8
    icon_s = 22

    # vote pill
    vp_w = 400
    _rounded_rect(d, [CX, py, CX + vp_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_upvote(d, CX + 48, py + ph // 2, icon_s)
    vote_txt = _format_count(score) if score else "10K+"
    d.text((CX + 88, py + (ph - 50) // 2), vote_txt,
           font=_font(48, bold=True), fill=TEXT_DARK)
    _draw_icon_downvote(d, CX + vp_w - 48, py + ph // 2, icon_s)

    # comment pill
    cx0 = CX + vp_w + 24
    cmt = comments if comments is not None else (max(1, round(score * 0.15)) if score else 0)
    cmt_txt = _format_count(cmt) if cmt else "1.2K"
    cmt_w = 130 + d.textlength(cmt_txt, font=_font(48, bold=True))
    _rounded_rect(d, [cx0, py, cx0 + cmt_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_comment(d, cx0 + 44, py + ph // 2, icon_s)
    d.text((cx0 + 82, py + (ph - 50) // 2), cmt_txt, font=_font(48, bold=True), fill=TEXT_DARK)

    # award pill
    aw_w = 104
    aw_x = cx0 + cmt_w + 24
    _rounded_rect(d, [aw_x, py, aw_x + aw_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_award(d, aw_x + aw_w // 2, py + ph // 2, icon_s)

    # share pill
    sh_txt = "Share"
    sh_w = 130 + d.textlength(sh_txt, font=_font(46, bold=True))
    sh_x = aw_x + aw_w + 24
    _rounded_rect(d, [sh_x, py, sh_x + sh_w, py + ph], ph // 2, PILL_BG)
    _draw_icon_share(d, sh_x + 46, py + ph // 2, icon_s)
    d.text((sh_x + 84, py + (ph - 48) // 2), sh_txt, font=_font(46, bold=True), fill=PILL_ICON)

    img.save(out_path, "PNG")
    print(f"   🖼️ Reddit frame generated: {out_path} "
          f"({card_w}x{canvas_h}, title {len(body_lines)} line(s) @ {body_font_size}px, "
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
