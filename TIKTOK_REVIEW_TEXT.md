# TikTok App Review — Submission Text (copy-paste)

Everything below is ready to paste into the TikTok Developer Portal:
https://developers.tiktok.com/app/7672008622024591367/pending

---

## 1. App description (App details → Basic information)

Limit: **120 characters** (this paste is 108).

```
AI turns Reddit stories into narrated, captioned shorts. Creators connect their TikTok to auto-publish them.
```

---

## 2. App review explanation (App review → Required information)

Limit: **1000 characters**.

```
Faceless Video Creator is a web platform that lets creators turn Reddit stories into original short-form videos: AI narration, word-synced captions, 1080x1920 render, published on a schedule. Any user signs up, connects their TikTok, and posts videos directly to their own profile.

How each product and scope is used:
- Login Kit + user.info.basic — A "Connect with TikTok" button on the website starts OAuth; we read the user's openid, display name and avatar to show which TikTok account is connected in their workspace.
- Content Posting API + video.publish — When a user schedules a video, the app uploads the finished MP4 via video/init and video/upload, then publishes it to that user's profile with the AI-content disclosure (is_aigc=true). Direct Post is enabled; the app does not create drafts.

Demo video: screen recording of the website — the user connects their TikTok (sandbox), a story becomes a video, and it is published privately through the Content Posting API.
```

---

## 3. Scopes — remove the unused one BEFORE submitting

TikTok's review page says: "If you don't need certain products or scopes, make sure to remove them before review."

| Keep | Remove |
|---|---|
| `user.info.basic` (Login Kit) | `video.upload` — draft posting, unused. Direct Post only needs `video.publish`. |
| `video.publish` (Content Posting API) | |

---

## 4. Everything else — keep exactly as-is

| Field | Value |
|---|---|
| App name | Faceless Video Creator poster |
| Category | Photo & Video |
| App icon | `app_icon.png` (repo root — 1024×1024, 8.5 KB, PNG) |
| Privacy Policy URL | https://gilbert231-dot.github.io/faceless-video-platform/privacy.html |
| Terms of Service URL | https://gilbert231-dot.github.io/faceless-video-platform/terms.html |
| Web/Desktop URL | https://gilbert231-dot.github.io/faceless-video-platform/ |
| Platform | Desktop |
| Redirect URIs | `http://localhost:8080/callback` and `http://localhost:*/callback` |
| Products | Login Kit + Content Posting API (Direct Post toggle ON) |
| Demo video | Screen recording of the website → sandbox OAuth → private post (see `tiktok_sandbox_demo.py`) |

---

## 5. Checklist before clicking Submit

1. Confirm the new site is live at https://gilbert231-dot.github.io/faceless-video-platform/ (wait ~2 minutes after the last push).
2. Remove the `video.upload` scope (section 3).
3. Upload the new demo recording — mp4/mov, under 50 MB (record with `tiktok_sandbox_demo.py`).
4. Paste section 1 into App description and section 2 into the App review textarea.
5. Submit for review.