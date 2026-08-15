"""
fetch_stories.py — top up the story bank from YOUR laptop (residential IP).

Why local? Reddit blocks GitHub/AWS datacenter IPs, so the pipeline itself
can never fetch Reddit's .json endpoints — but your home IP can (that's why
you've been doing it by hand in the browser). This script automates the whole
manual flow:

  1. Pulls the latest repo state.
  2. Counts UNUSED stories (vs used_story_ids.json).
  3. If unused >= MIN_UNUSED, prints "plenty left" and exits — so you can run
     it on a schedule and it only does work when the bank actually runs low.
  4. Otherwise fetches each subreddit's listings, then each NEW story's
     comments .json, builds the exact story format the pipeline consumes
     (top_comments + comment_script + quality_score), consolidates each
     subreddit's stories into one stories_with_comments_<ts>.json (old +
     new), tags new stories with added_at=today so the pipeline picks OLD
     stories first, then commits + pushes.

Story selection (see fetch_listings):
  * Most subreddits fetch CONTROVERSIAL (this week) FIRST — the most
    hotly-debated posts, which get people arguing in the comments and
    engaging with the videos. If controversy yields nothing usable, we
    fall back to HOT stories sorted by engagement (most comments, then
    highest score) so the bank still gets the posts people engaged with.
  * r/relationship_advice is the exception: its drama is in the story
    itself, so it takes ALL usable hot stories.

Run manually:   python fetch_stories.py
Schedule:       weekly via Windows Task Scheduler (see STORY_REFILL.md).

Optional env overrides:
  MIN_UNUSED=20        refill when unused stories fall below this
  SUBREDDITS=AITAH,tifu  only fetch these subreddits
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
import datetime

try:
    import requests
except ImportError:
    print("❌ 'requests' is not installed. Run:  python -m pip install requests")
    sys.exit(1)

# Windows consoles default to cp1252, which can't encode the emoji in our
# prints (✅ ⏳ 📊 ...) and would crash the refill mid-run. Force UTF-8 out.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO = os.path.dirname(os.path.abspath(__file__))
STORIES_DIR = "reddit_stories"
USED_IDS_FILE = "used_story_ids.json"

MIN_UNUSED = int(os.environ.get("MIN_UNUSED", "20"))
MIN_BODY = 100          # ignore posts with a body shorter than this
TOP_COMMENTS = 5        # comments per story, by score
HOT_LIMIT = 50
COMMENT_LIMIT = 30
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Same narrator intro/outro lines the old process_comments.py used, so the
# comment_script style stays consistent across the whole story bank.
SUBREDDIT_VIBES = {
    "AITAH": ("Let's see what the Reddit community had to say...",
              "Thanks to everyone who shared their thoughts!"),
    "AmItheAsshole": ("The people of Reddit had some strong opinions on this one...",
                      "A huge thank you to everyone who commented!"),
    "TrueOffMyChest": ("The community had some heartfelt responses...",
                       "Thank you to everyone who shared their support!"),
    "tifu": ("Reddit had some hilarious reactions to this one...",
             "Thanks to everyone who chimed in!"),
    "relationship_advice": ("The relationship experts of Reddit weighed in...",
                            "A big thank you to everyone who offered advice!"),
    "MaliciousCompliance": ("Reddit loved this satisfying story...",
                            "Thanks to all the commenters for their input!"),
    "ProRevenge": ("The Reddit community was fully on board with this revenge...",
                   "Thanks to everyone for their support!"),
    "pettyrevenge": ("Reddit enjoyed this petty revenge story...",
                     "Thanks to the commenters for their thoughts!"),
    "TalesFromTheFrontDesk": ("The front desk workers of Reddit had some stories to tell...",
                              "Thanks to all the hospitality workers who shared their experiences!"),
    "TalesFromRetail": ("Retail workers of Reddit shared their thoughts on this one...",
                        "Thanks to everyone who's ever worked a retail job!"),
    "confession": ("Redditors had some honest confessions about this...",
                   "Thanks to everyone who shared their truth!"),
    "self": ("The Reddit community shared their personal perspectives...",
             "Thanks to everyone who opened up about this!"),
    "offmychest": ("Redditors got things off their chest about this one...",
                   "Thanks to everyone who shared their feelings!"),
    "unpopularopinion": ("Reddit had some spicy hot takes on this...",
                         "Thanks to everyone who shared their (unpopular) opinions!"),
    "EntitledPeople": ("Reddit shared some wild entitled people stories...",
                       "Thanks to everyone who shared their encounters with entitlement!"),
}
DEFAULT_VIBE = ("Here are the top comments from Reddit...",
                "Thanks to everyone who commented!")


def subreddits():
    env = os.environ.get("SUBREDDITS")
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    return [d for d in sorted(os.listdir(STORIES_DIR))
            if os.path.isdir(os.path.join(STORIES_DIR, d))]


def load_used_ids():
    try:
        with open(USED_IDS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def fetch_listings(sub):
    """Candidates for one subreddit: controversial first, hot fallback.

    Reddit's .json endpoints accept a listing kind (hot/controversial/top...)
    plus a time filter for controversial/top. Most subs get BOTH:

      1. controversial (t=week) — the most-debated posts, exactly what gets
         people arguing in the comments (and engaging with our videos).
      2. hot — the fallback, sorted by engagement (comments, then score) so
         that even when controversy yields nothing, we still pull the posts
         people engaged with most.

    r/relationship_advice is the exception: its drama is in the story
    itself, so we take ALL usable hot stories instead of only the
    controversial ones.

    Returns deduped post dicts (id-keyed), tagged with _source, ordered
    controversial-first then by most comments / highest score.
    """
    if sub == "relationship_advice":
        urls = [("hot", f"https://www.reddit.com/r/{sub}/hot.json?limit={HOT_LIMIT}")]
    else:
        urls = [
            ("controversial", f"https://www.reddit.com/r/{sub}/controversial.json?t=week&limit={HOT_LIMIT}"),
            ("hot", f"https://www.reddit.com/r/{sub}/hot.json?limit={HOT_LIMIT}"),
        ]
    posts, seen = [], set()
    for label, url in urls:
        data = fetch_json(url)
        if not data:
            continue
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            pid = p.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            p["_source"] = label
            posts.append(p)
    # Controversial first; within a group, most comments, then highest score.
    posts.sort(key=lambda p: (0 if p.get("_source") == "controversial" else 1,
                              -p.get("num_comments", 0), -p.get("score", 0)))
    return posts


def fetch_json(url):
    """GET a Reddit .json endpoint with retries on 429/403."""
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        except Exception as e:
            print(f"   ⚠️ Request failed: {e}")
            time.sleep(5)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 403) and attempt < 3:
            wait = 8 * (attempt + 1)
            print(f"   ⏳ HTTP {r.status_code} on {url} — retrying in {wait}s")
            time.sleep(wait)
            continue
        print(f"   ⚠️ HTTP {r.status_code} on {url}")
        return None
    return None


def extract_top_comments(comment_data):
    """Mirror process_comments.extract_top_comments: top N comments by score."""
    comments = []
    try:
        if comment_data and len(comment_data) > 1:
            for child in comment_data[1]["data"]["children"]:
                c = child.get("data", {})
                author = c.get("author", "[deleted]")
                body = c.get("body", "")
                if body and author not in ("[deleted]", "AutoModerator") and len(body) > 10:
                    comments.append({"author": author, "body": body.strip(), "score": c.get("score", 0)})
    except Exception:
        pass
    comments.sort(key=lambda x: x["score"], reverse=True)
    return comments[:TOP_COMMENTS]


def build_comment_script(subreddit, top_comments):
    """Same narrator format as process_comments.format_comments_for_narrator."""
    intro, praise = SUBREDDIT_VIBES.get(subreddit, DEFAULT_VIBE)
    lines = [intro]
    for i, c in enumerate(top_comments, 1):
        body = re.sub(r"[^\w\s.,!?'\"]", "", c.get("body", ""))
        body = body[:200] + "..." if len(body) > 200 else body
        lines.append(f"Comment {i} from u/{c.get('author', 'a Redditor')}: '{body}'")
    lines.append(praise)
    return "\n\n".join(lines)


def quality_score(story_len, score, num_comments):
    length = min(story_len / 500, 1.0) * 10
    votes = min(score / 1000, 1.0) * 10
    comm = min(num_comments / 100, 1.0) * 5
    return round(length + votes + comm, 1)


def existing_stories(sub):
    """All stories currently in this subreddit's files (used ones excluded)."""
    folder = os.path.join(STORIES_DIR, sub)
    stories, seen = [], set()
    for f in (glob.glob(os.path.join(folder, "stories_with_comments_*.json"))
              + glob.glob(os.path.join(folder, "stories_*.json"))):
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
            stories.append(s)
    return stories


