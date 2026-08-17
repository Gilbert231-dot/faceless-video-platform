"""
One-time Facebook Page token script — run on YOUR computer, never in GitHub
Actions.

Mints the FB_PAGE_ACCESS_TOKEN the automation needs:
  1. Reads FB_APP_ID / FB_APP_SECRET (env, .env, or prompts).
  2. Starts a tiny local HTTPS web server on https://lvh.me:8765/ (a
     throwaway self-signed cert is generated into fb_local_certs/ on first
     run) and opens your browser to Facebook's consent screen.
  3. You log in with the account that OWNS your Facebook Page and approve the
     pages_show_list / pages_read_engagement / pages_manage_posts scopes.
  4. The script exchanges the code for a short-lived user token, swaps it for
     a long-lived (60-day) user token, then pulls your Page access token.

IMPORTANT: Page access tokens do NOT expire. Unlike YouTube/TikTok you do this
ONCE and the automation works indefinitely — no refresh dance. The token is
only invalidated if you change your Facebook password or remove the app from
your account (Settings -> Apps and websites).

Prerequisites (developers.facebook.com — your Meta app):
  - A Facebook Page (create one in the FB app if you don't have it yet).
  - A Meta app with the Facebook Login product added.
  - Add https://lvh.me:8765/ to: Facebook Login -> Settings -> Valid OAuth
    Redirect URIs (EXACTLY, with the trailing slash — this script uses that
    fixed port). Facebook requires an https:// URI, so it must start with
    https — the self-signed cert is only for the browser's one-time warning
    (click Advanced -> Proceed).
  - Add lvh.me to: Settings -> Basic -> App Domains (the dashboard rejects
    'localhost' because it has no top-level domain; lvh.me resolves to
    127.0.0.1 on any machine, so it reaches this local server the same way).
  - A Privacy Policy URL set on the app (reuse
    https://gilbert231-dot.github.io/faceless-video-platform/privacy.html).
  - The three scopes above requested on the app's permissions page.
  - Note the App ID and App Secret (Settings -> Basic).

Usage:
    python facebook_setup.py
    # or: FB_APP_ID=... FB_APP_SECRET=... python facebook_setup.py

⚠️  The repo is PUBLIC — never save the printed token into any file inside
    this project. Paste it straight into GitHub:
    Settings -> Secrets and variables -> Actions -> edit FB_PAGE_ACCESS_TOKEN.
"""

import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

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

# Make emoji-safe output even when stdout is redirected (Windows cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API_VERSION = "25.0"
GRAPH = f"https://graph.facebook.com/v{API_VERSION}"
# The OAuth consent dialog lives on www.facebook.com, NOT graph.facebook.com
# (graph is only for API calls — sending the dialog there returns
# "Object with ID 'dialog' does not exist").
DIALOG = f"https://www.facebook.com/v{API_VERSION}/dialog/oauth"
# lvh.me always resolves to 127.0.0.1 (like localhost) but has a real TLD, so
# Meta's App Domains field accepts it. The dashboard rejects 'localhost'
# outright with "Must contain a top level domain".
#
# The scheme MUST be https: Facebook now blocks http:// redirect URIs with
# "isn't using a secure connection to transfer information". The self-signed
# cert below satisfies the scheme check; the browser shows a one-time warning
# you click through.
REDIRECT_URI = "https://lvh.me:8765/"

CERTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fb_local_certs")
CERT_FILE = os.path.join(CERTS_DIR, "lvhme.pem")
KEY_FILE = os.path.join(CERTS_DIR, "lvhme.key")


