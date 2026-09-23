#!/usr/bin/env python3
"""Runner-side proof: can a 4K source be fetched BY SLICE instead of in full?

WHY A BYTE COUNT IS NOT ENOUGH
------------------------------
A seek that silently fails returns the START of the file, and the result still
has correct-looking duration metadata. A small byte count is therefore not
evidence that the right footage arrived. This compares CONTENT:

  remote reads  cut a window straight from the Drive URL — once DIRECT, once
                through a byte-counting local PROXY — at three timestamps
  ground truth  only THEN download the whole file, and hash every decoded frame
                of a window around each timestamp
  the check     each remote window's frames must be a CONTIGUOUS SUBSEQUENCE of
                its ground-truth window's frames

Subsequence rather than equality: an HTTP seek may land on a different keyframe
than a local file seek. What must hold is that the frames are the real footage
from the requested moment, in order, unaltered, and that they came cheaply.

ORDERING MATTERS (learned the hard way)
---------------------------------------
The first version downloaded the whole file first. The remote reads that
followed all failed in under a second with "Invalid data found when processing
input", and the proxy saw HTTP 200 with an EMPTY body — while curl had just
pulled the same 4.04 GB at 107 MB/s. Downloading that file immediately before
asking for ranges is exactly the pattern that makes Drive serve an interstitial
or refuse, so the reads now come FIRST and the ground-truth download LAST.

Every read also reports what Drive actually sent, so a failure is diagnosable:
status, Content-Type, Content-Range, and the first bytes of the body.

Exit code 0 only if every remote window matches the ground truth and stayed small.
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
# Asymmetric on purpose: a seek lands on the keyframe at or BEFORE the
# timestamp, so the ground truth must reach well back of it. Too narrow a
# window would fail a read that was actually correct.
GROUND_BEFORE = 15.0
GROUND_AFTER = 4.0
# Drive serves large files to unknown agents differently from browsers.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

STATE = {"bytes": 0, "requests": [], "capped": False, "diag": []}
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
        req.add_header("User-Agent", UA)
        if rng:
            req.add_header("Range", rng)
        sent, status = 0, "-"
        diag = {}
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = resp.status
                diag = {"status": status,
                        "content_type": resp.headers.get("Content-Type"),
                        "content_range": resp.headers.get("Content-Range"),
                        "content_length": resp.headers.get("Content-Length")}
                self.send_response(status)
                for h in ("Content-Type", "Content-Length", "Content-Range",
                          "Accept-Ranges"):
                    v = resp.headers.get(h)
                    if v:
                        self.send_header(h, v)
                self.end_headers()
                if head_only:
                    self._record("HEAD", rng, status, 0, diag)
                    return
                first = True
                while True:
                    with LOCK:
                        if STATE["bytes"] + sent >= Proxy.cap:
                            STATE["capped"] = True
                            break
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    if first:
                        diag["first_bytes"] = chunk[:16].hex()
                        first = False
                    self.wfile.write(chunk)
                    sent += len(chunk)
        except Exception as e:                        # noqa: BLE001
            diag["error"] = f"{type(e).__name__}: {e}"
            self._record("GET", rng, f"{status} ERR {type(e).__name__}", sent,
                         diag)
            return
        self._record("GET", rng, status, sent, diag)

    def _record(self, method, rng, status, n, diag=None):
        with LOCK:
            STATE["bytes"] += n
            STATE["requests"].append((method, rng or "-", str(status), n))
            STATE["diag"].append(diag or {})


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def frame_hashes(path, seek=None, dur=None):
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


def cut(src, seek, dur, out, timeout=300, ua=None):
    """Stream-copy a window. Returns (ok, elapsed, error)."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    if ua:
        cmd += ["-user_agent", ua]
    cmd += ["-ss", f"{seek:.3f}", "-t", f"{dur:.3f}", "-i", src,
            "-map", "0:v:0", "-c", "copy", out]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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


