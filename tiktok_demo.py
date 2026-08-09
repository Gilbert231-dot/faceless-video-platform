"""Post one video to TikTok through the REAL uploader — used for the app-review
demo recording (and for any manual single-video posting).

Run on YOUR computer (not in Actions), with the TikTok secrets available:

    TIKTOK_CLIENT_KEY=... TIKTOK_CLIENT_SECRET=... TIKTOK_REFRESH_TOKEN=... \
        python tiktok_demo.py [path/to/video.mp4]

Default video: assets/demo/tiktok_demo_clip.mp4 (the review demo clip).
Posts SELF_ONLY (private) with the AI-content disclosure, exactly like the
workflow does. Record your screen while it runs (OAuth approval from
tiktok_setup.py first, then this) — that recording is the demo video you
upload in the app-review form.
"""
import os
import sys

from tiktok_uploader import build_caption, publish_tiktok


def main():
    video = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "assets", "demo", "tiktok_demo_clip.mp4"
    )
    if not os.path.exists(video):
        print(f"Video not found: {video}")
        sys.exit(1)

    title = "Faceless Video Creator - API demo (private post)"
    print(f"Posting {os.path.basename(video)} to TikTok as SELF_ONLY (private)...")
    result = publish_tiktok(video, title=title, privacy_level="SELF_ONLY")
    print("DONE:", result)
    if result.get("url"):
        print("View it here:", result["url"])


if __name__ == "__main__":
    main()
