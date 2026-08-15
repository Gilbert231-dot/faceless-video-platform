# 🪫 Story bank auto-refill (`fetch_stories.py`)

The pipeline now reads stories straight from `reddit_stories/` in the repo (no more
`reddit_stories.zip`). The bank holds ~360 stories; at 2 videos/day that's ~6 months of
runway, so this refill runs **weekly** — it only does real work when the bank actually
runs low.

## How it works

1. **Counts unused stories** (total minus `used_story_ids.json`).
2. If unused ≥ **100** (`MIN_UNUSED`), it prints "plenty left" and exits — a scheduled run
   costs nothing when the bank is healthy.
3. If unused < 100, it fetches every subreddit's **controversial (this week) + hot**
   listings + each new story's comments `.json` from **your laptop's residential IP**
   (Reddit blocks GitHub's datacenter IPs — that's why this can't run inside the
   workflow). Controversial-first, hot-by-engagement fallback; both deduped by post id.
4. Builds the exact story format the pipeline consumes (`top_comments`, `comment_script`,
   `quality_score`), **tags new stories with `added_at=today`** so the pipeline picks OLD
   stories first, consolidates each subreddit into one file, then commits + pushes.

The workflow itself also stays lean: every run's generation marks used stories, then
`cleanup_used_stories.py` (in the workflow) removes used stories from the bank and drops
the legacy raw `comment_*.json` dumps — so the folder never crowds.

## One-time setup (about 2 minutes)

```bash
cd D:\Desktop\faceless_project
python -m pip install requests
python fetch_stories.py        # manual run — should print "Plenty of stories left"
```

If Reddit blocks your IP for `.json` scraping (403), drop a browser cookies export at
`reddit_cookies.txt` (Netscape format — "Get cookies.txt LOCALLY" extension, after
passing Reddit's "verify you're human" check) and the script rides that browser session
past the block. Or run from a different network.

Optional env overrides:
- `MIN_UNUSED=100` — refill when unused stories fall below this (default 100).
- `SUBREDDITS=AITAH,tifu` — only fetch these subreddits.
- `COOKIES_FILE=path` — where to read the browser cookies export.

## Schedule it weekly (Windows Task Scheduler)

1. Press **Win+R** → type `taskschd.msc` → Enter.
2. Right pane → **Create Basic Task**:
   - **Name:** `Refill story bank` → Next.
   - **Trigger:** *Weekly*, pick e.g. **Sunday 09:00** → Next.
   - **Action:** *Start a program* → Next.
   - **Program/script:** `python`
   - **Add arguments:** `fetch_stories.py`
   - **Start in:** `D:\Desktop\faceless_project`
   - → Finish.
3. (Recommended) Right-click the task → **Properties** → **Settings** tab → tick
   *Run task as soon as possible after a scheduled start is missed* (covers laptop-off
   times).
4. **Keep the laptop on / logged in at that time** — the task only runs when Windows is
   awake. If it misses, tick the "run as soon as possible" box and it catches up on the
   next boot.

## Picking order (old stories first)

- Stories **without** `added_at` (the current ~360) are picked first, randomly within that
  group.
- Stories **with** `added_at` (added by a refill) are only picked after every older story
  is used — so a refill never changes what you're currently watching.
- The pipeline logs a **loud warning** when unused stories drop to ≤ 20, so you'll see it
  in the run log if a refill is overdue.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `❌ 'requests' is not installed` | `python -m pip install requests` |
| `⚠️ git push failed` | Stories are saved locally; run `git push` from `D:\Desktop\faceless_project` |
| `⚠️ git pull` failed (non-fast-forward) | The runner pushed state first; re-run `python fetch_stories.py` |
| Reddit returns 403/429 repeatedly | Reddit is throttling; wait and re-run (the script retries 4× with backoff) |
| Workflow says "No story files found" | Story JSONs didn't get committed — run `fetch_stories.py` manually once |

Run the behavior tests anytime: `python test_story_pipeline.py`
