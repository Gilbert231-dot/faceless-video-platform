import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv is optional — lets scripts run on machines where it isn't
    # installed (values then come from real environment variables, e.g.
    # GitHub Actions secrets). Same pattern as youtube_setup.py.
    pass

# --- CONFIG ---
FAST_MODE = False  # Set to False for production quality

# ===========================
# MODE SELECTION
# ===========================

# DEBUG_MODE is now controlled by the DEBUG_MODE environment variable
# (the generate_video.yml workflow sets it from the `test_mode` input).
# Defaults to False (production) when run locally.
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ("true", "1", "yes")

# Voice speed (used in video_compile and tasks). 1.06 was the calm baseline;
# 1.08 at the first "tiny bit" request; 1.15 rendered 284 wpm and was judged too
# rushed by ear. 1.10 is the settle point: measured on the shipped audio it lands
# at ~250 wpm, the rate RANT_SHAPE_SPEC.md already writes to.
# Captions are timed to this exact value (whisper timestamps are divided by
# it) so they stay in sync no matter the value.
#
# Env-overridable (VOICE_SPEED=1.25) so one audition render can try a different
# read WITHOUT editing this file - the value has to reach the renderer, the
# footage planning and the caption timing, and the environment is the one place
# all three read from.
VOICE_SPEED = float(os.getenv("VOICE_SPEED", "1.10"))

# ===========================
# WHICH FOOTAGE CODECS RENDER DIRECTLY (one source of truth)
# ===========================
# The renderer can either decode the source straight into the final encode, or
# "stage" it first: re-encode the footage to 4K H.264 with `veryfast`, then
# encode THAT. Staging costs a whole extra lossy generation.
#
# VP9 and AV1 used to be staged, on the theory that 4K decode was too
# expensive on a 2-core runner. Measured at 4K60, every step on the same CPU
# and limited to 4 threads, normalised to 6s of footage:
#
#     decode VP9 ...............  6.0s   (1.17x realtime)
#     decode AV1 (dav1d) ....... 18.3s   (3.06x realtime)
#     the veryfast 4K x264 that staging pays  39.6s   (6.60x realtime)
#
# Staging therefore costs ~5.6x more time than decoding VP9 and ~2.2x more than
# decoding AV1 — plus a whole extra lossy generation. The exclusion was paying a
# permanent quality loss to buy a slowdown. It also hit nearly every file,
# because YouTube serves 4K gameplay as VP9, which is what these sources are.
#
# AV1's margin is the thinner of the two, so it was measured too rather than
# assumed: it still wins. Sources are VP9 in practice, so AV1 is the rarer arm.
#
# FOOTAGE_FORCE_STAGED_CODECS=vp9,av1 restores the old behaviour for a codec
# (useful as a rollback without touching code).
DIRECT_RENDER_CODECS = {"h264", "hevc", "vp9", "av1"}
FORCE_STAGED_CODECS = {c.strip().lower()
                       for c in os.getenv("FOOTAGE_FORCE_STAGED_CODECS", "").split(",")
                       if c.strip()}


def codec_needs_staging(codec):
    """True when this source codec should be normalized to H.264 before use.

    An empty/unknown codec is NOT staged. That mirrors the rule this replaces
    (`c and c not in safe_codecs`), where a failed probe has always gone direct,
    so behaviour is unchanged for every source we cannot identify. A codec we
    have never measured takes the safe route and is staged.
    """
    c = (codec or "").strip().lower()
    if not c:
        return False
    if c in FORCE_STAGED_CODECS:
        return True
    return c not in DIRECT_RENDER_CODECS


# ===========================
# NARRATOR PAUSE COMPRESSION
# ===========================
# The narrator pauses between sentences; these knobs SHORTEN those gaps
# slightly (user request: "speed up the narrator's pauses just a little").
# Applied to the RAW voiceover right after TTS, so footage duration, the
# atempo speed-up, and whisper caption timing all see the SAME compressed
# timeline and stay in sync. Pauses shorter than PAUSE_MIN_SEC are left
# untouched; longer ones keep PAUSE_KEEP_RATIO of their length (never less
# than PAUSE_MIN_KEPT_SEC, so it still reads as a pause). Leading/trailing
# silence is trimmed fully. Set PAUSE_COMPRESS=False to disable.
PAUSE_COMPRESS = True
PAUSE_MIN_SEC = 0.28        # only pauses longer than this are shortened
PAUSE_KEEP_RATIO = 0.30     # a 0.8s pause -> ~0.24s kept
PAUSE_MIN_KEPT_SEC = 0.10   # never keep less than this
PAUSE_THRESHOLD = 0.01      # |sample| below this (of full scale) counts as silence

