#!/usr/bin/env bash
#
# push_pipeline_state.sh -- commit the pipeline's state files onto main and
# push them, reapplying onto whatever main holds by the time we get there.
#
# WHY THIS EXISTS
# ---------------
# The state push used to be three lines:
#
#     git commit -m "..." 2>/dev/null || echo "Nothing new to commit"
#     git pull --rebase origin main 2>/dev/null || true
#     git push origin main || echo "State push failed"
#
# Every way that can fail, failed silently.  On 2026-09-22 the daily
# performance tracker committed to main while Batch 3 was rendering, so its
# push was rejected twice -- once "(fetch first)", once "(non-fast-forward)".
# The rebase was silenced with 2>/dev/null || true, the push failure was
# swallowed, the run went green, and the state was thrown away with the
# ephemeral runner:
#
#   * Batch 3's used-story mark (1kodd4f) never reached main, so that story can
#     be picked and published a second time.
#   * Its rotation memory never reached main, so Batch 4 read stale state,
#     rotated back onto the same background file and re-picked the same window
#     -- the near-duplicate signature the rotation work exists to remove.
#
# WHAT THIS DOES INSTEAD
# ----------------------
# Loops, and on each pass:
#
#   1. fetch the remote
#   2. fold the remote's copy of each state file into ours (state_merge.py), so
#      the other writer's records survive alongside ours and vice versa
#   3. `git reset <remote>` -- rebuild the index from the remote tip, so the
#      commit we are about to make contains ONLY our files, on top of whatever
#      main holds now.  (This is why the script never rebases: a rebase can
#      conflict on a state file, and "resolve it somehow" is how marks get lost.)
#   4. add our files, commit, push
#
# If the push is rejected anyway (main moved again mid-flight) the loop just
# refetches and redoes it.  If every attempt fails the script says so LOUDLY:
# a warning annotation, a step summary naming the files and the state at risk,
# and -- unless --soft-fail was passed -- a non-zero exit so the run goes red
# instead of reporting a success that quietly lost data.
#
# USAGE
# -----
#     push_pipeline_state.sh [--soft-fail] "<commit message>" <path> [<path>...]
#
#   --soft-fail  warn and exit 0 when the push cannot be made.  Used for the
#                EARLY push (its job is best-effort insurance; failing the step
#                there would skip the whole render/upload chain, because the
#                steps after it are gated on success()).  The LATE push runs
#                without this flag, so a real loss turns the run red.
#
# ENVIRONMENT
# -----------
#   STATE_REMOTE     remote to push to             (default: origin)
#   STATE_BRANCH     branch to push to             (default: main)
#   STATE_ATTEMPTS   attempts before giving up     (default: 6)

set -uo pipefail

SOFT_FAIL=0
if [ "${1:-}" = "--soft-fail" ]; then
  SOFT_FAIL=1
  shift
fi

MSG="${1:-}"
if [ -n "$MSG" ]; then shift; fi
PATHS=("$@")

if [ -z "$MSG" ] || [ "${#PATHS[@]}" -eq 0 ]; then
  echo "usage: push_pipeline_state.sh [--soft-fail] <commit message> <path> [<path>...]" >&2
  exit 2
fi

REMOTE="${STATE_REMOTE:-origin}"
BRANCH="${STATE_BRANCH:-main}"
ATTEMPTS="${STATE_ATTEMPTS:-6}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MERGER="$SCRIPT_DIR/state_merge.py"

git config user.name "github-actions[bot]" >/dev/null 2>&1 || true
git config user.email "github-actions[bot]@users.noreply.github.com" >/dev/null 2>&1 || true

# Fold main's version of each state file into ours.  Only JSON files are
# offered to the merger; directories (reddit_stories/) are staged as they are.
fold_in_remote_state() {
  local p tmp
  for p in "${PATHS[@]}"; do
    case "$p" in
      *.json) ;;
      *) continue ;;
    esac
    [ -f "$p" ] || continue
    tmp="$(mktemp)"
    if git show "$REMOTE/$BRANCH:$p" >"$tmp" 2>/dev/null && [ -s "$tmp" ]; then
      if command -v python >/dev/null 2>&1; then
        python "$MERGER" "$p" "$tmp" 2>&1 | sed 's/^/   /'
      fi
    fi
    rm -f "$tmp"
  done
}

