"""
One-time TikTok authorization script — run on YOUR computer (not in Actions).

What it does:
  1. Reads TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET from your environment or
     a .env file in the repo.
  2. Starts a tiny local web server on http://localhost:8080/callback
  3. Opens your browser to TikTok's authorization page.
  4. You log in to TikTok and approve the app.
  5. The script captures the code, exchanges it for tokens and prints the
     secret values to paste into GitHub.

Prerequisites (do these in the TikTok developer portal FIRST — see
TIKTOK_SETUP.md for the full click-by-click list):
  - Register the app as a Desktop App.
  - Add the Content Posting API product with scopes user.info.basic,
    video.publish.
  - Register the redirect URI http://localhost:8080/callback
    (or http://localhost:*/callback — wildcard ports are allowed).
  - Enable the "Direct Post" configuration for the Content Posting API.
  - The video.publish scope must be APPROVED by TikTok review before posting
    actually works (authorization itself works before approval).

Usage:
    python tiktok_setup.py
    # or: TIKTOK_CLIENT_KEY=... TIKTOK_CLIENT_SECRET=... python tiktok_setup.py
"""

import hashlib
import http.server
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
REDIRECT_PORT = int(os.getenv("TIKTOK_REDIRECT_PORT", "8080"))
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "user.info.basic,video.publish"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# PKCE — TikTok requires S256 with HEX-encoded SHA256 of the code verifier.
_VERIFIER_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
code_verifier = "".join(secrets.choice(_VERIFIER_CHARS) for _ in range(64))
code_challenge = hashlib.sha256(code_verifier.encode("utf-8")).hexdigest()
state = secrets.token_urlsafe(32)

_result = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if params.get("state", [""])[0] != state:
            self._page(400, "State mismatch — restart the script and try again.")
            return
        if "error" in params:
            _result["error"] = (
                params["error"][0]
                + " "
                + params.get("error_description", [""])[0]
            )
            self._page(200, "Authorization failed. You can close this tab.")
            return
        code = params.get("code", [""])[0]
        if not code:
            self._page(400, "No code received. You can close this tab.")
            return
        _result["code"] = code
        self._page(
            200,
            "<h2>✅ Authorized! You can close this tab and go back to the terminal.</h2>",
        )

    def _page(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body style='font-family:sans-serif'>{body}</body></html>".encode("utf-8"))

    def log_message(self, *args):  # silence request logging
        pass


def main():
    if not CLIENT_KEY or not CLIENT_SECRET:
        print("❌ TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set")
        print("   (export them, or put them in a .env file in the repo).")
        sys.exit(1)

    with socketserver.TCPServer(("127.0.0.1", REDIRECT_PORT), CallbackHandler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        auth_url = (
            "https://www.tiktok.com/v2/auth/authorize/?"
            + urllib.parse.urlencode(
                {
                    "client_key": CLIENT_KEY,
                    "response_type": "code",
                    "scope": SCOPES,
                    "redirect_uri": REDIRECT_URI,
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        print("👉 Opening your browser…")
        print("   If it doesn't open, copy this URL into your browser:\n")
        print("   " + auth_url + "\n")
        webbrowser.open(auth_url)

        while "code" not in _result and "error" not in _result:
            time.sleep(0.5)
        httpd.shutdown()

    if "error" in _result:
        print("❌ Authorization failed:", _result["error"])
        sys.exit(1)

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": _result["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        timeout=60,
    )
    payload = resp.json()
    if resp.status_code != 200 or payload.get("error"):
        print("❌ Token exchange failed:", payload)
        sys.exit(1)

    print("\n✅ Authorization complete!\n")
    print("Now add these THREE values as GitHub repository secrets:\n")
    print("   Settings → Secrets and variables → Actions → New repository secret\n")
    print("   1. Name: TIKTOK_CLIENT_KEY")
    print(f"      Value: {CLIENT_KEY}")
    print("")
    print("   2. Name: TIKTOK_CLIENT_SECRET")
    print(f"      Value: {CLIENT_SECRET}")
    print("")
    print("   3. Name: TIKTOK_REFRESH_TOKEN")
    print(f"      Value: {payload['refresh_token']}")
    print("")
    print("(The access token itself lasts only 24 h and is refreshed")
    print(" automatically each run — you do NOT need to save it.)")
    print("")
    print("Then flip the TIKTOK_ENABLED file in the repo to 'true' and")
    print("dispatch a workflow run. Full steps: TIKTOK_SETUP.md")


if __name__ == "__main__":
    main()