def consolidate_subreddit(sub, new_stories, used_ids):
    """Merge existing (unused) + new stories into ONE new file per subreddit
    and delete the older story files so the folder never crowds."""
    folder = os.path.join(STORIES_DIR, sub)
    existing = [s for s in existing_stories(sub)
                if (s.get("story_id") or s.get("id")) not in used_ids]
    all_stories = existing + new_stories
    if not all_stories:
        return False
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(folder, f"stories_with_comments_{ts}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_stories, f, indent=2, ensure_ascii=False)
    for f in (glob.glob(os.path.join(folder, "stories_with_comments_*.json"))
              + glob.glob(os.path.join(folder, "stories_*.json"))):
        if f != out:
            try:
                os.remove(f)
            except OSError:
                pass
    print(f"   ✅ r/{sub}: {len(all_stories)} stories total ({len(new_stories)} new) -> {os.path.basename(out)}")
    return True


def count_unused(used_ids):
    total, unused = 0, 0
    for sub in subreddits():
        for s in existing_stories(sub):
            total += 1
            if (s.get("story_id") or s.get("id")) not in used_ids:
                unused += 1
    return total, unused


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main():
    os.chdir(REPO)
    code, out = git("pull", "--ff-only")
    if code != 0:
        print(f"⚠️ git pull: {out}")

    used_ids = load_used_ids()
    total, unused = count_unused(used_ids)
    print(f"📊 Story bank: {unused} unused / {total} total (used: {len(used_ids)})")

    if unused >= MIN_UNUSED:
        print(f"✅ Plenty of stories left (threshold {MIN_UNUSED}) — nothing to fetch.")
        return

    print(f"⚠️ Below threshold ({MIN_UNUSED}) — fetching fresh stories...\n")
    new_total = 0
    for sub in subreddits():
        try:
            folder = os.path.join(STORIES_DIR, sub)
            os.makedirs(folder, exist_ok=True)
            seen_ids = {(s.get("story_id") or s.get("id")) for s in existing_stories(sub)}
            candidates = fetch_listings(sub)
            if not candidates:
                continue
            new = []
            for p in candidates:
                body = p.get("selftext") or ""
                if len(body) < MIN_BODY:
                    continue
                sid = p.get("id", "")
                if not sid or sid in used_ids or sid in seen_ids:
                    continue
                time.sleep(1.2)  # be polite to Reddit
                comments = fetch_json(
                    f"https://www.reddit.com/r/{sub}/comments/{sid}.json?limit={COMMENT_LIMIT}")
                top = extract_top_comments(comments)
                story = {
                    "id": sid,
                    "title": p.get("title", ""),
                    "story": body,
                    "score": p.get("score", 0),
                    "author": p.get("author", "unknown"),
                    "comments": p.get("num_comments", 0),
                    "subreddit": sub,
                    "url": "https://reddit.com" + (p.get("permalink") or ""),
                    "quality_score": quality_score(len(body), p.get("score", 0), p.get("num_comments", 0)),
                    "top_comments": top,
                    "comment_script": build_comment_script(sub, top),
                    "has_comments": bool(top),
                    "source": p.get("_source", "hot"),
                    # New stories are tagged with today's date so the pipeline
                    # sorts them LAST (old stories get picked first).
                    "added_at": datetime.date.today().isoformat(),
                }
                new.append(story)
                print(f"   🆕 r/{sub}: {sid} — {story['title'][:60]}")
            if new:
                if consolidate_subreddit(sub, new, used_ids):
                    new_total += len(new)
            time.sleep(1.5)
        except Exception as e:
            print(f"   ⚠️ r/{sub} failed: {e}")

    if not new_total:
        print("\nℹ️ No new stories found (Reddit throttling? all subreddits empty?). Try again later.")
        return

    print(f"\n📦 Committing {new_total} new stories...")
    code, out = git("add", "reddit_stories/")
    if code != 0:
        print(f"⚠️ git add: {out}")
    code, out = git("commit", "-m", f"Auto-refill story bank (+{new_total} stories)")
    print(out if out else "(nothing to commit)")
    code, out = git("push")
    if code != 0:
        print(f"⚠️ git push failed: {out}")
        print("   The stories are saved locally — run 'git push' when you can.")
    else:
        print("✅ Pushed! The pipeline will see the fresh stories on its next run.")


if __name__ == "__main__":
    main()
