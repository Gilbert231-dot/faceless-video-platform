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

# --- Paths ---
PROJECT_ROOT = "/workspaces/faceless-video-platform"
PROGRESS_FILE = f"{PROJECT_ROOT}/broll_progress.json"
GAMEPLAY_LIBRARY = f"{PROJECT_ROOT}/gameplay_library"