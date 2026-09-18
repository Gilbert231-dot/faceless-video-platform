"""
verify_posting_credentials.py — fail-fast credential preflight.

WHY THIS EXISTS
---------------
The pipeline renders for ~1.5 h and spends ElevenLabs credits BEFORE it tries
to upload anything. If a posting credential is dead, the old flow burned the
whole render (plus TTS credits and one story) and only then died at the upload
step. That is exactly what happened on run #260 (2026-09-15): YouTube answered
`invalid_grant: Token has been expired or revoked` at the last step, the
artifact step was skipped, and the finished video was lost with the runner.

This script proves every ENABLED credential works BEFORE the story is picked
and before any render starts. It costs a few seconds.

THE GOOGLE 7-DAY TRAP
---------------------
An OAuth app whose publishing status is "Testing" (External user type) is
issued refresh tokens that expire after 7 DAYS — that is why the YouTube token
died mid-morning on Sep 15 and again on Sep 8, with the three earlier batches
of the same day succeeding. Publishing the consent screen to "In production"
stops the cycle; until then a re-minted token lasts a week.

WHAT IT CHECKS
--------------
  YouTube   token refresh + channels().list(part=snippet, mine=true)  (1 quota unit)
  Facebook  GET /{page_id}?fields=name with the page token            (free)
  TikTok    ONE refresh (persisted), then creator_info

TikTok rotates its refresh token on every refresh, and the upload step captures
TIKTOK_REFRESH_TOKEN from the secret when the step starts. Refreshing here AND
again in the upload step would therefore use a superseded token, so this script
refreshes once and hands the fresh access token to the upload step through
$GITHUB_ENV; tiktok_uploader.publish_tiktok(access_token=...) reuses it.

POLICY
------
  * a platform whose gate file (FACEBOOK_ENABLED / TIKTOK_ENABLED) is not
    'true' is SKIPped — we never fail a run over a disabled platform;
  * a transient/network failure is a WARNING, never a failure: one blip must
    not cost the day's videos;
  * a dead or misconfigured credential is a FAILURE with a copy-paste remedy.

Exit codes: 0 = usable (warnings allowed), 1 = at least one credential is dead.

Usage:
    python verify_posting_credentials.py                  # all enabled platforms
    python verify_posting_credentials.py --only youtube
    python verify_posting_credentials.py --skip tiktok

Reads the same env vars the workflow gives the upload steps. On your laptop it
also falls back to .env and local_config.json (both gitignored), and it can run
standalone — no pipeline state, no video files, no side effects beyond a token
refresh. Secrets are only ever printed masked.
"""

import argparse
import json
import os
import subprocess
import sys
import time

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard requirement in CI
    requests = None

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"

GRAPH = "https://graph.facebook.com/v25.0"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_CREATOR_INFO_URL = (
    "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
)
DEFAULT_REPO = "Gilbert231-dot/faceless-video-platform"
HTTP_TIMEOUT = 30

# A token may only be refreshed with scopes that were granted at consent time.
# This list is deliberately the ORIGINAL three scopes, not everything
# youtube_setup.py now requests: Google rejects the whole refresh with
# `invalid_scope` when even one requested scope was never consented to, and
# this file is a CI preflight that gates posting — so listing a newer scope
# here would fail runs until the token is re-minted. Keep it to what every
# token in circulation already carries.
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

DISPLAY_NAME = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok"}