def ensure_local_certs():
    """Generate (once) a throwaway self-signed cert covering lvh.me,
    localhost and 127.0.0.1 so the local redirect can run over https.
    Stored next to this script (git-ignored); harmless dev-only key."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return CERT_FILE, KEY_FILE
    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit(
            "openssl not found on PATH — install OpenSSL or run this script "
            "from Git Bash, then re-run."
        )
    os.makedirs(CERTS_DIR, exist_ok=True)
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", KEY_FILE, "-out", CERT_FILE,
            "-days", "365", "-nodes",
            "-subj", "/CN=lvh.me",
            "-addext", "subjectAltName=DNS:lvh.me,DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return CERT_FILE, KEY_FILE

# Permissions the pipeline needs to post videos to your page (see
# FACEBOOK_SETUP.md). pages_show_list unlocks /me/accounts (page list),
# pages_read_engagement + pages_manage_posts unlock publishing.
# pages_manage_engagement additionally unlocks the Video Thumbnails API
# (custom cover images on posted videos) — added so a re-auth mints a
# token that can set the reddit-card thumbnail on every video.
SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_engagement"

AUTH_URL = (
    f"{DIALOG}?client_id={{app_id}}&redirect_uri="
    f"{urllib.parse.quote(REDIRECT_URI, safe='')}"
    f"&scope={SCOPES}&response_type=code&auth_type=rerequest"
)


class _Handler(BaseHTTPRequestHandler):
    """Captures the ?code=... Facebook redirects to after consent."""

    server_code = None
    server_error = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            type(self).server_code = qs["code"][0]
            body = (
                b"<html><body><h3>Authorization complete! "
                b"You can close this window.</h3></body></html>"
            )
        elif "error" in qs:
            type(self).server_error = qs["error"][0]
            body = (
                b"<html><body><h3>Authorization failed - you can close this "
                b"window and check the terminal.</h3></body></html>"
            )
        else:
            body = b"<html><body><h3>Waiting for Facebook...</h3></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the console clean
        pass


def wait_for_code(server, timeout_seconds=300):
    server.timeout = 5  # handle_request() wakes up periodically to check time
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        server.handle_request()
        if server.RequestHandlerClass.server_code or server.RequestHandlerClass.server_error:
            return
    raise SystemExit("Timed out waiting for Facebook authorization (5 min).")


def exchange_for_tokens(app_id, app_secret, code):
    """code -> short-lived user token -> long-lived (60-day) user token."""
    resp = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
        timeout=60,
    )
    payload = resp.json()
    if resp.status_code != 200 or "access_token" not in payload:
        raise RuntimeError(f"Code exchange failed (HTTP {resp.status_code}): {payload}")
    short_token = payload["access_token"]

    resp = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=60,
    )
    payload = resp.json()
    if resp.status_code != 200 or "access_token" not in payload:
        raise RuntimeError(f"Token exchange failed (HTTP {resp.status_code}): {payload}")
    return payload["access_token"]  # long-lived user token (60 days)


def list_pages(long_lived_token):
    """/me/accounts -> the pages this user manages (with page tokens)."""
    resp = requests.get(
        f"{GRAPH}/me/accounts",
        params={"access_token": long_lived_token},
        timeout=60,
    )
    payload = resp.json()
    if resp.status_code != 200 or "data" not in payload:
        err = payload.get("error", {})
        raise RuntimeError(
            f"Could not list pages (HTTP {resp.status_code}): "
            f"{err.get('message', payload)}"
        )
    return payload["data"]


def verify_page(page_id, page_token):
    """Confirm the page token works and print the page name."""
    resp = requests.get(
        f"{GRAPH}/{page_id}",
        params={"fields": "name", "access_token": page_token},
        timeout=60,
    )
    payload = resp.json()
    if resp.status_code != 200 or "name" not in payload:
        err = payload.get("error", {})
        raise RuntimeError(
            f"Page verification failed (HTTP {resp.status_code}): "
            f"{err.get('message', payload)}"
        )
    return payload["name"]


def main():
    app_id = os.getenv("FB_APP_ID")
    app_secret = os.getenv("FB_APP_SECRET")

    if not app_id or not app_secret:
        print("FB_APP_ID / FB_APP_SECRET not found in env or .env.")
        print("Find them at developers.facebook.com -> your app -> Settings -> Basic")
        print("(App ID + App Secret, under the 'Show' button).\n")
        app_id = input("App ID: ").strip()
        app_secret = input("App Secret: ").strip()
        print()

    if not app_id or not app_secret:
        print("❌ Both values are required. Exiting.")
        sys.exit(1)

    # Make sure the redirect URI is registered BEFORE opening the browser.
    print("👉 Before you continue, confirm your Meta app has this EXACT value in")
    print(f"   Facebook Login → Settings → Valid OAuth Redirect URIs:  {REDIRECT_URI}")
    print("   (If it's missing, add it there now — the consent screen will fail")
    print("   with a redirect-URI mismatch otherwise.)\n")

    cert_file, key_file = ensure_local_certs()
    server = HTTPServer(("127.0.0.1", 8765), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.RequestHandlerClass.server_code = None
    server.RequestHandlerClass.server_error = None

    auth_url = AUTH_URL.format(app_id=app_id)
    print("👉 Opening your browser to Facebook's consent screen…")
    print("   Your browser will warn the connection 'isn't private' — click")
    print("   Advanced → Proceed. That's expected: a self-signed local cert.")
    print("   Log in with the account that OWNS your Facebook Page and click Allow.")
    print("   (If your browser doesn't open, copy this URL into it:\n")
    print(f"   {auth_url}\n")
    webbrowser.open(auth_url)

    try:
        wait_for_code(server)
    finally:
        server.server_close()

    if _Handler.server_error:
        print(f"\n❌ Facebook returned an error: {_Handler.server_error}")
        sys.exit(1)
    code = _Handler.server_code
    print("\n✅ Authorization complete!\n")

    try:
        user_token = exchange_for_tokens(app_id, app_secret, code)
        pages = list_pages(user_token)
    except Exception as e:
        print(f"\n❌ Token setup failed: {e}")
        print("   Check the App ID/Secret and that the app has Facebook Login enabled.")
        sys.exit(1)

    if not pages:
        print("❌ No Facebook Pages found on this account.")
        print("   Create a Page in the Facebook app first, then re-run this script.")
        sys.exit(1)

    if len(pages) == 1:
        page = pages[0]
    else:
        print("Found multiple Pages — pick the one to post to:")
        for i, p in enumerate(pages, 1):
            print(f"   {i}. {p.get('name')} (id {p.get('id')})")
        try:
            choice = int(input("Number: ").strip())
            page = pages[choice - 1]
        except (ValueError, IndexError):
            print("❌ Invalid choice. Exiting.")
            sys.exit(1)

    page_id = page.get("id")
    page_token = page.get("access_token")
    if not page_token:
        print("❌ Facebook did not return a page access token for this page.")
        print("   Make sure the app has the pages_manage_posts permission.")
        sys.exit(1)

    try:
        name = verify_page(page_id, page_token)
        print(f"✅ Verified — this token posts to the page: {name}")
    except Exception as e:
        print(f"⚠️  Token minted but page verification failed: {e}")
        name = page.get("name", page_id)

    print("\nNow add THREE GitHub secrets (Settings → Secrets and variables → Actions):\n")
    print(f"   Name:  FB_APP_ID")
    print(f"   Value: {app_id}\n")
    print(f"   Name:  FB_PAGE_ID")
    print(f"   Value: {page_id}\n")
    print(f"   Name:  FB_PAGE_ACCESS_TOKEN")
    print(f"   Value: {page_token}\n")
    print("Then flip FACEBOOK_ENABLED to 'true' in the repo and the next run posts")
    print("to Facebook (test runs post DRAFTS you can review on the page first).\n")
    print("⚠️  This repo is PUBLIC — don't paste the value above into any file in")
    print("    the project. GitHub Secrets is the only place it should live.\n")
    print(f"    (Reminder: the page token does NOT expire — you only re-run this if")
    print(f"     you change your Facebook password or remove the app.)")


if __name__ == "__main__":
    main()
