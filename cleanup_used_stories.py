"""
cleanup_used_stories.py — keeps reddit_stories/ from crowding.

Runs inside the GitHub workflow AFTER generation (before the state push):
  1. Reads used_story_ids.json (stories already narrated).
  2. Drops any story whose id is in it from every subreddit's story files,
     rewriting each subreddit as ONE consolidated stories_with_comments file
     (the loader only reads the newest file per subreddit anyway).
  3. Deletes the legacy raw comment_*.json dumps the pipeline never reads
     (they used to make the zip 146 MB; nothing in the pipeline touches them).

The daily cron removes each story the day after it was used, so the folder
stays small no matter how many videos are generated.
"""

import glob
import json
import os
import time

STORIES_DIR = "reddit_stories"
USED_IDS_FILE = "used_story_ids.json"


def load_used_ids():
    if os.path.exists(USED_IDS_FILE):
        with open(USED_IDS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def main():
    used = load_used_ids()
    print(f"🧹 Cleanup: {len(used)} used story id(s) to remove")

    removed_total = 0
    subreddits = [d for d in sorted(os.listdir(STORIES_DIR))
                  if os.path.isdir(os.path.join(STORIES_DIR, d))]

    for sub in subreddits:
        folder = os.path.join(STORIES_DIR, sub)

        # Legacy raw comment dumps — dead weight, safe to delete.
        for f in glob.glob(os.path.join(folder, "comment_*.json")):
            try:
                os.remove(f)
            except OSError:
                pass

        story_files = (glob.glob(os.path.join(folder, "stories_with_comments_*.json"))
                       + glob.glob(os.path.join(folder, "stories_*.json")))
        if not story_files:
            continue

        merged, seen, removed = [], set(), 0
        for f in sorted(story_files):
            try:
                data = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            for s in (data if isinstance(data, list) else [data]):
                sid = s.get("story_id") or s.get("id")
                if sid:
                    if sid in seen:
                        continue
                    seen.add(sid)
                    if sid in used:
                        removed += 1
                        continue
                merged.append(s)

        if not merged:
            # Everything in this subreddit was used — drop the files entirely.
            for f in story_files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            removed_total += removed
            print(f"   🗑️ r/{sub}: all {removed} stories used — removed files")
            continue

        target = sorted(story_files)[-1]  # newest name becomes the consolidated file
        with open(target, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        for f in story_files:
            if f != target:
                try:
                    os.remove(f)
                except OSError:
                    pass
        if removed:
            removed_total += removed
            print(f"   🧹 r/{sub}: removed {removed} used stories, {len(merged)} left -> {os.path.basename(target)}")

    print(f"✅ Cleanup done — removed {removed_total} used stories total")


if __name__ == "__main__":
    main()