# Anything matching these means "we could not verify right now", which must
# never fail a run (rate limits, runner network hiccups, provider 5xx).
TRANSIENT_MARKERS = (
    "timed out", "timeout", "connection reset", "connection aborted",
    "connection refused", "temporary failure", "temporarily unavailable",
    "name resolution", "ssl", "broken pipe", "remote end closed",
    "too many requests", "rate limit", "429", "500", "502", "503", "504",
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _is_transient(text, exc_name=""):
    blob = f"{exc_name} {text}".lower()
    return any(marker in blob for marker in TRANSIENT_MARKERS)


def _classify_youtube_grant_error(exc):
    """Tell Google's two `invalid_grant` cases apart.

    They need OPPOSITE remedies, and reporting both as "expired or revoked"
    sends you to re-mint a token that was never broken:

      * description "Token has been expired or revoked." -> genuinely dead
        (re-mint).
      * description "Bad Request" -> the stored string is not parseable, i.e. a
        typo, stray space or line break from a hand-paste. Re-minting does
        nothing; the value has to be re-copied.

    Measured 2026-09-16 against the live endpoint: corrupting ONE character of a
    valid token reproduces "Bad Request" exactly, while a token from the Testing
    era yields "Token has been expired or revoked".

    Returns 'malformed' | 'dead' | 'client' | 'other_grant' | None.
    """
    # RefreshError keeps the provider body: args[1] is normally the error dict.
    detail = ""
    for arg in getattr(exc, "args", ()) or ():
        if isinstance(arg, dict):
            detail = str(arg.get("error_description") or arg.get("error") or "")
            break

    blob = f"{exc} {detail}".lower()
    # A mismatched client is its own problem, and its remedy is not a re-mint.
    if "invalid_client" in blob or "unauthorized_client" in blob:
        return "client"
    if "bad request" in blob:
        return "malformed"
    if "expired or revoked" in blob or "revoked" in blob:
        return "dead"
    if "invalid_grant" in blob:
        return "other_grant"
    return None


def mask(value):
    """Never print a secret: first/last 4 characters plus its length."""
    if not value:
        return "(unset)"
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]} (len {len(value)})"


def load_dotenv_if_present():
    """Load .env the same way the setup scripts do (python-dotenv optional)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
        return
    except ImportError:
        pass
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(
                    key.strip(), value.strip().strip('"').strip("'")
                )
    except OSError:
        pass


def load_local_config():
    """local_config.json (gitignored) is how the laptop scripts hold secrets."""
    try:
        with open("local_config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def gate_enabled(path):
    """FACEBOOK_ENABLED / TIKTOK_ENABLED: 'true' (any case, padded) enables."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip().strip('"').lower() == "true"
    except OSError:
        return False


def export_to_github_env(values):
    """Hand values to later steps (only exists inside GitHub Actions)."""
    path = os.environ.get("GITHUB_ENV")
    if not path:
        return False
    try:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")
        return True
    except OSError:
        return False


def result(status, detail, identifiers=None):
    return {"status": status, "detail": detail, "identifiers": identifiers or {}}


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

def _youtube_values():
    """Env first (GitHub Actions), then local_config.json (laptop runs)."""
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    source = "environment"
    if not all([client_id, client_secret, refresh_token]):
        config = load_local_config()
        client_id = client_id or config.get("youtube_client_id")
        client_secret = client_secret or config.get("youtube_client_secret")
        refresh_token = refresh_token or config.get("youtube_refresh_token")
        source = "environment + local_config.json"
    return client_id, client_secret, refresh_token, source


def check_youtube():
    client_id, client_secret, refresh_token, source = _youtube_values()
    missing = [
        name
        for name, value in {
            "YOUTUBE_CLIENT_ID": client_id,
            "YOUTUBE_CLIENT_SECRET": client_secret,
            "YOUTUBE_REFRESH_TOKEN": refresh_token,
        }.items()
        if not value
    ]
    if missing:
        return result(
            FAIL,
            "missing secret(s): "
            + ", ".join(missing)
            + " — run `python youtube_setup.py` and set them in GitHub → Settings "
            "→ Secrets and variables → Actions",
        )

    try:
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - installed via requirements.txt
        return result(WARN, f"Google client libraries not available ({exc}) — cannot verify")

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=YOUTUBE_SCOPES,
    )

    try:
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        response = youtube.channels().list(part="snippet", mine=True).execute()
    except RefreshError as exc:
        text = str(exc)
        kind = _classify_youtube_grant_error(exc)
        # Always report WHICH token was checked, so a stale local_config.json can
        # never be mistaken for a dead GitHub secret.
        identifiers = {
            "YOUTUBE_REFRESH_TOKEN": mask(refresh_token),
            "credentials from": source,
        }
        if kind == "malformed":
            return result(
                FAIL,
                "refresh token is MALFORMED, not expired — Google could not parse it "
                "(invalid_grant: Bad Request). Minting another one will NOT help: the "
                "stored value is wrong, so re-copy YOUTUBE_REFRESH_TOKEN as ONE "
                "unbroken string (no spaces, no line breaks) and save it again. "
                f"The value in use is {mask(refresh_token)} — a wrong length is a "
                "giveaway, but swapping one character keeps the length identical, so "
                "re-copy from the source instead of re-typing it.",
                identifiers,
            )
        if kind == "dead":
            return result(
                FAIL,
                "refresh token is EXPIRED or REVOKED — mint a new one with "
                "`python youtube_setup.py` and update the YOUTUBE_REFRESH_TOKEN secret. "
                "Google expires these tokens 7 days after they are minted while the "
                "OAuth app's publishing status is 'Testing' — publish it to 'In "
                "production' to stop the weekly cycle.",
                identifiers,
            )
        if kind == "client":
            return result(
                FAIL,
                "Google rejected the OAuth client, not the token "
                "(invalid_client / unauthorized_client) — YOUTUBE_CLIENT_ID and "
                "YOUTUBE_CLIENT_SECRET must come from the SAME OAuth client that "
                "minted the refresh token.",
                identifiers,
            )
        return result(FAIL, f"token refresh failed: {text[:300]}", identifiers)
    except Exception as exc:
        text = str(exc)
        name = type(exc).__name__
        lowered = text.lower()
        if "403" in text and ("insufficient" in lowered or "scope" in lowered):
            return result(
                WARN,
                "token refreshes, but channels().list is not permitted by the granted "
                f"scopes ({text[:160]}). Uploads may still work — the minted token should "
                "include youtube.readonly.",
            )
        if _is_transient(text, name):
            return result(
                WARN, f"could not reach Google ({name}: {text[:160]}) — continuing unverified"
            )
        return result(FAIL, f"{name}: {text[:300]}")

    items = response.get("items") or []
    identifiers = {
        "YOUTUBE_REFRESH_TOKEN": mask(refresh_token),
        "credentials from": source,
    }
    if not items:
        return result(
            WARN,
            "token is valid but no channel was found on this account — make sure you "
            "authorized with the account that OWNS the channel",
            identifiers,
        )
    title = (items[0].get("snippet") or {}).get("title") or items[0].get("id")
    return result(OK, f"channel: {title}", identifiers)


