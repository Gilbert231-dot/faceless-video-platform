# 🏠 Local YouTube Upload Setup

## Why Local Upload?

YouTube's algorithm treats uploads from **data center IPs** (like GitHub Actions) differently than uploads from **residential IPs** (like your laptop). Videos uploaded from GitHub often get less algorithmic push, while videos uploaded manually from your laptop get promoted more aggressively.

**The solution:** Let GitHub Actions generate the videos (heavy lifting), but upload to YouTube from your laptop (residential IP).

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions (Cloud)                                     │
│  ✅ Generate videos (rendering, captions, thumbnails)       │
│  ✅ Save as artifacts (7-day retention)                      │
│  ❌ Skip YouTube upload                                      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Your Laptop (Residential IP)                               │
│  ✅ Download videos from GitHub                             │
│  ✅ Upload to YouTube (algorithm pushes it!)                 │
│  ✅ Schedule for optimal posting times                       │
└─────────────────────────────────────────────────────────────┘
```

## Setup Instructions

### Step 1: Enable Local Upload Mode on GitHub

1. Go to your GitHub repo: `https://github.com/Gilbert231-dot/faceless-video-platform`
2. Navigate to **Settings → Secrets and variables → Actions**
3. Click the **Variables** tab
4. Click **New repository variable**
5. Name: `LOCAL_UPLOAD_MODE`
6. Value: `true`
7. Click **Add variable**

This tells GitHub to skip YouTube uploads and save videos as artifacts instead.

### Step 2: Set Up Local Configuration

On your laptop, open Command Prompt and navigate to your project folder:

```bash
cd D:\Desktop\faceless_project\faceless-video-platform
```

Run the setup wizard:

```bash
python local_youtube_upload.py --setup
```

The wizard will ask for:
- **GitHub repo**: `Gilbert231-dot/faceless-video-platform`
- **GitHub token** (optional but recommended): Create at https://github.com/settings/tokens
- **YouTube credentials**: Same ones you use for GitHub Actions
- **Schedule times**: Default is `23:00,03:00,15:00`

### Step 3: Test It

**Dry run** (see what would be uploaded without uploading):

```bash
python local_youtube_upload.py --dry-run
```

**Actual upload**:

```bash
python local_youtube_upload.py
```

### Step 4: Automate with Windows Task Scheduler

To run the upload script automatically after GitHub generates videos:

1. Open **Task Scheduler** (search for it in Windows)
2. Click **Create Basic Task**
3. Name: `YouTube Local Upload`
4. Trigger: **Daily**
5. Time: **10:00 PM** (after GitHub finishes generating at ~9 PM UTC)
6. Action: **Start a program**
7. Program: `python`
8. Arguments: `D:\Desktop\faceless_project\faceless-video-platform\local_youtube_upload.py`
9. Start in: `D:\Desktop\faceless_project\faceless-video-platform`
10. Click **Finish**

## Commands

```bash
# Setup wizard
python local_youtube_upload.py --setup

# Check for new videos (dry run)
python local_youtube_upload.py --dry-run

# Upload videos
python local_youtube_upload.py

# Force re-upload (even if already uploaded)
python local_youtube_upload.py --force

# Check more recent runs
python local_youtube_upload.py --limit 5
```

## Configuration File

Your settings are saved in `local_config.json`:

```json
{
  "github_repo": "Gilbert231-dot/faceless-video-platform",
  "github_token": "ghp_xxxxx",
  "youtube_client_id": "xxxxx.apps.googleusercontent.com",
  "youtube_client_secret": "xxxxx",
  "youtube_refresh_token": "1//xxxxx",
  "youtube_privacy": "public",
  "schedule_times": ["23:00", "03:00", "15:00"]
}
```

## Troubleshooting

### "No completed runs found"
- Make sure `LOCAL_UPLOAD_MODE` is set to `true` on GitHub
- Wait for a workflow run to complete (runs daily at 5 PM UTC)
- Check your GitHub token has `repo` scope

### "YouTube credentials not configured"
- Run: `python local_youtube_upload.py --setup`
- Or set environment variables: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`

### "Upload failed"
- Check your refresh token is still valid (regenerate at https://developers.google.com/oauthplayground)
- Make sure you're connected to the internet
- Try uploading one video manually first

### Videos not showing up on YouTube
- Check YouTube Studio for processing status
- Videos may take 5-10 minutes to process
- Make sure video format is H.264 High with AAC audio

## Disabling Local Upload Mode

To go back to uploading from GitHub Actions:

1. Go to GitHub repo Settings → Secrets and variables → Actions
2. Delete the `LOCAL_UPLOAD_MODE` variable (or set it to `false`)

## Notes

- Artifacts are retained for **7 days** — make sure to upload within that window
- The script tracks uploaded videos in `local_uploaded_ids.json` to avoid duplicates
- Schedule times are in **UTC** — convert to your local time
- Your laptop needs to be **on and connected** for uploads to happen