stage_ours() {
  local p
  for p in "${PATHS[@]}"; do
    git add -f -- "$p" >/dev/null 2>&1 || true
  done
}

changed_files() {
  git diff --cached --name-only -- "${PATHS[@]}" 2>/dev/null || true
}

# On a real loss, dump the small state files into the run summary so their
# contents can be recovered by hand from the log instead of vanishing with it.
dump_small_state() {
  local p size
  for p in "${PATHS[@]}"; do
    case "$p" in
      *.json) ;;
      *) continue ;;
    esac
    [ -f "$p" ] || continue
    size=$(wc -c <"$p" 2>/dev/null | tr -d ' ')
    [ -n "$size" ] && [ "$size" -le 8000 ] || continue
    echo "<details><summary>$p</summary>"
    echo
    echo '```json'
    cat "$p"
    echo '```'
    echo "</details>"
  done
}

PUSHED=0
ATTEMPT=0
while [ "$ATTEMPT" -lt "$ATTEMPTS" ]; do
  ATTEMPT=$((ATTEMPT + 1))
  echo "── state push attempt $ATTEMPT/$ATTEMPTS ($REMOTE/$BRANCH) ──"

  if ! git fetch --quiet "$REMOTE" "$BRANCH" 2>/dev/null; then
    echo "   ⚠️ fetch failed (network?) — will retry"
    sleep $((ATTEMPT * 5))
    continue
  fi

  fold_in_remote_state

  # Rebuild the index from the remote tip.  --mixed (the default) leaves the
  # working tree alone, so our freshly written state files are still on disk to
  # be staged in a moment -- but anything the OTHER writer changed is inherited
  # instead of being reverted by a stale index.
  if ! git reset --quiet "$REMOTE/$BRANCH" 2>/dev/null; then
    echo "   ⚠️ could not reset onto $REMOTE/$BRANCH — will retry"
    sleep $((ATTEMPT * 5))
    continue
  fi

  stage_ours

  if [ -z "$(changed_files)" ]; then
    echo "ℹ️ nothing new to commit — main already holds this state"
    PUSHED=1
    break
  fi

  echo "   committing: $(changed_files | tr '\n' ' ')"
  if ! git commit --quiet -m "$MSG"; then
    echo "   ⚠️ commit failed — will retry"
    sleep $((ATTEMPT * 5))
    continue
  fi

  if git push --quiet "$REMOTE" "HEAD:refs/heads/$BRANCH" 2>/dev/null; then
    echo "✅ state pushed to $REMOTE/$BRANCH as $(git rev-parse --short HEAD)"
    PUSHED=1
    break
  fi

  echo "   ⚠️ push rejected — main moved underneath us; refetching and reapplying"
  sleep $((ATTEMPT * 5))
done

if [ "$PUSHED" = "1" ]; then
  exit 0
fi

echo "❌ Pipeline state was NOT pushed after $ATTEMPTS attempts."
{
  echo "### ⚠️ Pipeline state was NOT pushed to $REMOTE/$BRANCH"
  echo
  echo "\`$MSG\` failed $ATTEMPTS times. The files below were written on the"
  echo "runner and did **not** reach main, so the next run will not see them:"
  echo
  for p in "${PATHS[@]}"; do echo "- \`$p\`"; done
  echo
  echo "Consequences: a used-story mark that did not land means that story can be"
  echo "picked and published again; rotation memory that did not land means the"
  echo "next batch can reuse the previous video's background window."
  echo
  echo "Their contents at the moment of failure (the small ones):"
  echo
  dump_small_state
} >>"$SUMMARY"

if [ "$SOFT_FAIL" = "1" ]; then
  echo "::warning title=Pipeline state push failed::$MSG did not reach $BRANCH after $ATTEMPTS attempts (early push — continuing, the final push will retry)"
  exit 0
fi

echo "::error title=Pipeline state push failed::$MSG did not reach $BRANCH after $ATTEMPTS attempts"
exit 1
