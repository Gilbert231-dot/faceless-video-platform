#!/usr/bin/env python3
"""Runner-side proof: can a 4K source be fetched BY SLICE instead of in full?

WHY A BYTE COUNT IS NOT ENOUGH
------------------------------
A seek that silently fails returns the START of the file, and the result still
has correct-looking duration metadata. A small byte count is therefore not
evidence that the right footage arrived. This compares CONTENT instead:

  ground truth  download the whole file, then hash every decoded frame of a
                window around each timestamp
  remote read   cut a window straight from the Drive URL — once DIRECT (what a
                naive implementation would do) and once through a byte-counting
                local PROXY — then require the frames produced to be a
                CONTIGUOUS SUBSEQUENCE of the ground-truth window's frames

A subsequence, not equality: an HTTP seek may land on a different keyframe than
a local file seek. What must hold is that the frames are the real footage from
the requested moment, in order, unaltered, and that they came cheaply.

WHAT THE ANSWER BUYS
--------------------
The pipeline re-downloads the whole footage file every run. GitHub's cache is
10 GB per repository, so a 15-25 GB source can never be cached — it is fetched
in full four times a day, and one file pulled that often is what trips Drive's
per-file 24h download quota. If a slice can be fetched instead, big files stop
being a problem and no splitting is needed.

Exit code 0 only if every timestamp passes both checks.
"""

import argparse
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

TIMESTAMPS = [100.0, 600.0, 1100.0]   # early / middle / late in a ~20 min file
WINDOW = 2.0                          # seconds fetched per seek
# Ground truth is asymmetric on purpose: a seek lands on the keyframe at or
# BEFORE the timestamp, so the window must reach well back of it. Too narrow a
# window would fail a read that was actually correct.
GROUND_BEFORE = 15.0
GROUND_AFTER = 4.0


# --------------------------------------------------------------------------
# byte-counting proxy
# --------------------------------------------------------------------------
STATE = {"bytes": 0, "requests": [], "capped": False}
LOCK = threading.Lock()


