// Faceless Video Dashboard — renders data.json + live GitHub Actions status.

const REPO = "Gilbert231-dot/faceless-video-platform";
const GITHUB_API = `https://api.github.com/repos/${REPO}/actions/runs?per_page=5`;

async function loadJSON(url, fallback, attempts = 3) {
    // Retry with backoff: the GitHub API rate-limits unauthenticated IPs, so
    // a single cold fetch can fail while the next one succeeds.
    let lastErr = null;
    for (let i = 0; i < attempts; i++) {
        try {
            const res = await fetch(url, { cache: "no-store" });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (err) {
            lastErr = err;
            console.warn(`Attempt ${i + 1}/${attempts} failed for ${url}:`, err);
            await new Promise(r => setTimeout(r, 700 * (i + 1)));
        }
    }
    return { __error: lastErr ? lastErr.message : "unknown" };
}

function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
}

function renderCards(d) {
    const cards = document.getElementById("stat-cards");
    cards.innerHTML = "";
    const pct = d.total_stories ? Math.round((d.unused_stories / d.total_stories) * 100) : 0;

    const defs = [
        { label: "Total stories", value: d.total_stories, cls: "", sub: `${d.subreddits.length} subreddits` },
        { label: "Unused", value: d.unused_stories, cls: "green", sub: "ready to narrate" },
        { label: "Used", value: d.used_stories, cls: "blue", sub: "already narrated" },
        { label: "Remaining", value: `${pct}%`, cls: pct <= 20 ? "red" : "green", sub: "of the bank" },
        { label: "Story runway", value: d.days_of_stories != null ? `~${d.days_of_stories}d` : "—",
          cls: d.days_of_stories != null && d.days_of_stories <= 30 ? "red" : "green",
          sub: d.runway_date ? `at ${d.videos_per_day || 2}/day, dry ${new Date(d.runway_date).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`
                             : `at ${d.videos_per_day || 2} videos/day` },
    ];

    for (const def of defs) {
        const card = el("div", "card");
        card.appendChild(el("div", "label", def.label));
        card.appendChild(el("div", `value ${def.cls}`, String(def.value)));
        card.appendChild(el("div", "sub", def.sub));
        cards.appendChild(card);
    }
}

function renderBars(d) {
    const box = document.getElementById("subreddit-bars");
    box.innerHTML = "";
    if (!d.subreddits.length) {
        box.appendChild(el("p", "empty", "No stories in the bank yet."));
        return;
    }
    const max = Math.max(...d.subreddits.map(s => s.unused), 1);
    for (const s of d.subreddits) {
        const row = el("div", "bar-row");
        const label = el("div", "bar-label");
        const name = el("span", "", `r/${s.name}`);
        const count = el("span", "count", `${s.unused} / ${s.total}`);
        label.append(name, count);
        const track = el("div", "bar-track");
        const fill = el("div", `bar-fill${s.unused <= 5 ? " low" : ""}`);
        fill.style.width = `${Math.round((s.unused / max) * 100)}%`;
        track.appendChild(fill);
        row.append(label, track);
        box.appendChild(row);
    }
}

function renderYouTubePublish(d) {
    const box = document.getElementById("yt-publish-box");
    box.innerHTML = "";
    const yt = d.youtube;

    document.getElementById("yt-privacy-hint").textContent =
        yt && yt.privacy ? `(privacy: ${yt.privacy})` : "";
    document.getElementById("yt-slots").textContent =
        yt && yt.slots && yt.slots.length ? yt.slots.join(", ") : "—";

    if (!yt || !yt.next_publish_utc || !yt.next_publish_utc.length) {
        box.appendChild(el("p", "empty",
            yt && yt.privacy !== "public"
                ? "No schedule — videos post as " + (yt ? yt.privacy : "private") +
                  ". Set YOUTUBE_PRIVACY to 'public' to schedule go-live times."
                : "No upcoming publishes yet — the pipeline writes them after each run."));
        return;
    }

    for (const iso of yt.next_publish_utc) {
        const row = el("div", "schedule-item");
        const label = el("span", "", "Next publish");
        const slot = el("span", "slot",
            new Date(iso).toLocaleString(undefined, {
                month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
            }));
        row.append(label, slot);
        box.appendChild(row);
    }
    if (yt.privacy !== "public") {
        box.appendChild(el("p", "subtitle",
            "(privacy is " + yt.privacy + " — these slots will apply once set to public)"));
    }
}

