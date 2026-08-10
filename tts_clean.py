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
     "[TEST]" -> "Test", URLs read aloud, emojis read by name, HTML entities.
  5. "I'm" -> "I am" — deterministic fix for the glottal-stop trigger that
     makes the voice insert the "ale" syllable even when the text is clean.
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
# prefix, Reddit's [removed]/[deleted] markers, etc. (generic brackets like
# "[F 23]" are left alone — the age is still useful narration).
BRACKET_LABEL_RE = re.compile(
    r"\[(?:test|removed|deleted|unavailable|content|requested|update)\]",
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
