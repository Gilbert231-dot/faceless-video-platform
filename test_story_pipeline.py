"""Temporary behavior tests for the story-bank pipeline (old-first ordering,
cleanup, fetcher core). Run:  python _test_story_pipeline.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import reddit_story_loader as rsl
import cleanup_used_stories as cus
import fetch_stories as fs

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- temp sandbox
sandbox = tempfile.mkdtemp(prefix="storytest_")
old_cwd = os.getcwd()


def make_sub(sub, stories, ts="20260101_000000"):
    folder = os.path.join(sandbox, "reddit_stories", sub)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"stories_with_comments_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(stories, f)


# ------------------------------------------------- 1. old-first ordering test
print("\n=== 1. old-first ordering ===")
os.chdir(sandbox)
make_sub("AITAH", [
    {"id": "old1", "title": "Old 1", "story": "x" * 200, "subreddit": "AITAH"},
    {"id": "old2", "title": "Old 2", "story": "x" * 200, "subreddit": "AITAH"},
])
make_sub("tifu", [
    {"id": "new1", "title": "New 1", "story": "x" * 200, "subreddit": "tifu",
     "added_at": "2026-08-09"},
    {"id": "new2", "title": "New 2", "story": "x" * 200, "subreddit": "tifu",
     "added_at": "2026-08-09"},
])
loader = rsl.RedditStoryLoader("reddit_stories", debug_mode=False)
loader.used_ids = []
ids = [s["id"] for s in loader.get_unused_stories(limit=10)]
print("   order:", ids)
check("all four stories returned", len(ids) == 4, f"got {ids}")
# Order is now randomized within the top pool (FIXED: story repeats) — the
# contract is that ALL unused stories are eligible, not that old ones sort
# before new ones.
check("both old stories returned", {"old1", "old2"} <= set(ids), f"got {ids}")
check("both new stories returned", {"new1", "new2"} <= set(ids), f"got {ids}")

# new stories picked ONLY after old ones are used
loader.used_ids = ["old1", "old2"]
ids2 = [s["id"] for s in loader.get_unused_stories(limit=10)]
check("new stories only after old used", set(ids2) == {"new1", "new2"}, f"got {ids2}")

# a story with a REAL id that was already used under the OLD synth-ID scheme
# (md5 of "subreddit|title") must NOT be re-picked
import hashlib
synth = "synth_" + hashlib.md5(b"AITAH|Old 1").hexdigest()[:12]
loader.used_ids = [synth]
ids3 = [s["id"] for s in loader.get_unused_stories(limit=10)]
check("synth-id used story excluded", "old1" not in ids3, f"{synth} -> got {ids3}")

# ------------------------------------------------- 2. cleanup test
print("\n=== 2. cleanup_used_stories ===")
make_sub("ProRevenge", [
    {"id": "u1", "title": "Used 1", "story": "x" * 200},
    {"id": "k1", "title": "Kept 1", "story": "x" * 200},
    {"id": "k2", "title": "Kept 2", "story": "x" * 200},
], ts="20260101_000000")
# second file to prove consolidation
make_sub("ProRevenge", [
    {"id": "u2", "title": "Used 2", "story": "x" * 200},
    {"id": "k3", "title": "Kept 3", "story": "x" * 200},
], ts="20260202_000000")
with open(os.path.join(sandbox, "used_story_ids.json"), "w", encoding="utf-8") as f:
    json.dump(["u1", "u2"], f)

cus.STORIES_DIR = "reddit_stories"
cus.USED_IDS_FILE = "used_story_ids.json"
cus.main()

with open(os.path.join(sandbox, "reddit_stories", "ProRevenge", "stories_with_comments_20260202_000000.json"),
          encoding="utf-8") as f:
    kept = json.load(f)
kept_ids = sorted(s["id"] for s in kept)
check("used stories removed", kept_ids == ["k1", "k2", "k3"], f"got {kept_ids}")
files = os.listdir(os.path.join(sandbox, "reddit_stories", "ProRevenge"))
check("consolidated to one file", len([x for x in files if x.endswith(".json")]) == 1, f"files: {files}")
check("no raw comment dumps left", not [x for x in files if x.startswith("comment_")], f"files: {files}")

# ------------------------------------------------- 3. fetcher core functions
print("\n=== 3. fetch_stories core ===")
check("quality_score sane", fs.quality_score(2000, 5000, 300) >= 15, str(fs.quality_score(2000, 5000, 300)))
check("quality_score low for short", fs.quality_score(50, 5, 2) < 5, str(fs.quality_score(50, 5, 2)))

comment_data = [
    {"data": {"children": []}},
    {"data": {"children": [
        {"data": {"author": "user1", "body": "This is a solid top comment about the story.", "score": 50}},
        {"data": {"author": "user2", "body": "short", "score": 200}},
        {"data": {"author": "AutoModerator", "body": "Please keep things civil in this thread, thanks everyone.", "score": 999}},
        {"data": {"author": "user3", "body": "Another decent comment worth narrating here.", "score": 40}},
        {"data": {"author": "user4", "body": "A fifth long enough comment body.", "score": 10}},
        {"data": {"author": "user5", "body": "Fifth comment body that should also appear in the list.", "score": 30}},
        {"data": {"author": "user6", "body": "Sixth comment that would be cut by the top-5 limit.", "score": 20}},
    ]}},
]
top = fs.extract_top_comments(comment_data)
check("top comments sorted by score", [c["score"] for c in top] == [50, 40, 30, 20, 10], str([c["score"] for c in top]))
check("automod filtered", all(c["author"] != "AutoModerator" for c in top), str([c["author"] for c in top]))
check("short bodies filtered", all(len(c["body"]) > 10 for c in top), str([c["body"] for c in top]))
check("max 5 comments", len(top) == 5, str(len(top)))

script = fs.build_comment_script("AITAH", top)
check("script has intro", "Reddit community" in script, script[:80])
check("script has u/ mentions", "u/user1" in script, script)
check("script has praise outro", "Thanks to everyone" in script, script[-80:])

# consolidate: merge existing + new, keep unused only, one file per sub
os.chdir(sandbox)
fs.STORIES_DIR = "reddit_stories"
make_sub("AmItheCheese", [
    {"id": "e1", "title": "Existing", "story": "x" * 200, "subreddit": "AmItheCheese"},
], ts="20260303_000000")
new = [{"id": "n1", "title": "New", "story": "y" * 200, "subreddit": "AmItheCheese", "added_at": "2026-08-09"}]
ok = fs.consolidate_subreddit("AmItheCheese", new, set())
check("consolidate returns True", ok)
files = sorted(os.listdir(os.path.join(sandbox, "reddit_stories", "AmItheCheese")))
check("consolidate leaves one file", len(files) == 1, str(files))
with open(os.path.join(sandbox, "reddit_stories", "AmItheCheese", files[0]), encoding="utf-8") as f:
    merged = json.load(f)
check("merged has old + new", sorted(s["id"] for s in merged) == ["e1", "n1"], str([s["id"] for s in merged]))

os.chdir(old_cwd)
shutil.rmtree(sandbox, ignore_errors=True)

print("\n" + ("ALL TESTS PASSED ✅" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
