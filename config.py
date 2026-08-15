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
# 1.08 at the first "tiny bit" request; 1.12 now at the user's "a bit" bump.
# Captions are timed to this exact value (whisper timestamps are divided by
# it) so they stay in sync no matter the value.
VOICE_SPEED = 1.12

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
        "#truestory",
        "#storynarration",
    ],
}


def platform_tags(platform, subreddit=""):
    """Return the curated tag list for one platform, plus the subreddit tag.

    YouTube tags are plain words; Facebook/TikTok tags get '#'. The subreddit
    is appended the same way ('AITAH' on YouTube, '#AITAH' on FB/TikTok).
    """
    tags = list(PLATFORM_TAGS[platform])
    if subreddit:
        subreddit = subreddit.strip()
        if platform == "youtube":
            tags.append(subreddit)
        else:
            tags.append("#" + subreddit.replace(" ", "").replace("#", ""))
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