class Proxy(http.server.BaseHTTPRequestHandler):
    target = ""
    cap = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head_only=True)

    def _serve(self, head_only=False):
        rng = self.headers.get("Range")
        req = urllib.request.Request(Proxy.target)
        if rng:
            req.add_header("Range", rng)
        sent, status = 0, "-"
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = resp.status
                self.send_response(status)
                for h in ("Content-Type", "Content-Length", "Content-Range",
                          "Accept-Ranges"):
                    v = resp.headers.get(h)
                    if v:
                        self.send_header(h, v)
                self.end_headers()
                if head_only:
                    self._record("HEAD", rng, status, 0)
                    return
                while True:
                    with LOCK:
                        if STATE["bytes"] + sent >= Proxy.cap:
                            STATE["capped"] = True
                            break
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
        except Exception as e:                        # noqa: BLE001
            self._record("GET", rng, f"{status} ERR {type(e).__name__}", sent)
            return
        self._record("GET", rng, status, sent)

    def _record(self, method, rng, status, n):
        with LOCK:
            STATE["bytes"] += n
            STATE["requests"].append((method, rng or "-", str(status), n))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def frame_hashes(path, seek=None, dur=None):
    """MD5 of every decoded frame, in order."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error"]
    if seek is not None:
        cmd += ["-ss", f"{seek:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-an", "-f", "framemd5", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        if line and not line.startswith("#") and line.count(",") >= 5:
            out.append(line.split(",")[-1].strip())
    return out


def cut(src, seek, dur, out, timeout=300):
    """Stream-copy a window. Returns (ok, elapsed_seconds, error).

    The timeout matters: a read that turns out to be SEQUENTIAL has to chew
    through gigabytes to reach a late timestamp, and this must not stall the
    whole job.
    """
    t0 = time.time()
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-y",
             "-ss", f"{seek:.3f}", "-t", f"{dur:.3f}", "-i", src,
             "-map", "0:v:0", "-c", "copy", out],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, f"timed out after {timeout}s"
    return r.returncode == 0, time.time() - t0, (r.stderr or "").strip()[:200]


def contiguous_subsequence(hay, needle):
    if not needle or len(needle) > len(hay):
        return None
    first = needle[0]
    for i in range(len(hay) - len(needle) + 1):
        if hay[i] == first and hay[i:i + len(needle)] == needle:
            return i
    return None


def download(url, dest):
    t0 = time.time()
    r = subprocess.run(["curl", "-sSL", "--fail", "--max-time", "900",
                        "-o", dest, url], capture_output=True, text=True)
    return r.returncode == 0, time.time() - t0, (r.stderr or "").strip()[:200]


def remote_size(url):
    try:
        req = urllib.request.Request(url)
        req.add_header("Range", "bytes=0-")
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range") or ""
            if "/" in cr:
                return int(cr.rsplit("/", 1)[1])
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception as e:                            # noqa: BLE001
        print(f"  size probe failed: {type(e).__name__}: {e}")
        return None


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-id",
                    default="1Qk6bx5C5Mkg1EEFyQZ9oNqy-nr-OcHK7")
    ap.add_argument("--cap-mb", type=float, default=200.0)
    ap.add_argument("--workdir", default="probe_work")
    ap.add_argument("--src-duration", type=float, default=1233.0,
                    help="source duration in seconds; used only to say how many "
                         "bytes a real 2s window should contain")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    url = ("https://drive.usercontent.google.com/download?id="
           f"{args.file_id}&export=download&confirm=t")
    report = {"file_id": args.file_id, "url_stripped": url.split("?")[0],
              "timestamps": TIMESTAMPS, "window_s": WINDOW,
              "ground_before_s": GROUND_BEFORE,
              "ground_after_s": GROUND_AFTER,
              "phases": {}, "checks": {}, "failures": []}

    def disk():
        r = subprocess.run(["df", "-h", "."], capture_output=True, text=True)
        return r.stdout.strip().splitlines()[-1]

    print(f"host: {socket.gethostname()}")
    print(f"disk: {disk()}")
    print(f"ffmpeg: {subprocess.run(['ffmpeg','-version'], capture_output=True, text=True).stdout.splitlines()[0]}")
    print(f"cpus: {os.cpu_count()}")

    size = remote_size(url)
    print(f"file size: {size/1e9:.2f} GB" if size else "file size: unknown")
    report["file_size_bytes"] = size

    # ---------------- phase A: ground truth (full download) ----------------
    print("\n=== phase A: download the whole file (ground truth) ===")
    full = os.path.join(args.workdir, "full.mp4")
    ok, secs, err = download(url, full)
    if not ok:
        print(f"  download FAILED after {secs:.0f}s: {err}")
        report["failures"].append(f"full download failed: {err}")
        with open("probe_result.json", "w") as f:
            json.dump(report, f, indent=2)
        return 1
    real = os.path.getsize(full)
    mbps = (real / secs / 1e6) if secs else 0
    print(f"  {real/1e9:.2f} GB in {secs:.1f}s ({mbps:.0f} MB/s)")
    report["phases"]["full_download"] = {"seconds": round(secs, 2),
                                        "bytes": real,
                                        "MBps": round(mbps, 1)}
    if size and real != size:
        print(f"  NOTE: got {real} bytes, server advertised {size}")

    # ---------------- phase B: ground-truth windows ------------------------
    print("\n=== phase B: ground-truth frames for each timestamp ===")
    truth = {}
    span = GROUND_BEFORE + GROUND_AFTER
    for ts in TIMESTAMPS:
        start = max(0.0, ts - GROUND_BEFORE)
        got = frame_hashes(full, seek=start, dur=span)
        truth[ts] = got or []
        print(f"  ts={ts:6.0f}s  {len(truth[ts])} frames hashed over "
              f"{start:.0f}-{start + span:.0f}s")
    report["checks"]["ground_truth_frames"] = {str(k): len(v)
                                               for k, v in truth.items()}

    # ---------------- phase C: DIRECT remote reads ------------------------
    print("\n=== phase C: cut straight from the Drive URL (no proxy) ===")
    direct = {}
    for ts in TIMESTAMPS:
        out = os.path.join(args.workdir, f"direct_{int(ts)}.mp4")
        ok, secs, err = cut(url, ts, WINDOW, out)
        h = frame_hashes(out) if ok else None
        at = contiguous_subsequence(truth[ts], h or []) if h else None
        good = bool(h) and at is not None
        direct[ts] = {"ok": ok, "seconds": round(secs, 2),
                      "frames": len(h or []), "match_at": at,
                      "content_ok": good, "error": err}
        print(f"  ts={ts:6.0f}s  exit={'0' if ok else 'x'}  {secs:6.1f}s  "
              f"{len(h or [])} frames  content={'MATCH' if good else 'NO'}"
              + (f"  {err}" if err and not good else ""))
        if not good:
            report["failures"].append(
                f"direct read at ts={ts} did not return the real footage")
    report["phases"]["direct"] = {str(k): v for k, v in direct.items()}

    # ---------------- phase D: PROXIED remote reads (byte count) ----------
    print("\n=== phase D: same reads through a byte-counting proxy ===")
    Proxy.target = url
    Proxy.cap = int(args.cap_mb * 1e6)
    port = free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Proxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    local = f"http://127.0.0.1:{port}/footage.mp4"
    proxied = {}
    for ts in TIMESTAMPS:
        with LOCK:
            STATE["bytes"] = 0
            STATE["requests"] = []
            STATE["capped"] = False
        out = os.path.join(args.workdir, f"proxy_{int(ts)}.mp4")
        ok, secs, err = cut(local, ts, WINDOW, out)
        with LOCK:
            fwd = STATE["bytes"]
            reqs = list(STATE["requests"])
            capped = STATE["capped"]
        h = frame_hashes(out) if ok else None
        at = contiguous_subsequence(truth[ts], h or []) if h else None
        good = bool(h) and at is not None
        expected = (size / args.src_duration * WINDOW) if size else None
        proxied[ts] = {"ok": ok, "seconds": round(secs, 2),
                       "bytes_forwarded": fwd, "requests": reqs,
                       "capped": capped, "frames": len(h or []),
                       "match_at": at, "content_ok": good,
                       "expected_window_bytes": expected, "error": err}
        print(f"  ts={ts:6.0f}s  exit={'0' if ok else 'x'}  {secs:6.1f}s  "
              f"forwarded {fwd/1e6:7.2f} MB of {real/1e9:.2f} GB "
              f"({fwd/real*100:.2f}%)  content="
              f"{'MATCH' if good else 'NO'}")
        for m, rng, status, n in reqs:
            print(f"        {m} {str(rng)[:40]:40} {status:16} {n/1e6:8.2f} MB")
        if not good:
            report["failures"].append(
                f"proxied read at ts={ts} did not return the real footage")
    srv.shutdown()
    report["phases"]["proxied"] = {str(k): v for k, v in proxied.items()}

    # ---------------- verdict --------------------------------------------
    all_direct = all(d["content_ok"] for d in direct.values())
    all_proxy = all(d["content_ok"] for d in proxied.values())
    worst = max(d["seconds"] for d in proxied.values())
    frac = max(d["bytes_forwarded"] for d in proxied.values()) / real
    cheap = frac < 0.10
    report["verdict"] = {
        "direct_content_ok": all_direct,
        "proxied_content_ok": all_proxy,
        "max_fraction_of_file_fetched": round(frac, 4),
        "full_download_seconds": report["phases"]["full_download"]["seconds"],
        "slowest_seek_seconds": round(worst, 1),
    }
    print("\n=== VERDICT ===")
    print(f"  content correct, direct URL : {all_direct}")
    print(f"  content correct, proxied    : {all_proxy}")
    print(f"  most of the file any seek needed: {frac*100:.2f}%")
    print(f"  slowest seek {worst:.1f}s vs "
          f"{report['phases']['full_download']['seconds']:.1f}s for the whole file")

    if all_direct and all_proxy and cheap:
        print("\n  SLICE-FETCHING WORKS. The frames are the real footage and the "
              "read stays tiny, so big files need no splitting and no cache.")
    elif all_direct and all_proxy:
        print("\n  Content is right, but the reads are not small enough to be "
              "worth it — treat this as NOT VIABLE for big files.")
    else:
        print("\n  NOT VIABLE — at least one read did not return the footage "
              "that was asked for. Do not implement this path.")
    print("  (full report: probe_result.json)")

    with open("probe_result.json", "w") as f:
        json.dump(report, f, indent=2)
    return 0 if (all_direct and all_proxy and cheap) else 1


if __name__ == "__main__":
    sys.exit(main())
