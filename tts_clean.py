"""
tts_clean.py — every text fix that runs BEFORE the narrator's script hits
ElevenLabs. Single source of truth, applied in two places (belt and suspenders):

  * clean_script_for_tts() in tasks.py (right after the script is assembled)
  * generate_voiceover() in voiceover.py (the last line before synthesis)

What it fixes (the "things the narrator should never say"):

  1. "ale" -> "" — the filler syllable the voice inserts after "I'm"
     (e.g. "I'm ale about to"). Wherever "ale" appears in the text, the
     narrator says NOTHING. Word boundaries keep real words intact:
     male / female / scale / tale are never touched.
  2. Casual contractions -> natural full phrases ("gonna" -> "going to",
     "wanna" -> "want to", ...) so the narrator speaks clearly — the
     corrections-dictionary technique, applied to the VOICE text (not just
     the captions, which is why the old version never fixed the audio).
  3. Reddit acronyms -> spoken-out meanings ("AITAH" -> "Am I the jerk",
     "ITAH" -> "I am the jerk") so anyone listening understands.
  4. Markdown & artifacts TTS reads literally: "**" -> "asterisk asterisk",
     "[TEST]" / "[FULL STORY]" title labels stripped (never spoken), URLs
     read aloud, emojis read by name, HTML entities.
  5. "I'm" -> "I am" — deterministic fix for the glottal-stop trigger that
     makes the voice insert the "ale" syllable even when the text is clean.
  6. Machine dates -> spoken dates. "02/10" is read one digit at a time
     ("zero two ten"), which the listener hears as a TIMESTAMP for a moment
     that never appears on screen; "February 10" is heard as a date. The
     forge writes its dated anchors as MM/DD, so this runs on every
     narration — reddit stories included.
"""

import re

# ---------------------------------------------------------------- 1. "ale"
# ROOT-CAUSE FIX (proven by the tts_script artifact): the story adapter
# sometimes writes the filler GLUED onto "I'm" as "I'male" — e.g.
# "I'male literally about to tell you" — so word-boundary regexes (\bale\b,
# \bI'm\b) can't see it, whisper hears one clean word (QA passes), and the
# voice pronounces it "I'm ale" (exactly the artifact the user hears). Match
# the whole glued family and replace it with "I am".
GLUED_IM_RE = re.compile(r"\bI'?ma?le+\b", re.IGNORECASE)

# Standalone filler word -> silence. Any case. "aale"/"alee" are common
# renderings of the same artifact.
ALE_RE = re.compile(r"\b(?:ale|aale|alee)\b", re.IGNORECASE)

# ------------------------------------------------- 2. Casual speech -> natural
CORRECTIONS = {
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "got to",
    "kinda": "kind of",
    "sorta": "sort of",
    "lemme": "let me",
    "dunno": "don't know",
    "outta": "out of",
    "alright": "all right",
    "y'all": "you all",
}

# ----------------------------------------------- 3. Reddit acronyms -> spoken
# Wording matches script_gen.SLANG_MAP ("jerk" instead of the harsher word —
# YouTube-safe and keeps the story PG). Also covers "ITAH", which SLANG_MAP
# missed.
ACRONYMS = {
    "AITA": "Am I the jerk",
    "AITAH": "Am I the jerk",
    "ITAH": "I am the jerk",
    "NTA": "Not the jerk",
    "YTA": "You're the jerk",
    "ESH": "Everyone sucks here",
    "NAH": "No jerks here",
    "TIFU": "Today I messed up",
    "OP": "the original poster",
    "TLDR": "Too long, didn't read",
    "IMO": "in my opinion",
    "IMHO": "in my humble opinion",
    "TBH": "to be honest",
    "IDK": "I don't know",
    "LOL": "laughing out loud",
    "OMG": "oh my god",
    "WTF": "what the heck",
}

# ----------------------------------------------- 4. Markdown / TTS artifacts
# Paired markdown -> its content. Order matters: **bold** before *italic*.
MARKDOWN_PATTERNS = [
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),     # **bold**
    (re.compile(r"\*([^*\s][^*]*?)\*"), r"\1"),  # *italic*
    (re.compile(r"\b_([^_\n]+)_\b"), r"\1"),     # _italic_ (word-wrapped only)
    (re.compile(r"~~([^~]+)~~"), r"\1"),         # ~~strike~~
    (re.compile(r"`([^`]+)`"), r"\1"),           # `code`
    (re.compile(r"^#{1,6}\s*", re.M), ""),       # # headings
    (re.compile(r"^\s*>\s?", re.M), ""),         # > blockquotes
    (re.compile(r"https?://\S+|www\.\S+"), " "),  # URLs
]

# Bracket labels the narrator must never speak: the [TEST] test-mode title
# prefix, the [FULL STORY] production-title prefix, Reddit's
# [removed]/[deleted] markers, etc. (generic brackets like "[F 23]" are
# left alone — the age is still useful narration).
BRACKET_LABEL_RE = re.compile(
    r"\[(?:test|full\s*story|fullstory|removed|deleted|unavailable|content|requested|update)\]",
    re.IGNORECASE,
)

# Emoji / pictographs / symbols TTS would otherwise read aloud by name
# ("red heart", "grinning face", ...). Strips the common blocks.
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0000FE0F\u20E3\U00002190-\U000021FF]"
)

HTML_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
}

# --------------------------------------------- 6. Machine dates -> spoken
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")