# ===========================
# NARRATOR LOUDNESS
# ===========================
# Every narration is brought to ONE integrated loudness, whatever voice spoke
# it, so the male and female narrators match instead of drifting apart.
#
# A plain gain CANNOT do this for a cloned voice with a wide crest factor:
# the true-peak ceiling caps how far a quiet voice can be lifted. Measured on
# the female clone of Sep 2026 ("Female yappy voice"): -29.7 LUFS with a
# -3.1 dBFS peak, i.e. a 26 dB crest factor. Lifting her to the -20 target
# needed +9.7 dB but the ceiling allowed only +1.6 dB, so she landed ~13 dB
# under the male and was heard as "very low compared to the male's voice".
# So the voiceover is LIMIT-normalized (ffmpeg loudnorm: it lifts the average
# and limits the peaks together), then measured and corrected to the target.
# Set VOICE_LEVEL_NORMALIZE=False to fall back to gain-only behaviour.
VOICE_LEVEL_NORMALIZE = os.getenv("VOICE_LEVEL_NORMALIZE", "True").lower() in ("true", "1", "yes")
VOICE_LUFS_TARGET = float(os.getenv("VOICE_LUFS_TARGET", "-20.0"))  # integrated loudness
VOICE_TP_MAX = float(os.getenv("VOICE_TP_MAX", "-1.5"))            # max true peak (dBFS)
VOICE_LRA_TARGET = float(os.getenv("VOICE_LRA_TARGET", "11"))      # loudness range
# Extra offset for the female narrator, ON TOP of the target. 0.0 = identical
# level to the male (user request: "on the same level or close"). Was 6.5, which
# was set for the previous voice (Sarah) and never survived the peak clamp.
FEMALE_VOICE_BOOST_DB = float(os.getenv("FEMALE_VOICE_BOOST_DB", "0.0"))

# ===========================
# PER-PLATFORM TAGS
# ===========================

# Each platform has its own tagging conventions for the SAME content, so the
# tags are curated per platform (user request):
#   - YouTube takes PLAIN WORDS (no '#') in its tags field.
#   - Facebook hashtags live in the video description.
#   - TikTok hashtags live in the caption and are the most hashtag-heavy.
# The story's subreddit name is appended to the chosen platform's list at
# save time (see platform_tags()).
PLATFORM_TAGS = {
    "youtube": [
        "RedditStories",
        "Storytime",
        "Reddit",
        "TrueStory",
        "StoryNarration",
        "AudioStory",
        "AskReddit",
        "RedditReads",
        "StoryChannel",
        "FacelessChannel",
    ],
    "facebook": [
        "#RedditStories",
        "#StoryTime",
        "#Reddit",
        "#Storytelling",
        "#TrueStory",
        "#AudioStory",
        "#StoryNarration",
    ],
    "tiktok": [
        "#redditstories",
        "#storytime",
        "#fyp",
        "#foryou",
        "#reddit",
        "#storytimeviral",
        "#drama",
        "#redditread",
        "#viralstory",
    ],
}


def platform_tags(platform, subreddit=""):
    """Return the curated tag list for one platform, plus the subreddit tag.

    YouTube tags are plain words; Facebook/TikTok tags get '#'. The subreddit
    is appended the same way ('AITAH' on YouTube, '#AITAH' on FB/TikTok).
    
    For AITAH/AmITheJerk subreddits, both #aita and #amIthejerk are added
    (they always travel together as tags).
    """
    tags = list(PLATFORM_TAGS[platform])
    if subreddit:
        subreddit = subreddit.strip()
        subreddit_lower = subreddit.lower().replace(" ", "").replace("#", "")
        # Add AITAH-specific tags when the subreddit is AITAH or AmITheJerk
        if subreddit_lower in ("aitah", "amithejerk", "aita"):
            if platform == "youtube":
                tags.extend(["AITA", "AmITheJerk"])
            else:
                tags.extend(["#aita", "#amIthejerk"])
        if platform == "youtube":
            tags.append(subreddit)
        else:
            tags.append("#" + subreddit_lower)
    return tags
# ===========================
# CAPTIONS SETTINGS
# ===========================

# Set to True to add captions to videos (uses fal.ai API)
# Set to False to skip captions (saves money)
USE_CAPTIONS = True  # Change to False if you want to save credits

# --- API Keys ---
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
PEXELS_API_KEY = os.getenv('PEXELS_API_KEY')
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET')
REDDIT_USER_AGENT = "python:faceless-video-generator:v1.0 (by u/Gilbert_Poet4518)"

