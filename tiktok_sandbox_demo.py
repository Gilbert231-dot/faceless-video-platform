"""TikTok app-review demo helper — sandbox posting flow for the review video.

TikTok's App Review form requires (for first-time apps) a screen recording
that demonstrates the integration in the SANDBOX environment, showing the
website UI and the posting flow end to end. This script drives that flow
and tells you exactly when to be recording.

The recording should cover, in one take:
  1. Open the website  https://gilbert231-dot.github.io/faceless-video-platform/
  2. Sandbox OAuth: log in with the sandbox test account and approve the app
  3. Post the demo clip via the Content Posting API (private, is_aigc on)
  4. Show the private post in the sandbox account

Usage:
    TIKTOK_SANDBOX_CLIENT_KEY=... TIKTOK_SANDBOX_CLIENT_SECRET=... \\
        python tiktok_sandbox_demo.py --run [--video path/to/clip.mp4]

    (no --run)  -> prints the recording checklist only (nothing is executed)
    --run       -> executes the flow, prompting you between stages
    --video     -> clip to post (default: assets/demo/tiktok_demo_clip.mp4)

Notes:
  - The sandbox client key/secret come from the TikTok developer portal
    (app -> Sandbox tab). They are NOT the production credentials.
  - The refresh token printed during setup is a sandbox token — keep it
    out of any public file.
"""

import os
import sys

DEFAULT_VIDEO = os.path.join("assets", "demo", "tiktok_demo_clip.mp4")


def checklist():
    print("=" * 72)
    print("RECORDING CHECKLIST — start your screen recorder, then:")
    print("=" * 72)
    print(" 1. Open https://gilbert231-dot.github.io/faceless-video-platform/")
    print("    and scroll the landing page (show the demo video and the")
    print("    Live Demo link).")
    print(" 2. Run this script with --run. The browser opens TikTok's")
    print("    authorization page — log in with the SANDBOX test account")
    print("    and click Allow.")
    print(" 3. The script posts the demo clip privately via the Content")
    print("    Posting API (is_aigc on) — keep the log on screen.")
    print(" 4. Open the sandbox account's profile and show the private post.")
    print(" 5. Stop recording, export as .mp4/.mov under 50 MB, upload in")
    print("    the App review form.")
    print("=" * 72)


def main():
    run = "--run" in sys.argv
    video = DEFAULT_VIDEO
    if "--video" in sys.argv:
        idx = sys.argv.index("--video")
        if idx + 1 < len(sys.argv):
            video = sys.argv[idx + 1]

    checklist()
    if not run:
        print("\n(no --run given — nothing was executed.)")
        return

    key = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY") or os.environ.get("TIKTOK_CLIENT_KEY")
    secret = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET") or os.environ.get("TIKTOK_CLIENT_SECRET")
    if not key or not secret:
        print("\n❌ Set TIKTOK_SANDBOX_CLIENT_KEY and TIKTOK_SANDBOX_CLIENT_SECRET")
        print("   (sandbox credentials from the TikTok developer portal).")
        sys.exit(1)

    # Set env BEFORE importing tiktok_setup — it reads these at import time.
    os.environ["TIKTOK_CLIENT_KEY"] = key
    os.environ["TIKTOK_CLIENT_SECRET"] = secret

    input("\n→ Step 2: press Enter to open the sandbox OAuth page (record this).")
    from tiktok_setup import main as setup_main
    setup_main()

    input("\n→ Step 3: press Enter to post the demo clip via the Content Posting API (record this).")
    sys.argv = ["tiktok_demo.py"] + ([video] if video else [])
    from tiktok_demo import main as demo_main
    demo_main()

    print("\n→ Step 4: open the sandbox account's profile and show the private post,")
    print("  then stop the recording. Export as .mp4/.mov under 50 MB and upload")
    print("  it in the App review form.")


if __name__ == "__main__":
    main()