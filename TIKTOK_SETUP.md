# TikTok Automation — Setup Guide

This project posts your generated videos to TikTok using TikTok's **official
Content Posting API** (no unofficial bots — nothing that can get the account
banned). This guide covers the **one-time manual steps only you can do**, and
the pause/play controls for the whole automation.

---

## Part 1 — What YOU must do manually (one time, ~30–60 min)

The code side is already in place (`tiktok_uploader.py`, `tiktok_setup.py` and
the workflow step). These steps require your TikTok account and can't be done
from the automation:

### 1. Create the developer app
1. Go to <https://developers.tiktok.com> and sign in (or create an account).
2. Click your profile icon → **Manage apps** → **Create app**.
3. Choose platform **Desktop App** (required so we can use a
   `http://localhost` redirect URI).
4. Fill in the app name and a privacy policy URL (any simple page — e.g. a
   GitHub Pages page or even the repo URL — is accepted).

### 1b. The submission form — exact values to use

These are the fields that decide whether the app gets approved. Most are
filled from files already in this repo:

| Form field | What to enter |
|---|---|
| App name | Any name you like, e.g. **Faceless Video Creator** (the ToS/privacy pages already use this name) |
| App icon | Download **`app_icon.png`** from the repo (repo root) and upload it — already 1024×1024, ready to go |
| App description | Paste: *"Automates creation and posting of short-form faceless story videos. Users provide story text; the app rewrites it, generates an AI voiceover, compiles a captioned video and posts it to the user's own TikTok account via the official API."* |
| Category | **Developer tools** or **Entertainment** (either is fine) |
| Privacy Policy URL | `https://gilbert231-dot.github.io/faceless-video-platform/privacy.html` |
| Terms of Service URL | `https://gilbert231-dot.github.io/faceless-video-platform/terms.html` |
| Redirect URI | `http://localhost:8080/callback` |
| Platform | **Desktop App** |
| Scopes | `user.info.basic` + `video.publish` |
| Direct Post | **Enabled** (the toggle in the Content Posting API config) |

> The repo already contains the **site-verification token** TikTok asked for
> (`tiktokTocajJRvTUwOWbzIqwkunUDn1zJj8Aa6.txt`) — it's live at
> `https://gilbert231-dot.github.io/faceless-video-platform/tiktokTocajJRvTUwOWbzIqwkunUDn1zJj8Aa6.txt`
> (GitHub Pages serves the file after each push; give it a minute or two to
> deploy before TikTok checks it).

### 2. Add the Content Posting API product + scopes
1. Open your app → **Add products** → add **Content Posting API**.
2. In the app's **Scopes** section, request:
   - `user.info.basic`
   - `video.publish`
3. **Enable the "Direct Post" configuration** for the Content Posting API in
   the app settings (this is a toggle; it's what lets the API publish straight
   to the profile instead of only dropping drafts).

### 3. Register the redirect URI
1. In the app's **Login Kit / Redirect URI** configuration add:
   `http://localhost:8080/callback`
   (or `http://localhost:*/callback` — wildcard ports are allowed).
2. Note your app's **Client Key** and **Client Secret** from the app page —
   you'll need them in Part 2.

### 4. Submit `video.publish` for approval
1. Submit the app + the `video.publish` scope for **review**.
2. Approval times vary — reports range from ~10 hours to a couple of weeks.
   **You can authorize and build everything before approval**, but the API
   will refuse to post until `video.publish` is approved.
3. Important: until your app also passes TikTok's **content audit**, **all
   posts via the API are forced to private viewing mode** — exactly what we
   want for a review-first pipeline. The workflow posts `SELF_ONLY` (private)
   by default. After the audit passes you can switch to `PUBLIC_TO_EVERYONE`.

### 5. Authorize your TikTok account (run the setup script)
Run this on your own computer (not in GitHub Actions), from the repo folder:

```bash
python -m pip install -r requirements.txt   # if not already installed
TIKTOK_CLIENT_KEY=your_client_key TIKTOK_CLIENT_SECRET=your_client_secret python tiktok_setup.py
```

It opens your browser → you log in to TikTok → approve the app → the script
prints the secret values. (Alternatively put the two values in a `.env` file
in the repo and just run `python tiktok_setup.py`.)