# A form that carries a YEAR is never a fraction, so it is converted
# unconditionally: 12/03/2018, 02/10/18.
SLASH_DATE_FULL_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

# A bare M/D is ambiguous, so it is only converted when it READS as a date:
# a zero-padded month (02/10), an impossible fraction (3/28), or a date cue
# word in front of it ("on 3/5"). "1/2 cup" and "3/4 of the time" stay put.
SLASH_DATE_SHORT_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
FRACTION_UNIT_RE = re.compile(
    r"\s*(?:cup|cups|tsp|tbsp|teaspoon|tablespoon|oz|ounce|ounces|lb|lbs|pound|"
    r"pounds|mile|miles|km|kg|g|gram|grams|inch|inches|foot|feet|hour|hours|"
    r"minute|minutes|second|seconds|day|days|week|weeks|month|months|year|"
    r"years|percent|%)\b",
    re.IGNORECASE,
)
DATE_CUE_RE = re.compile(
    r"(?:\b(?:on|dated|by|since|until|before|after|that|during)\s+)$",
    re.IGNORECASE,
)


def _spoken_date(month: int, day: int, year=None) -> str:
    """1928 -> 'January 28, 1928'. Returns '' when the numbers aren't a date."""
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    out = f"{_MONTHS[month - 1]} {day}"
    return f"{out}, {year}" if year else out


def _day_first(month: int, day: int):
    """12/03 is month-first, but 13/02 can only be day-first."""
    return (day, month) if month > 12 and day <= 12 else (month, day)


def _expand_year(raw: str) -> str:
    """A two-digit year is spoken as a year, not as 'eighteen'."""
    if len(raw) == 4:
        return raw
    from datetime import date
    y = int(raw)
    century = 2000 if y <= date.today().year % 100 else 1900
    return str(century + y)


def _speak_dates(text: str) -> str:
    """Rewrite machine dates as words, leaving fractions and ratios alone."""
    def full(m):
        mo, dy = _day_first(int(m.group(1)), int(m.group(2)))
        return _spoken_date(mo, dy, _expand_year(m.group(3))) or m.group(0)

    def iso(m):
        return _spoken_date(int(m.group(2)), int(m.group(3)), m.group(1)) or m.group(0)

    def short(m):
        mo, dy = int(m.group(1)), int(m.group(2))
        if FRACTION_UNIT_RE.match(text[m.end():m.end() + 12]):
            return m.group(0)                      # 1/2 cup, 3/4 oz
        cue = bool(DATE_CUE_RE.search(text[max(0, m.start() - 24):m.start()]))
        if not (m.group(1).startswith("0") or dy >= 13 or cue):
            return m.group(0)                      # ambiguous: leave it
        mo, dy = _day_first(mo, dy)
        return _spoken_date(mo, dy) or m.group(0)

    text = SLASH_DATE_FULL_RE.sub(full, text)
    text = ISO_DATE_RE.sub(iso, text)
    return SLASH_DATE_SHORT_RE.sub(short, text)


def _fix_html_entities(text: str) -> str:
    def rep(m):
        return HTML_ENTITIES.get(m.group(1), "")
    return re.sub(r"&(amp|lt|gt|quot|apos|nbsp|#\d+);", rep, text)


def clean_for_tts(text: str) -> str:
    """Normalize a narration script so the narrator speaks it naturally.

    Idempotent (safe to run twice — every replacement is word-boundary or
    pattern-based, so already-normalized text passes through unchanged).
    """
    if not text:
        return text

    # Curly quotes/apostrophes -> straight, so every \b pattern below matches
    # ("I\u2019m ale" vs "I'm ale").
    text = (
        text.replace("\u2019", "'").replace("\u2018", "'")
            .replace("\u201c", '"').replace("\u201d", '"')
    )
    text = _fix_html_entities(text)

    for pattern, repl in MARKDOWN_PATTERNS:
        text = pattern.sub(repl, text)

    text = BRACKET_LABEL_RE.sub(" ", text)   # "[TEST] Foo" -> " Foo"
    text = EMOJI_RE.sub("", text)
    # Machine dates -> words BEFORE the word-level passes, so a date can never
    # be read digit by digit ("02/10" -> "zero two ten").
    text = _speak_dates(text)
    # Any asterisk/backtick/tilde that survived the paired patterns is almost
    # certainly leftover markdown ("asterisk asterisk" must never be spoken).
    text = re.sub(r"[*`~]", "", text)

    # Glued "I'male" family -> "I am" (runs FIRST: it's the proven root cause,
    # and "I'male" contains an "ale" substring no \b rule could see).
    text = GLUED_IM_RE.sub("I am", text)

    for wrong, correct in CORRECTIONS.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", correct, text, flags=re.IGNORECASE)
    for acro, full in ACRONYMS.items():
        text = re.sub(rf"\b{re.escape(acro)}\b", full, text, flags=re.IGNORECASE)

    # The "ale" filler -> silence.
    text = ALE_RE.sub("", text)

    # Deterministic glottal-stop fix: "I'm" -> "I am" so the voice never hits
    # the trigger that makes it insert the "ale" syllable. The caption step
    # merges whisper's "I am" cues back to "I'm", so captions look unchanged.
    text = re.sub(r"\bI'm\b", "I am", text, flags=re.IGNORECASE)

    return re.sub(r"\s+", " ", text).strip()
