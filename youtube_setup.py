"""
One-time YouTube OAuth authorization script — run on YOUR computer, never in
GitHub Actions.

When you need it: if Google's 2-Step Verification setup forces a password
change, existing refresh tokens are revoked and the automation's uploads
start failing with an auth error. Run this once to mint a fresh refresh
token, then paste it into GitHub — the pipeline picks it up automatically.

What it does:
  1. Reads YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET (env, .env, or prompts).
  2. Opens your browser to Google's consent screen (InstalledAppFlow, free
     localhost port — nothing to configure).
  3. You log in (with 2FA if enabled) and approve access with the Google
     account that OWNS your YouTube channel.
  4. The script exchanges the code for tokens, VERIFIES the token works by
     reading back your channel name, and prints the refresh token.

Prerequisites (Google Cloud Console → APIs & Services → Credentials):
  - An OAuth 2.0 Client ID of type "Desktop app". The same client whose
    values are already in GitHub works — nothing new to create. (Your
    YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET stay the same; only the
    refresh token changes.)
  - YouTube Data API v3 enabled (APIs & Services → Library → YouTube Data
    API v3 → Enable).
  - If your client is type "Web application" instead, create a Desktop-app
    client (or add http://localhost:* to its authorized redirect URIs).

Usage:
    python youtube_setup.py
    # or: YOUTUBE_CLIENT_ID=... YOUTUBE_CLIENT_SECRET=... python youtube_setup.py

⚠️  The repo is PUBLIC — never save the printed refresh token into any file
    inside this project. Paste it straight into GitHub:
    Settings → Secrets and variables → Actions → edit YOUTUBE_REFRESH_TOKEN.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    def load_dotenv():  # python-dotenv optional: parse .env ourselves
        try:
            with open(".env", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass
    load_dotenv()
from google_auth_oauthlib.flow import InstalledAppFlow

# Make emoji-safe output even when stdout is redirected (Windows cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv()

# youtube.upload = what the pipeline uploads with; youtube.readonly = lets us
# verify the token by reading the channel name back (harmless, and the
# uploader keeps working with its narrower scope).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _credentials_from_config(client_id, client_secret):
    """Build an InstalledAppFlow from bare client id/secret values."""
    return InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )


def verify_and_show_channel(creds):
    """Confirm the token actually works and print the channel it belongs to."""
    from googleapiclient.discovery import build

    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
        if items:
            print(
                f"✅ Verified — this refresh token belongs to the channel: "
                f"{items[0]['snippet']['title']}"
            )
            return True
        print(
            "⚠️  Token works but no channel was found on this account. "
            "Double-check you approved with the account that owns the channel."
        )
        return False
    except Exception as e:
        print(f"⚠️  Token was minted but the verification call failed: {e}")
        print("   It may still work for uploads — check the account/scope if uploads fail.")
        return False


def main():
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET not found in env or .env.")
        print("Find them at console.cloud.google.com → APIs & Services → Credentials")
        print("→ your OAuth 2.0 Client ID (they're always viewable there).\n")
        client_id = input("Client ID: ").strip()
        client_secret = input("Client Secret: ").strip()
        print()

    if not client_id or not client_secret:
        print("❌ Both values are required. Exiting.")
        sys.exit(1)

    flow = _credentials_from_config(client_id, client_secret)

    print("👉 Opening your browser to Google's consent screen…")
    print("   Log in with the account that OWNS your YouTube channel and click Allow.")
    print("   (If your browser doesn't open, copy the printed URL into it.)\n")

    try:
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception as e:
        print(f"\n❌ Authorization failed: {e}")
        print("   Tip: your OAuth client must be type \"Desktop app\" (see the")
        print("   docstring at the top of this file).")
        sys.exit(1)

    print("\n✅ Authorization complete!\n")

    verify_and_show_channel(creds)

    print("\nNow update the GitHub secret — ONLY the refresh token changed:\n")
    print("   Settings → Secrets and variables → Actions → pencil icon next to")
    print("   YOUTUBE_REFRESH_TOKEN → paste the new value → Update secret\n")
    print("   Name:  YOUTUBE_REFRESH_TOKEN")
    print(f"   Value: {creds.refresh_token}\n")
    print("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET stay exactly as they are.")
    print("The next scheduled upload uses the new token automatically — no other")
    print("changes needed.\n")
    print("⚠️  This repo is PUBLIC — don't paste the value above into any file in")
    print("    the project. GitHub Secrets is the only place it should live.")


if __name__ == "__main__":
    main()
