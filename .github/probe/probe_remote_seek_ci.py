#!/usr/bin/env python3
"""Runner-side proof: can a 4K source be fetched BY SLICE instead of in full?

WHY THIS IS A TRANSPORT QUESTION
--------------------------------
Two runs already disagreed with each other in a way that points at the HTTP
client, not at seeking:

  curl, browser UA, Range  ->  HTTP/2 206  type='video/mp4'
                               range='bytes 0-1023/4043533814'  magic='ftypisom'
  ffmpeg (direct URL)      ->  "Invalid data found when processing input" (0.9s)
  python urllib, same UA   ->  HTTP/1.1 200  type='text/html'  body starts '<!DOCTYPE'

Same file, same IP, seconds apart. So before asking whether seeking works, this
asks WHICH CLIENT DRIVE WILL SERVE MEDIA TO — and then tests slice reads through
the one that works, including a curl-backed local proxy so ffmpeg can seek
without needing to speak that transport itself.

WHY A BYTE COUNT IS NOT ENOUGH
------------------------------
A seek that silently fails returns the START of the file with duration metadata
that still looks correct. So the final check is on CONTENT: the frames from each
remote window must be a contiguous subsequence of the frames of a window around
that timestamp, taken from a full download. (Subsequence, not equality, because
an HTTP seek may land on a different keyframe than a local file seek.)

Exit code 0 only if a working transport is found AND every window matches the
ground truth AND the read stayed small.
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
import urllib.request

TIMESTAMPS = [100.0, 600.0, 1100.0]
WINDOW = 2.0
GROUND_BEFORE = 15.0
GROUND_AFTER = 4.0
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

STATE = {"bytes": 0, "requests": [], "diag": []}
LOCK = threading.Lock()


class Proxy(http.server.BaseHTTPRequestHandler):
    """Local range-serving proxy in front of Drive.

    upstream='curl' shells out to curl for each range, which matters because
    curl demonstrably gets media where ffmpeg's own HTTP client does not. This
    lets ffmpeg seek while the actual fetch uses the transport that works.
    """
    target = ""
    upstream = "urllib"
    ua = BROWSER_UA
    total = 0
    cap = 0
    timeout = 300

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self._respond(status=200, length=Proxy.total, extra=(), body=None)

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end = 0, Proxy.total - 1
        status = 200
        if rng and rng.startswith("bytes="):
            spec = rng.split("=", 1)[1].split(",")[0].strip()
            s, _dash, e = spec.partition("-")
            start = int(s) if s else 0
            if e:
                end = min(int(e), Proxy.total - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{Proxy.total}")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        diag = {"transport": Proxy.upstream, "range": rng, "status": status,
                "start": start, "end": end, "length": length}
        try:
            if Proxy.upstream == "curl":
                cmd = ["curl", "-sS", "--max-time", str(Proxy.timeout),
                       "-A", Proxy.ua, "-r", f"{start}-{end}", Proxy.target]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
                body = proc.stdout
            else:
                req = urllib.request.Request(Proxy.target)
                req.add_header("User-Agent", Proxy.ua)
                req.add_header("Range", f"bytes={start}-{end}")
                proc = None
                body = urllib.request.urlopen(req, timeout=Proxy.timeout)
                first = body.read(16)
                diag["first_bytes"] = first.hex()
                diag["content_type"] = body.headers.get("Content-Type")
                diag["content_range"] = body.headers.get("Content-Range")
        except Exception as e:                          # noqa: BLE001
            diag["error"] = f"{type(e).__name__}: {e}"
            self._record(0, diag)
            self.send_response(502)
            self.end_headers()
            return

        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{Proxy.total}")
        self.end_headers()
        sent = 0
        try:
            while sent < length:
                with LOCK:
                    if STATE["bytes"] + sent >= Proxy.cap:
                        break
                if Proxy.upstream == "curl":
                    chunk = body.read(min(262144, length - sent))
                    if not chunk:
                        break
                    if sent == 0:
                        diag["first_bytes"] = chunk[:16].hex()
                else:
                    chunk = body.read(min(262144, length - sent))
                    if not chunk:
                        break
                    if sent == 0:
                        diag["first_bytes_media"] = chunk[:16].hex()
                self.wfile.write(chunk)
                sent += len(chunk)
        except Exception as e:                          # noqa: BLE001
            diag["error"] = f"write {type(e).__name__}"
        finally:
            if proc is not None:
                try:
                    proc.stderr.close()
                    proc.wait(timeout=10)
                except Exception:                       # noqa: BLE001
                    proc.kill()
        self._record(sent, diag)

    def _respond(self, status, length, extra, body):
        self.send_response(status)
        self.send_header("Content-Length", str(length))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def _record(self, n, diag):
        with LOCK:
            STATE["bytes"] += n
            STATE["requests"].append((diag.get("range") or "-", n))
            STATE["diag"].append(diag)


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
    return [l.split(",")[-1].strip() for l in r.stdout.splitlines()
            if l and not l.startswith("#") and l.count(",") >= 5]


def cut(src, seek, dur, out, timeout=300, ua=None, extra_headers=None):
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    if ua:
        cmd += ["-user_agent", ua]
    if extra_headers:
        cmd += ["-headers", extra_headers]
    cmd += ["-ss", f"{seek:.3f}", "-t", f"{dur:.3f}", "-i", src,
            "-map", "0:v:0", "-c", "copy", out]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, f"timed out after {timeout}s"
    return r.returncode == 0, time.time() - t0, (r.stderr or "").strip()[:180]


def one_kb(url, label, extra_curl=(), urllib_headers=(), use_urllib=False,
           full_get=False):
    """Fetch 1 KB (or start a full GET) and report exactly what came back."""
    out = "probe_work/kb.bin"
    os.makedirs("probe_work", exist_ok=True)
    if use_urllib:
        try:
            req = urllib.request.Request(url)
            for k, v in urllib_headers:
                req.add_header(k, v)
            if not full_get:
                req.add_header("Range", "bytes=0-1023")
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read(1024 if not full_get else 65536)
                rec = {"status": r.status, "type": r.headers.get("Content-Type"),
                       "magic": body[4:12].decode("latin-1", "replace"),
                       "first": body[:16].hex()}
        except Exception as e:                          # noqa: BLE001
            rec = {"error": f"{type(e).__name__}: {e}"}
    else:
        cmd = ["curl", "-sS", "--max-time", "60", "-o", out, "-D", "-"]
        cmd += list(extra_curl)
        if not full_get:
            cmd += ["-r", "0-1023"]
        else:
            cmd += ["--max-time", "8"]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
        rec = {"curl_exit": r.returncode}
        for line in r.stdout.splitlines():
            low = line.lower()
            if low.startswith("http/"):
                rec["status"] = line.strip()
            elif low.startswith("content-type:"):
                rec["type"] = line.split(":", 1)[1].strip()
            elif low.startswith("content-range:"):
                rec["range"] = line.split(":", 1)[1].strip()
        if os.path.exists(out):
            rec["bytes_written"] = os.path.getsize(out)
            with open(out, "rb") as f:
                b = f.read(1024)
            rec["magic"] = b[4:12].decode("latin-1", "replace") if len(b) > 12 else ""
            rec["first"] = b[:16].hex()
    served = ("video" in (rec.get("type") or "").lower()
              or "ftyp" in (rec.get("magic") or ""))
    print(f"  {label:52} {str(rec.get('status') or rec.get('error'))[:24]:24} "
          f"type={str(rec.get('type'))[:18]:18} bytes={rec.get('bytes_written', '-')}"
          f"  {'MEDIA' if served else 'not media'}")
    rec["served_media"] = served
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-id", default="1Qk6bx5C5Mkg1EEFyQZ9oNqy-nr-OcHK7")
    ap.add_argument("--cap-mb", type=float, default=200.0)
    ap.add_argument("--workdir", default="probe_work")
    ap.add_argument("--src-duration", type=float, default=1233.0)
    ap.add_argument("--skip-matrix", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.workdir, exist_ok=True)

    url = ("https://drive.usercontent.google.com/download?id="
           f"{args.file_id}&export=download&confirm=t")
    report = {"file_id": args.file_id, "transport_matrix": {}, "phases": {},
              "failures": []}
    print(f"host: {socket.gethostname()}  cpus: {os.cpu_count()}")
    print(subprocess.run(["ffmpeg", "-version"], capture_output=True,
                         text=True).stdout.splitlines()[0])

    # ---- phase A: WHICH CLIENT DOES DRIVE SERVE MEDIA TO? ---------------
    if not args.skip_matrix:
        print("\n=== phase A: transport matrix (1 KB range, then a short full GET) ===")
        m = report["transport_matrix"]
        m["curl_h2_default_ua_range"] = one_kb(
            url, "curl HTTP/2, default UA, Range")
        m["curl_h2_browser_ua_range"] = one_kb(
            url, "curl HTTP/2, browser UA, Range",
            extra_curl=["-A", BROWSER_UA])
        m["curl_h1_browser_ua_range"] = one_kb(
            url, "curl forced HTTP/1.1, browser UA, Range",
            extra_curl=["-A", BROWSER_UA, "--http1.1"])
        m["curl_h1_default_ua_range"] = one_kb(
            url, "curl forced HTTP/1.1, default UA, Range",
            extra_curl=["--http1.1"])
        m["urllib_browser_ua_range"] = one_kb(
            url, "urllib (HTTP/1.1), browser UA, Range", use_urllib=True,
            urllib_headers=[("User-Agent", BROWSER_UA)])
        m["urllib_browser_ua_accept_range"] = one_kb(
            url, "urllib, browser UA, Accept: */*, Range", use_urllib=True,
            urllib_headers=[("User-Agent", BROWSER_UA), ("Accept", "*/*")])
        m["curl_h2_browser_ua_fullget"] = one_kb(
            url, "curl HTTP/2, browser UA, FULL GET (8s cap)",
            extra_curl=["-A", BROWSER_UA], full_get=True)
        m["curl_h2_default_ua_fullget"] = one_kb(
            url, "curl HTTP/2, default UA, FULL GET (8s cap)", full_get=True)

    total = None
    for line in subprocess.run(
            ["curl", "-sS", "-I", "-r", "0-0", url],
            capture_output=True, text=True).stdout.splitlines():
        if line.lower().startswith("content-range:"):
            total = int(line.split("/")[-1])
    print(f"\nfile size: {total/1e9:.2f} GB" if total else "\nfile size: unknown")
    report["file_size_bytes"] = total
    if not total:
        with open("probe_result.json", "w") as f:
            json.dump(report, f, indent=2)
        return 1
    Proxy.total = total
    Proxy.cap = int(args.cap_mb * 1e6)

    # ---- phase B: ffmpeg direct -----------------------------------------
    print("\n=== phase B: ffmpeg direct on the Drive URL ===")
    direct = {}
    for ua_mode, ua in (("browser_ua", BROWSER_UA), ("default_ua", None),
                        ("browser_ua_accept", BROWSER_UA)):
        out = os.path.join(args.workdir, f"direct_{ua_mode}.mp4")
        eh = "Accept: */*" if ua_mode.endswith("accept") else None
        ok, secs, err = cut(url, TIMESTAMPS[1], WINDOW, out, ua=ua,
                            extra_headers=eh)
        h = frame_hashes(out) if ok else None
        direct[ua_mode] = {"ok": ok, "seconds": round(secs, 2),
                           "frames": len(h or []), "hashes": h or [],
                           "error": err}
        print(f"  {ua_mode:20} exit={'0' if ok else 'x'} {secs:6.1f}s "
              f"frames={len(h or [])}" + (f"  {err}" if err else ""))
    report["phases"]["direct"] = {k: {kk: vv for kk, vv in v.items()
                                      if kk != "hashes"}
                                  for k, v in direct.items()}

    # ---- phase C: proxied, both upstream transports ---------------------
    print("\n=== phase C: slice reads through a local range proxy ===")
    proxied = {}
    for upstream in ("urllib", "curl"):
        Proxy.upstream = upstream
        port = free_port()
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Proxy)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        local = f"http://127.0.0.1:{port}/footage.mp4"
        for ts in TIMESTAMPS:
            with LOCK:
                STATE["bytes"] = 0
                STATE["requests"] = []
                STATE["diag"] = []
            out = os.path.join(args.workdir, f"{upstream}_{int(ts)}.mp4")
            ok, secs, err = cut(local, ts, WINDOW, out)
            with LOCK:
                fwd, diags = STATE["bytes"], list(STATE["diag"])
            h = frame_hashes(out) if ok else None
            key = f"{upstream}@{int(ts)}"
            proxied[key] = {"upstream": upstream, "ts": ts, "ok": ok,
                            "seconds": round(secs, 2),
                            "bytes_forwarded": fwd, "diag": diags,
                            "frames": len(h or []), "hashes": h or [],
                            "error": err}
            print(f"  upstream={upstream:7} ts={ts:6.0f}s exit={'0' if ok else 'x'} "
                  f"{secs:6.1f}s  forwarded {fwd/1e6:7.2f} MB  "
                  f"frames={len(h or [])}" + (f"  {err}" if err else ""))
            for d in diags[:2]:
                print(f"        {d.get('transport')} status={d.get('status')} "
                      f"type={d.get('content_type')} first={d.get('first_bytes')}")
        srv.shutdown()
    report["phases"]["proxied"] = {k: {kk: vv for kk, vv in v.items()
                                       if kk != "hashes"}
                                   for k, v in proxied.items()}

    # ---- phase D: ground truth last (default UA: the browser UA returned 0 bytes)
    print("\n=== phase D: full download (ground truth) ===")
    full = os.path.join(args.workdir, "full.mp4")
    t0 = time.time()
    r = subprocess.run(["curl", "-sSL", "--fail", "--max-time", "900", "-o",
                        full, url], capture_output=True, text=True)
    secs = time.time() - t0
    real = os.path.getsize(full) if os.path.exists(full) else 0
    print(f"  {real/1e9:.2f} GB in {secs:.1f}s ({real/secs/1e6:.0f} MB/s)")
    report["phases"]["full_download"] = {"seconds": round(secs, 2),
                                        "bytes": real,
                                        "MBps": round(real / secs / 1e6, 1)
                                        if secs else 0}
    if real < 1e9:
        print("  ground truth unavailable — cannot content-check")
        report["failures"].append("full download did not produce the file")
        with open("probe_result.json", "w") as f:
            json.dump(report, f, indent=2)
        return 1

    print("\n=== phase E: content check ===")
    truth = {}
    span = GROUND_BEFORE + GROUND_AFTER
    for ts in TIMESTAMPS:
        start = max(0.0, ts - GROUND_BEFORE)
        truth[ts] = frame_hashes(full, seek=start, dur=span) or []

    def subseq(hay, needle):
        if not needle or len(needle) > len(hay):
            return None
        for i in range(len(hay) - len(needle) + 1):
            if hay[i] == needle[0] and hay[i:i + len(needle)] == needle:
                return i
        return None

    for k, w in proxied.items():
        w["match_at"] = subseq(truth[w["ts"]], w["hashes"])
        w["content_ok"] = w["match_at"] is not None
        print(f"  {k:16} frames={w['frames']:5} "
              f"{'MATCH' if w['content_ok'] else 'NO MATCH'}")
        if not w["content_ok"]:
            report["failures"].append(f"{k} did not return the real footage")

    # ---- verdict --------------------------------------------------------
    winners = {}
    for k, w in proxied.items():
        winners.setdefault(w["upstream"], []).append(w)
    best = None
    for up, ws in winners.items():
        if all(w.get("content_ok") for w in ws):
            frac = max(w["bytes_forwarded"] for w in ws) / real
            if frac < 0.10:
                best = (up, frac)
    media_clients = [k for k, v in report["transport_matrix"].items()
                     if v.get("served_media")]
    report["verdict"] = {
        "transports_drive_served_media_to": media_clients,
        "direct_ffmpeg_worked": [k for k, v in direct.items() if v["ok"]],
        "proxied_upstream_that_passed": best[0] if best else None,
        "max_fraction_of_file_fetched": round(best[1], 4) if best else None,
        "full_download_seconds": report["phases"]["full_download"]["seconds"],
    }
    print("\n=== VERDICT ===")
    print(f"  clients Drive served media to : {media_clients or 'NONE'}")
    print(f"  ffmpeg direct worked          : "
          f"{[k for k, v in direct.items() if v['ok']] or 'no'}")
    if best:
        print(f"  SLICE-FETCHING WORKS via upstream={best[0]}: the frames are "
              f"the real footage and the read stayed at "
              f"{best[1]*100:.2f}% of the file.")
    else:
        print("  NOT PROVEN — no transport both returned the real footage and "
              "stayed small.")
    print("  (full report: probe_result.json)")

    with open("probe_result.json", "w") as f:
        json.dump(report, f, indent=2)
    return 0 if best else 1


if __name__ == "__main__":
    sys.exit(main())
