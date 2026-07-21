import os
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
FAST_MODE = True  # Set to False for production quality

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