def curl_probe(url, label):
    """What does Drive actually send to this IP right now? Cheap and decisive."""
    head = "probe_work/head.bin"
    os.makedirs("probe_work", exist_ok=True)
    r = subprocess.run(
        ["curl", "-sS", "-A", UA, "-r", "0-1023", "-o", head, "-D", "-",
         "--max-time", "60", url], capture_output=True, text=True)
    status = ""
    ctype = crange = ""
    for line in r.stdout.splitlines():
        low = line.lower()
        if low.startswith("http/"):
            status = line.strip()
        elif low.startswith("content-type:"):
            ctype = line.split(":", 1)[1].strip()
        elif low.startswith("content-range:"):
            crange = line.split(":", 1)[1].strip()
    magic = ""
    if os.path.exists(head):
        with open(head, "rb") as f:
            b = f.read(24)
        magic = b[4:12].decode("latin-1", "replace") if len(b) > 12 else b[:12].hex()
    print(f"  [{label}] {status}  type={ctype!r}  range={crange!r}  "
          f"magic={magic!r}")
    return {"status": status, "content_type": ctype, "content_range": crange,
            "magic": magic}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-id", default="1Qk6bx5C5Mkg1EEFyQZ9oNqy-nr-OcHK7")
    ap.add_argument("--cap-mb", type=float, default=200.0)
    ap.add_argument("--workdir", default="probe_work")
    ap.add_argument("--src-duration", type=float, default=1233.0)
    ap.add_argument("--no-ua-for-ffmpeg", action="store_true",
                    help="test ffmpeg WITHOUT the browser User-Agent, to tell a "
                         "User-Agent problem apart from a throttling problem")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    url = ("https://drive.usercontent.google.com/download?id="
           f"{args.file_id}&export=download&confirm=t")
    report = {"file_id": args.file_id, "timestamps": TIMESTAMPS,
              "window_s": WINDOW, "ground_before_s": GROUND_BEFORE,
              "ground_after_s": GROUND_AFTER, "user_agent_sent": UA,
              "phases": {}, "checks": {}, "failures": []}

    print(f"host: {socket.gethostname()}  cpus: {os.cpu_count()}")
    print(subprocess.run(["df", "-h", "."], capture_output=True,
                         text=True).stdout.strip().splitlines()[-1])
    print(subprocess.run(["ffmpeg", "-version"], capture_output=True,
                         text=True).stdout.splitlines()[0])

    # ---- phase 0: is Drive serving MEDIA to this IP at all, right now? ----
    print("\n=== phase 0: can Drive serve this file at all from here? ===")
    pre = curl_probe(url, "before")
    report["phases"]["drive_reachable_before"] = pre
    media = ("video" in (pre["content_type"] or "").lower()
             or "ftyp" in (pre["magic"] or ""))
    if not media:
        print("  Drive is NOT serving media to this IP right now — any seek "
              "failure below would be an environment problem, not a seeking "
              "problem.")

    # ---- phase 1: DIRECT remote seeks (no big download first) ------------
    print("\n=== phase 1: cut straight from the Drive URL (direct) ===")
    ua = None if args.no_ua_for_ffmpeg else UA
    direct = {}
    for ts in TIMESTAMPS:
        out = os.path.join(args.workdir, f"direct_{int(ts)}.mp4")
        ok, secs, err = cut(url, ts, WINDOW, out, ua=ua)
        h = frame_hashes(out) if ok else None
        direct[ts] = {"ok": ok, "seconds": round(secs, 2), "frames": len(h or []),
                      "hashes": h or [], "error": err}
        print(f"  ts={ts:6.0f}s  exit={'0' if ok else 'x'}  {secs:6.1f}s  "
              f"frames={len(h or [])}" + (f"  {err}" if err else ""))
        time.sleep(2)
    report["phases"]["direct"] = {str(k): {kk: vv for kk, vv in v.items()
                                           if kk != "hashes"}
                                  for k, v in direct.items()}

    # ---- phase 2: PROXIED remote seeks (byte counting) -------------------
    print("\n=== phase 2: same reads through a byte-counting proxy ===")
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
            STATE["diag"] = []
            STATE["capped"] = False
        out = os.path.join(args.workdir, f"proxy_{int(ts)}.mp4")
        ok, secs, err = cut(local, ts, WINDOW, out)
        with LOCK:
            fwd, reqs, diags = STATE["bytes"], list(STATE["requests"]), \
                list(STATE["diag"])
            capped = STATE["capped"]
        h = frame_hashes(out) if ok else None
        proxied[ts] = {"ok": ok, "seconds": round(secs, 2),
                       "bytes_forwarded": fwd, "requests": reqs, "diag": diags,
                       "capped": capped, "frames": len(h or []),
                       "hashes": h or [], "error": err}
        print(f"  ts={ts:6.0f}s  exit={'0' if ok else 'x'}  {secs:6.1f}s  "
              f"forwarded {fwd/1e6:7.2f} MB  frames={len(h or [])}"
              + (f"  {err}" if err else ""))
        for d in diags:
            print(f"        upstream: status={d.get('status')} "
                  f"type={d.get('content_type')!r} "
                  f"range={d.get('content_range')!r} "
                  f"first_bytes={(d.get('first_bytes') or '-')[:16]} "
                  f"{d.get('error', '')}")
        time.sleep(2)
    srv.shutdown()
    report["phases"]["proxied"] = {str(k): {kk: vv for kk, vv in v.items()
                                            if kk != "hashes"}
                                   for k, v in proxied.items()}

    # ---- phase 3: ground truth (full download, LAST) --------------------
    print("\n=== phase 3: full download (ground truth), after the seeks ===")
    full = os.path.join(args.workdir, "full.mp4")
    t0 = time.time()
    r = subprocess.run(["curl", "-sSL", "-A", UA, "--fail", "--max-time", "900",
                        "-o", full, url], capture_output=True, text=True)
    secs = time.time() - t0
    if r.returncode != 0:
        print(f"  download FAILED after {secs:.0f}s: {r.stderr.strip()[:200]}")
        report["failures"].append("ground-truth download failed: "
                                  + r.stderr.strip()[:200])
        with open("probe_result.json", "w") as f:
            json.dump(report, f, indent=2)
        return 1
    real = os.path.getsize(full)
    mbps = real / secs / 1e6 if secs else 0
    print(f"  {real/1e9:.2f} GB in {secs:.1f}s ({mbps:.0f} MB/s)")
    report["phases"]["full_download"] = {"seconds": round(secs, 2),
                                        "bytes": real, "MBps": round(mbps, 1)}
    post = curl_probe(url, "after-download")
    report["phases"]["drive_reachable_after"] = post

    print("\n=== phase 4: ground-truth frames, then the content check ===")
    truth = {}
    span = GROUND_BEFORE + GROUND_AFTER
    for ts in TIMESTAMPS:
        start = max(0.0, ts - GROUND_BEFORE)
        truth[ts] = frame_hashes(full, seek=start, dur=span) or []
        print(f"  ts={ts:6.0f}s  {len(truth[ts])} ground-truth frames over "
              f"{start:.0f}-{start + span:.0f}s")

    results = {"direct": direct, "proxied": proxied}
    for phase, windows in results.items():
        for ts in TIMESTAMPS:
            h = windows[ts]["hashes"]
            at = contiguous_subsequence(truth[ts], h)
            windows[ts]["match_at"] = at
            windows[ts]["content_ok"] = at is not None
            if at is None:
                report["failures"].append(
                    f"{phase} read at ts={ts} did not return the real footage")
    print("\n=== content check (frames vs ground truth) ===")
    for phase, windows in results.items():
        for ts in TIMESTAMPS:
            w = windows[ts]
            print(f"  {phase:8} ts={ts:6.0f}s  frames={w['frames']:5}  "
                  f"{'MATCH at ' + str(w['match_at']) if w['content_ok'] else 'NO MATCH'}")

    # ---- verdict ---------------------------------------------------------
    all_direct = all(w["content_ok"] for w in direct.values())
    all_proxy = all(w["content_ok"] for w in proxied.values())
    frac = (max((w["bytes_forwarded"] for w in proxied.values()), default=0)
            / real) if real else 1.0
    cheap = frac < 0.10
    worst = max((w["seconds"] for w in proxied.values()), default=0)
    report["verdict"] = {
        "direct_content_ok": all_direct,
        "proxied_content_ok": all_proxy,
        "max_fraction_of_file_fetched": round(frac, 4),
        "full_download_seconds": report["phases"]["full_download"]["seconds"],
        "slowest_seek_seconds": round(worst, 1),
        "drive_served_media_before_seeks": media,
    }
    print("\n=== VERDICT ===")
    print(f"  Drive served media before the seeks : {media}")
    print(f"  content correct, direct URL          : {all_direct}")
    print(f"  content correct, proxied             : {all_proxy}")
    print(f"  most of the file any seek needed     : {frac*100:.2f}%")
    print(f"  slowest seek {worst:.1f}s vs "
          f"{report['phases']['full_download']['seconds']:.1f}s for the whole file")
    if all_direct and all_proxy and cheap:
        print("\n  SLICE-FETCHING WORKS. The frames are the real footage and the "
              "read stays tiny, so big files need no splitting and no cache.")
    elif not media:
        print("\n  INCONCLUSIVE — Drive was not serving this file to the runner "
              "at all when the reads ran, so this says nothing about seeking.")
    else:
        print("\n  NOT VIABLE — at least one read did not return the footage "
              "that was asked for. Do not implement this path.")
    print("  (full report: probe_result.json)")

    with open("probe_result.json", "w") as f:
        json.dump(report, f, indent=2)
    return 0 if (all_direct and all_proxy and cheap) else 1


if __name__ == "__main__":
    sys.exit(main())