### 6. Add the secrets to GitHub
In your repo: **Settings → Secrets and variables → Actions → New repository
secret**, add these three:

| Secret name | Value |
|---|---|
| `TIKTOK_CLIENT_KEY` | your Client Key |
| `TIKTOK_CLIENT_SECRET` | your Client Secret |
| `TIKTOK_REFRESH_TOKEN` | printed by the setup script |

Optional but recommended — automatic refresh-token rotation (see Part 4):
| `GH_PAT` | a fine-grained PAT with **Secrets** (read & write) permission on this repo |

### 7. Turn TikTok posting on
1. Open the file **`TIKTOK_ENABLED`** in the repo (GitHub web UI → click the
   file → pencil icon).
2. Change `false` → `true`, commit.
3. Next run will post to TikTok automatically (private) right after YouTube.

> YouTube posting stays independent — it already works. TikTok posting only
> runs when `TIKTOK_ENABLED` is `true` **and** the three secrets exist.

---

## Part 2 — Pause / Play the whole automation

The most common reason to pause is running low on ElevenLabs credits. There is
a kill switch for the **entire** pipeline (generation + YouTube + TikTok):

- **⏸️ PAUSE:** in the repo (GitHub web UI) click **Add file → Create new
  file**, name it exactly **`AUTOMATION_PAUSED`** (any content, e.g. a dash),
  and commit.
- **▶️ RESUME:** open `AUTOMATION_PAUSED` and delete it (click the file →
  the trash icon → commit).

When paused:
- The daily 2 AM UTC cron still fires, but the **generate job is skipped** —
  zero ElevenLabs credits spent, zero stories consumed, nothing posted.
- The run shows green in Actions with "generate skipped" and a message saying
  it's paused.

Fastest emergency stop (if you don't want to touch files): set
`ELEVENLABS_API_KEY` to any invalid value in Settings → Secrets — the very
first thing the run does is check it and abort before spending anything.

---

## Part 3 — Privacy & going live

| Stage | What TikTok allows | `TIKTOK_PRIVACY_LEVEL` |
|---|---|---|
| Before audit passes | private only (forced by TikTok) | `SELF_ONLY` (default — private) |
| After audit passes | public posts allowed | `PUBLIC_TO_EVERYONE` when you're ready |

`TIKTOK_PRIVACY_LEVEL` lives in the workflow file
(`.github/workflows/generate_video.yml`, in the "Upload videos to TikTok"
step). Videos you review privately are consumed as "used" either way, so
decide before generating whether you want them public — there's no "make
private video public later" via the API.

Also note: every TikTok video is sent with `is_aigc=True`, so TikTok labels it
as AI-generated content — that's the required disclosure for this content.

---

## Part 4 — Token maintenance

- The refresh token is valid **365 days**. Every run refreshes the access
  token automatically (access tokens only last 24 h).
- TikTok **rotates** the refresh token on refresh. If you added the `GH_PAT`
  secret, the run saves the new token back to the `TIKTOK_REFRESH_TOKEN`
  secret automatically and you never think about it again.
- Without `GH_PAT`: the original token keeps working for its full year — just
  **re-run `tiktok_setup.py` before the 365 days are up** (the run log warns
  you when this is pending).
- If posting ever fails with `access_token_invalid`, re-run `tiktok_setup.py`
  and update the `TIKTOK_REFRESH_TOKEN` secret.

---

## Part 5 — FAQ / limits

- **Posting rate:** the API caps at ~6 init requests/min and a daily post cap
  per user — 2 videos/day is nowhere near it.
- **Duration:** TikTok direct posts cap at 5 minutes (our videos are ~2–3 min).
- **Format:** our videos are 1080×1920 (9:16) — TikTok's native format.
- **Failure reasons:** `duration_check_failed`, `picture_size_check_failed`,
  etc. are surfaced verbatim in the run log; `spam_risk_text` means the
  caption looked spammy (rare for story captions).
- **Where the video goes:** with `SELF_ONLY` you'll find it on your profile
  as a private video, or in the app's videos list marked private — review it
  there, just like the YouTube private videos.