function renderSchedule(d) {
    const box = document.getElementById("schedule-box");
    box.innerHTML = "";
    const sched = d.schedule;

    if (!sched || Object.keys(sched).length === 0) {
        box.appendChild(el("p", "empty",
            "No TikTok slots assigned yet. The pipeline writes these to " +
            "tiktok_schedule_state.json as videos are generated."));
        return;
    }

    const items = sched.slots || [];
    if (!items.length) {
        box.appendChild(el("p", "empty", "Schedule state exists but no slots recorded."));
        return;
    }
    for (const item of items) {
        const row = el("div", "schedule-item");
        const label = el("span", "", String(item.title || item.video_file || "video"));
        const slot = el("span", "slot", String(item.slot_utc || item.time || "?"));
        row.append(label, slot);
        box.appendChild(row);
    }
}

function statusBadge(state) {
    const labels = {
        posted: "posted", success: "success", failure: "failed", failed: "failed",
        cancelled: "cancelled", in_progress: "in progress", queued: "queued",
        skipped: "skipped", startup_failure: "startup failure",
    };
    const cls = state === "failed" ? "failure" : state;
    return el("span", `status ${cls}`, labels[state] || state);
}

function statusPill(run) {
    const done = run.status === "completed";
    const state = done ? (run.conclusion || "completed") : run.status;
    return statusBadge(state);
}

function renderVideos(videos) {
    const list = document.getElementById("video-list");
    list.innerHTML = "";
    if (!videos || !videos.length) {
        list.appendChild(el("p", "empty",
            "No videos recorded yet — the workflow writes these after each YouTube upload."));
        return;
    }
    for (const v of videos) {
        const item = el("li", "video-item");

        const top = el("div", "video-top");
        const a = el("a", "", v.title || v.video_file || "video");
        a.href = v.url || "#";
        a.target = "_blank";
        top.appendChild(a);
        if (v.subreddit) top.appendChild(el("span", "badge", `r/${v.subreddit}`));

        const meta = el("div", "video-meta");
        meta.appendChild(statusBadge(v.status));
        if (v.posted_at) {
            meta.appendChild(el("span", "run-meta", new Date(v.posted_at).toLocaleString(undefined, {
                month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
            })));
        }
        // Performance metrics (filled in by performance_tracker.py for YouTube videos)
        if (v.views !== undefined && v.views !== null) {
            const stats = [];
            stats.push(`${v.views.toLocaleString()} views`);
            if (v.completion_pct !== undefined && v.completion_pct !== null) {
                const pct = Number(v.completion_pct).toFixed(1);
                const good = Number(v.completion_pct) >= 60;
                const badge = el("span", good ? "perf-badge perf-good" : "perf-badge", `${pct}% completion`);
                stats.push(badge);
            }
            if (v.avg_view_duration_sec !== undefined && v.avg_view_duration_sec !== null) {
                stats.push(el("span", "run-meta", `${Number(v.avg_view_duration_sec).toFixed(1)}s avg view`));
            }
            if (stats.length) {
                const wrap = el("span", "perf-stats");
                stats.forEach(s => wrap.appendChild(typeof s === "string" ? el("span", "run-meta", s) : s));
                meta.appendChild(wrap);
            }
        }
        if (v.error) meta.appendChild(el("span", "run-meta", v.error));

        item.append(top, meta);
        list.appendChild(item);
    }
}

function renderRuns(data) {
    const list = document.getElementById("run-list");
    list.innerHTML = "";
    if (data && data.__error) {
        list.appendChild(el("p", "empty",
            `Could not reach the GitHub API (${data.__error}) — run status unavailable.`));
        return;
    }
    // The GitHub API wraps runs in { total_count, workflow_runs: [...] } —
    // unwrap it before rendering.
    const runs = (data && data.workflow_runs) || [];
    if (!runs.length) {
        list.appendChild(el("p", "empty", "No pipeline runs found yet."));
        return;
    }
    for (const run of runs) {
        const item = el("li", "run-item");
        const a = el("a", "", run.name || "workflow");
        a.href = run.html_url;
        a.target = "_blank";
        item.append(statusPill(run), a);
        const when = new Date(run.created_at).toLocaleString(undefined, {
            month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
        });
        item.appendChild(el("span", "run-meta", when));
        list.appendChild(item);
    }
}

async function main() {
    const data = await loadJSON("data.json", null);

    if (data && !data.__error) {
        document.getElementById("generated-at").textContent =
            `Story data generated ${new Date(data.generated_at).toLocaleString()}.`;

        const alert = document.getElementById("low-bank-alert");
        if (data.low_bank) {
            document.getElementById("low-bank-count").textContent = data.unused_stories;
            alert.hidden = false;
        } else {
            alert.hidden = true;
        }

        renderCards(data);
        renderVideos(data.videos);
        renderBars(data);
        renderYouTubePublish(data);
        renderSchedule(data);
    } else {
        document.getElementById("generated-at").textContent =
            "⚠️ data.json not found — run dashboard/build_data.py first.";
    }

    renderRuns(await loadJSON(GITHUB_API, null));
}

main();