# --------------------------------------------------------------------------
# Facebook
# --------------------------------------------------------------------------

def check_facebook():
    app_id = os.getenv("FB_APP_ID")
    page_id = os.getenv("FB_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    missing = [
        name
        for name, value in {
            "FB_APP_ID": app_id,
            "FB_PAGE_ID": page_id,
            "FB_PAGE_ACCESS_TOKEN": token,
        }.items()
        if not value
    ]
    if missing:
        return result(
            FAIL,
            "missing secret(s): "
            + ", ".join(missing)
            + " — run `python facebook_setup.py` and set them in GitHub → Settings → "
            "Secrets and variables → Actions",
        )
    if requests is None:
        return result(WARN, "the 'requests' package is not installed — cannot verify")

    # Read-only call: same one facebook_setup.py uses to verify a minted token.
    # Costs nothing and posts nothing.
    try:
        response = requests.get(
            f"{GRAPH}/{page_id}",
            params={"fields": "name,id", "access_token": token},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        name = type(exc).__name__
        if _is_transient(str(exc), name):
            return result(WARN, f"could not reach Facebook ({name}) — continuing unverified")
        return result(FAIL, f"could not reach Facebook ({name}): {str(exc)[:200]}")

    try:
        payload = response.json()
    except ValueError:
        return result(FAIL, f"Facebook returned non-JSON (HTTP {response.status_code})")

    error = payload.get("error")
    if error:
        code = error.get("code")
        subcode = error.get("error_subcode")
        message = error.get("message") or code
        if code == 190:
            return result(
                FAIL,
                "page access token is INVALID or EXPIRED — re-run "
                "`python facebook_setup.py` and update FB_PAGE_ACCESS_TOKEN. "
                "(A Facebook password change or removing the app revokes it; the "
                "page token itself has no fixed expiry.)",
            )
        if code == 200:
            return result(
                FAIL,
                f"the token lacks permission for this page: {message} — it needs "
                "pages_show_list, pages_read_engagement and pages_manage_posts",
            )
        if code in (803, 100):
            return result(
                FAIL,
                f"page id / parameter problem ({code}/{subcode}): {message} — check FB_PAGE_ID",
            )
        if code in (4, 17, 32, 613) or _is_transient(str(message)):
            return result(WARN, f"Facebook rate-limited or unavailable ({code}): {message}")
        return result(WARN, f"Facebook returned an unexpected error ({code}/{subcode}): {message}")

    name = payload.get("name") or page_id
    return result(OK, f"page: {name}", {"FB_PAGE_ACCESS_TOKEN": mask(token)})


# --------------------------------------------------------------------------
# TikTok
# --------------------------------------------------------------------------

def _persist_tiktok_refresh_token(new_token):
    """Persist a rotated refresh token so the NEXT run can still refresh.

    Deliberately implemented here instead of calling
    tiktok_uploader.save_refresh_token_via_gh(): that helper swallows its own
    failures (it is best-effort by design), and a silent "persisted" here would
    hide a token that the next run can no longer use.
    """
    pat = os.getenv("GH_PAT")
    if not pat:
        return False, "GH_PAT is not set, so the rotation could not be persisted"
    repo = os.getenv("GITHUB_REPOSITORY") or DEFAULT_REPO
    try:
        subprocess.run(
            ["gh", "secret", "set", "TIKTOK_REFRESH_TOKEN", "--repo", repo],
            input=new_token.encode("utf-8"),
            check=True,
            capture_output=True,
            env={**os.environ, "GH_TOKEN": pat},
        )
        return True, "gh secret set"
    except Exception as exc:
        return False, str(exc)[:160]


def _tiktok_creator_info(access_token):
    """Confirm the grant actually carries video.publish (posting needs it)."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    try:
        response = requests.post(
            TIKTOK_CREATOR_INFO_URL, headers=headers, json={}, timeout=HTTP_TIMEOUT
        )
    except Exception as exc:
        name = type(exc).__name__
        if _is_transient(str(exc), name):
            return WARN, f"could not reach TikTok ({name}) — continuing unverified"
        return FAIL, f"could not reach TikTok ({name}): {str(exc)[:200]}"

    try:
        payload = response.json()
    except ValueError:
        return FAIL, f"TikTok creator_info returned non-JSON (HTTP {response.status_code})"

    error = payload.get("error") or {}
    code = error.get("code")
    if code in (None, "", "ok"):
        data = payload.get("data") or {}
        options = data.get("privacy_level_options") or []
        who = data.get("creator_username") or "ok"
        options_text = ", ".join(options) if options else "n/a"
        return OK, f"creator: {who} (privacy options: {options_text})"
    if code == "scope_not_authorized":
        return FAIL, (
            "the grant does not include video.publish — re-run `python tiktok_setup.py` "
            "and re-authorize, or wait for the scope to be approved in the TikTok developer portal"
        )
    if code == "unaudited_client_can_only_post_to_private_accounts":
        return WARN, (
            "the app has not passed TikTok's audit yet, so API posts stay private "
            "(SELF_ONLY) until it does — expected and harmless"
        )
    if _is_transient(str(code) + str(error.get("message", ""))):
        return WARN, f"TikTok rate-limited or unavailable ({code})"
    return WARN, f"creator_info returned ({code}): {error.get('message') or code}"


def check_tiktok():
    client_key = os.getenv("TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET")
    refresh_token = os.getenv("TIKTOK_REFRESH_TOKEN")
    missing = [
        name
        for name, value in {
            "TIKTOK_CLIENT_KEY": client_key,
            "TIKTOK_CLIENT_SECRET": client_secret,
            "TIKTOK_REFRESH_TOKEN": refresh_token,
        }.items()
        if not value
    ]
    if missing:
        return result(
            FAIL,
            "missing secret(s): "
            + ", ".join(missing)
            + " — run `python tiktok_setup.py` and set them in GitHub → Settings → "
            "Secrets and variables → Actions",
        )
    if requests is None:
        return result(WARN, "the 'requests' package is not installed — cannot verify")

    try:
        response = requests.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        name = type(exc).__name__
        if _is_transient(str(exc), name):
            return result(WARN, f"could not reach TikTok ({name}) — continuing unverified")
        return result(FAIL, f"could not reach TikTok ({name}): {str(exc)[:200]}")

    try:
        payload = response.json()
    except ValueError:
        return result(FAIL, f"TikTok token endpoint returned non-JSON (HTTP {response.status_code})")

    if response.status_code != 200 or payload.get("error"):
        error = payload.get("error") or {}
        code = error.get("code") or response.status_code
        message = error.get("message") or payload
        if _is_transient(f"{code} {message}"):
            return result(WARN, f"TikTok token endpoint unavailable ({code}) — continuing unverified")
        return result(
            FAIL,
            f"token refresh REJECTED ({code}): {str(message)[:200]} — re-run "
            "`python tiktok_setup.py` and update TIKTOK_REFRESH_TOKEN (these last 365 days, "
            "and are invalidated if you revoke the app's access)",
        )

    access_token = payload.get("access_token")
    new_refresh = payload.get("refresh_token")
    notes = []

    if new_refresh and new_refresh != refresh_token:
        persisted, how = _persist_tiktok_refresh_token(new_refresh)
        if persisted:
            notes.append("rotated refresh token persisted")
        else:
            # Best-effort by design: TikTok keeps the previous token usable, so a
            # missing PAT is a warning, not a reason to skip the day's videos.
            notes.append(
                f"⚠️ rotation not persisted ({how}) — add the GH_PAT secret (Secrets "
                "permission) or re-run tiktok_setup.py before the 365-day expiry"
            )

    exported = export_to_github_env(
        {
            "TIKTOK_ACCESS_TOKEN": access_token or "",
            "TIKTOK_REFRESH_TOKEN": new_refresh or refresh_token,
        }
    )
    if exported:
        notes.append(
            "access token handed to the upload step (it must not refresh again — "
            "TikTok rotates the refresh token on every refresh)"
        )

    creator_status, creator_detail = _tiktok_creator_info(access_token)
    status = FAIL if creator_status == FAIL else (WARN if creator_status == WARN else OK)
    detail = f"refresh ok; {creator_detail}"
    if notes:
        detail += " — " + "; ".join(notes)
    return result(status, detail, {"TIKTOK_REFRESH_TOKEN": mask(refresh_token)})


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

CHECKS = {
    "youtube": ("YouTube", "YOUTUBE", check_youtube),
    "facebook": ("Facebook", "FACEBOOK_ENABLED", check_facebook),
    "tiktok": ("TikTok", "TIKTOK_ENABLED", check_tiktok),
}


def run_checks(only=None, skip=None):
    """Run every enabled platform check, in a fixed order (YouTube first)."""
    only = {p.strip().lower() for p in (only or []) if p.strip()}
    skip = {p.strip().lower() for p in (skip or []) if p.strip()}

    results = {}
    for key in ("youtube", "facebook", "tiktok"):
        label, gate, check = CHECKS[key]
        if only and key not in only:
            results[label] = result(SKIP, "not requested (--only)")
            continue
        if key in skip:
            results[label] = result(SKIP, "skipped (--skip)")
            continue
        # YouTube is always posted to, so it has no gate file.
        if gate != "YOUTUBE" and not gate_enabled(gate):
            results[label] = result(SKIP, f"{gate} is not 'true'")
            continue
        results[label] = check()
    return results


def print_report(results):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"🔐 Posting-credential preflight — {stamp}")
    print("")
    for label, res in results.items():
        print(f"  {label:<9} {res['status']:<5} {res['detail']}")
        for name, value in res["identifiers"].items():
            print(f"            {name} = {value}")
    print("")

    failures = [label for label, res in results.items() if res["status"] == FAIL]
    warnings = [label for label, res in results.items() if res["status"] == WARN]
    if failures:
        print("❌ Unusable credential(s): " + ", ".join(failures))
        print("   Fix those before the run can post. Nothing was rendered yet, so")
        print("   no credits, stories or video files were spent on this attempt.")
    elif warnings:
        print("⚠️  Verified with warning(s): " + ", ".join(warnings))
        print("   The run will continue — these are transient or informational.")
    else:
        print("✅ Every enabled credential is usable.")
    return failures


def write_step_summary(results):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## 🔐 Posting-credential preflight", "", "| Platform | Result | Detail |", "|---|---|---|"]
    for label, res in results.items():
        detail = res["detail"].replace("|", "\\|")
        lines.append(f"| {label} | {res['status']} | {detail} |")
    lines.append("")
    if any(res["status"] == FAIL for res in results.values()):
        lines.append(
            "**Nothing was rendered** — the run stopped before generating a video, so no "
            "ElevenLabs credits, stories or render time were spent. Fix the credential "
            "above and re-run."
        )
        lines.append("")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify the posting credentials before a video is rendered."
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated platforms to check (default: every enabled platform)",
    )
    parser.add_argument(
        "--skip", default="", help="comma-separated platforms to skip"
    )
    args = parser.parse_args(argv)

    load_dotenv_if_present()

    results = run_checks(
        only=args.only.split(",") if args.only else None,
        skip=args.skip.split(",") if args.skip else None,
    )
    failures = print_report(results)
    write_step_summary(results)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
