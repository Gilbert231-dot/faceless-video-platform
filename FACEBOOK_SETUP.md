# Facebook Automation — Setup Guide

This project posts your generated videos to a Facebook Page using Facebook's
**official Graph API Video API** (no unofficial bots — nothing that can get
the account banned). This guide covers the **one-time manual steps only you
can do**, plus how the Facebook step behaves.

---

## Part 1 — What YOU must do manually (one time, ~30–45 min)

The code side is already in place (`facebook_poster.py`, `facebook_setup.py`
and the workflow step). These steps require your Facebook account and can't
be done from the automation:

### 1. Create the Facebook Page (if you don't have one)
In the Facebook app: **Menu → Pages → Create new Page**. Name it after the
channel (e.g. the same name as your YouTube channel). You post the videos
here, and this is the page that eventually earns money.

### 2. Create the Meta developer app
1. Go to <https://developers.facebook.com> and sign in with the account that
   OWNS the Page.
2. **My Apps → Create App** → use case: **Other** → **Business** (or the
   simplest available type).
3. Add the **Facebook Login** product to the app.
4. In **App settings → Basic**, note your **App ID** and **App Secret**
   (click *Show*). You need both later.
5. Set a **Privacy Policy URL** and **Terms of Service URL** — reuse the ones
   already live for this project:
   - `https://gilbert231-dot.github.io/faceless-video-platform/privacy.html`
   - `https://gilbert231-dot.github.io/faceless-video-platform/terms.html`
   (Facebook Login won't work until a privacy policy URL is set.)

### 3. Register the redirect URI
In the app: **Facebook Login → Settings → Valid OAuth Redirect URIs**, add
**exactly** this value (trailing slash matters — the setup script listens on
this fixed port):

```
https://lvh.me:8765/
```

- The scheme **must be `https`** — Facebook now blocks `http://` redirect
  URIs with *"isn't using a secure connection to transfer information"*. The
  script generates a throwaway self-signed cert into `fb_local_certs/` and
  serves HTTPS locally; the browser shows a one-time *"not private"*
  warning which you click through (**Advanced → Proceed**).
- `lvh.me` always resolves to `127.0.0.1` (your own computer, same as
  `localhost`), but it has a real top-level domain — the dashboard **rejects
  `localhost`** in App Domains with *"Must contain a top level domain"*, so
  `lvh.me` is the workaround.
- Also add the domain to **Settings → Basic → App Domains**: type `lvh.me`
  and save (otherwise the consent screen shows "Can't load URL — the domain
  of this URL isn't included in the app's domains").

### 4. Request the three permissions
In the app: **App Review → Permissions and Features**, request (they're
standard-approval permissions):

| Permission | Why |
|---|---|
| `pages_show_list` | lets the setup script list your Pages (`/me/accounts`) |
| `pages_read_engagement` | read access to the Page (required for publishing) |
| `pages_manage_posts` | create/publish videos on the Page |
| `pages_manage_engagement` | **optional but recommended** — unlocks the Video Thumbnails API so every video gets the reddit-card custom cover (same as YouTube). Without it, videos still post but use Facebook's auto-picked thumbnail |

> If your token was minted before `pages_manage_engagement` was added, the
> custom thumbnail step logs a soft warning and videos still post fine. To
> enable thumbnails, add the permission in **App Review → Permissions and
> Features** and re-run `facebook_setup.py` to mint a new token, then update
> the `FB_PAGE_ACCESS_TOKEN` secret.

### 5. Authorize your Page (run the setup script)
Run this on your own computer (not in GitHub Actions), from the repo folder:

```bash
python -m pip install -r requirements.txt   # if not already installed
FB_APP_ID=your_app_id FB_APP_SECRET=your_app_secret python facebook_setup.py
```

It opens your browser → you log in to Facebook → approve the app → the script
verifies the page name and prints the three values you need. (Alternatively
put the two values in a `.env` file in the repo and just run
`python facebook_setup.py`.)

### 6. Add the secrets to GitHub
In your repo: **Settings → Secrets and variables → Actions → New repository
secret**, add these three:

| Secret name | Value |
|---|---|
| `FB_APP_ID` | your Meta App ID |
| `FB_PAGE_ID` | your Page ID (printed by the setup script) |
| `FB_PAGE_ACCESS_TOKEN` | printed by the setup script |

### 7. Turn Facebook posting on
1. Open the file **`FACEBOOK_ENABLED`** in the repo (GitHub web UI → click the
   file → pencil icon).
2. Change `false` → `true`, commit.
3. Next run posts to Facebook automatically right after YouTube.

> YouTube posting stays independent. Facebook posting only runs when
> `FACEBOOK_ENABLED` is `true` **and** the three secrets exist.

---

## Part 2 — Test vs. production behavior

The Facebook step mirrors the YouTube step exactly:

| Mode | What happens on Facebook |
|---|---|
| **Test run** (`test_mode=true` in the dispatch) | posts a **DRAFT** — only you (page admin) can see it. Review it on the Page, exactly like a private YouTube video |
| **Production + public** (normal cron) | posts **PUBLISHED** at the **same slots as YouTube** (12:00 / 20:00 UTC, 8h apart) — one scheduling decision per run, both platforms go live together |
| **Production + private** (YOUTUBE_PRIVACY=private) | posts PUBLISHED immediately (FB has no "private" visibility; draft is test-only) |

So: keep `FACEBOOK_ENABLED=false` while you're reviewing content, and only
flip it to `true` when you're happy — then every production run also goes
live on Facebook automatically.

---

## Part 3 — App Review (Advanced Access)

- In **Development mode**, publishing to your **own Page** works for you as
  the app admin — no review needed. You can run the whole pipeline this way.
- When you take the app **Live**, `pages_manage_posts` needs **Advanced
  Access** (App Review). Submit it through **App Review → Permissions and
  Features → Request Advanced Access** and walk through the review flow.
  Approval times vary (days to weeks) — the token and code keep working
  while you wait, only the go-live scope is gated.

---

## Part 4 — Token maintenance (the good news)

- The Page access token **does not expire**. No 7-day refresh, no annual
  re-authorization like YouTube/TikTok.
- It's only invalidated if you **change your Facebook password** or **remove
  the app** from your account (Settings → Apps and websites). If that ever
  happens, just re-run `facebook_setup.py` and update the
  `FB_PAGE_ACCESS_TOKEN` secret — nothing else changes.

---

## Part 5 — FAQ / limits

- **Scheduling:** Facebook schedules up to 75 days ahead; our slots are always
  within a day or two. Scheduled videos sit in the Page's Scheduled queue
  until their time.
- **Titles/descriptions:** titles cap at 255 chars, descriptions at 5,000 —
  the poster trims automatically.
- **Rate limits:** posting is ~4 API calls per video; Facebook's page limits
  (hundreds of calls per window) are nowhere near being hit at 2 videos/day.
- **Hashtags:** the description carries Facebook's curated tag set
  (`#RedditStories #StoryTime #Reddit ...` + the subreddit hashtag), defined in
  `config.py` → `PLATFORM_TAGS["facebook"]` — distinct from the YouTube and
  TikTok lists so each platform gets the tags it actually recognizes.
- **Reels vs video posts:** videos up to 90 seconds are posted as **Reels**
  (they appear in the Reels discovery feed — regular posts don't); longer
  stories automatically fall back to a regular page video post. Both paths
  set `is_ai_generated=true` so every video carries the AI label.
- **Monetization:** the Page becomes eligible for Facebook **Content
  Monetization** (invite-only, 5,000+ followers / 60,000 minutes watched in
  60 days) — express interest from the mobile app → Professional Dashboard →
  Monetization → Content Monetization. Payouts go to your bank via Facebook's
  Payouts once you cross the threshold.