# --- Dynamic Paths (Works in Codespace, GitHub Actions, and locally) ---
# ===========================
# AUTOMATION PAUSE SWITCH
# ===========================
# The CI workflow has its own pause-check job; this is the same switch for the
# code paths that run OUTSIDE the workflow (a local run, a Celery worker), so a
# paused repo cannot quietly render through a side door. It is ON when either a
# file named AUTOMATION_PAUSED sits next to this file, or env AUTOMATION_PAUSED
# is truthy. env AUTOMATION_PAUSED=0/false resumes even with the file present.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
AUTOMATION_PAUSED_FILE = os.path.join(REPO_ROOT, "AUTOMATION_PAUSED")

_env_pause = os.getenv("AUTOMATION_PAUSED")
if _env_pause is not None:
    AUTOMATION_PAUSED = _env_pause.strip().lower() in ("1", "true", "yes", "on")
    AUTOMATION_PAUSE_SOURCE = "the AUTOMATION_PAUSED environment variable"
else:
    AUTOMATION_PAUSED = os.path.exists(AUTOMATION_PAUSED_FILE)
    AUTOMATION_PAUSE_SOURCE = f"the file {AUTOMATION_PAUSED_FILE}"


class AutomationPaused(RuntimeError):
    """Raised when video generation is attempted with the pause switch on."""


def automation_pause_reason() -> str:
    """A one-line, actionable explanation of why generation is paused."""
    if _env_pause is not None and AUTOMATION_PAUSED:
        resume = "Unset AUTOMATION_PAUSED (or set it to false) to resume."
    else:
        resume = ("Delete the AUTOMATION_PAUSED file (or set AUTOMATION_PAUSED=false) "
                  "to resume.")
    return (f"Automation is PAUSED ({AUTOMATION_PAUSE_SOURCE} is in place) - "
            f"no video is generated and no story is consumed. {resume}")


def require_automation_active() -> None:
    """Raise AutomationPaused if the kill switch is on. Call before spending."""
    if AUTOMATION_PAUSED:
        raise AutomationPaused(automation_pause_reason())


if AUTOMATION_PAUSED:
    print("\u23f8\ufe0f " + automation_pause_reason())

# ===========================
# TEXT TO SPEECH ENGINE
# ===========================
# "chatterbox" is the free local engine (MIT licence, code AND weights): no account,
# no key, no per-character bill, and it clones a voice from a short reference clip.
# "elevenlabs" is the paid API the pipeline used before; kept as a switch so a voice
# can still be auditioned there, but nothing needs it and an unset ELEVENLABS_API_KEY
# no longer stops a run.
TTS_ENGINE = os.getenv("TTS_ENGINE", "chatterbox").strip().lower()

# cpu everywhere; "cuda" if a GPU is present (GitHub's runner has none).
TTS_DEVICE = os.getenv("TTS_DEVICE", "cpu").strip().lower()

# turbo = the small fast model (recommended on CPU); standard = the original one.
TTS_MODEL = os.getenv("TTS_MODEL", "turbo").strip().lower()

# Reference clips the local engine clones: 10-20 s of continuous speech each, in
# assets/voices/. Absolute by default (built from REPO_ROOT, not the cwd) so a step
# that changes directory cannot silently lose the voice and fall back to the model's
# own. Missing files are not fatal - the engine says so and speaks in its default.
TTS_REF_MALE = os.getenv("TTS_REF_MALE", os.path.join(REPO_ROOT, "assets", "voices", "male.wav"))
TTS_REF_FEMALE = os.getenv("TTS_REF_FEMALE", os.path.join(REPO_ROOT, "assets", "voices", "female.wav"))

# Chunk size for the local engine. The model truncates its input at its token limit,
# so a whole narration is spoken in pieces and joined; ~200 characters keeps each
# piece well inside that window and inside one breath.
TTS_CHUNK_CHARS = int(os.getenv("TTS_CHUNK_CHARS", "200"))

# --- Dynamic Paths (Works in Codespace, GitHub Actions, and locally) ---
PROJECT_ROOT = os.getcwd()  # Current working directory
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "broll_progress.json")
GAMEPLAY_LIBRARY = os.path.join(PROJECT_ROOT, "gameplay_library")

# --- Create directories if they don't exist ---
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(GAMEPLAY_LIBRARY, exist_ok=True)

# --- Optional: Fallback if API keys are missing ---
if not GROQ_API_KEY:
    print("⚠️ WARNING: GROQ_API_KEY not found in environment variables.")
if not PEXELS_API_KEY:
    print("⚠️ WARNING: PEXELS_API_KEY not found in environment variables.")
