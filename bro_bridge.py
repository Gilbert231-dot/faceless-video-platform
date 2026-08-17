"""
bro_bridge.py — a tiny local server that lets the Agent Room page summon Bro.

The Agent Room (dashboard/agent_room.html) is hosted on GitHub Pages, but Bro
runs on THIS laptop (opencode + your global AGENTS.md brain). This bridge is
the connector: it runs locally on 127.0.0.1 and answers small HTTP calls from
the room page so you can ask Bro things right from the room.

Endpoints:
  GET  /api/status            -> {"ok": true, "bro": true|false, "key": true|false, ...}
  POST /api/ask   {"prompt"}  -> runs: opencode run --format json "<prompt>"
                                 returns Bro's plain-text answer (or an error)
  GET  /api/task?kind=disk|files|whoami&path=...   -> instant laptop tasks (no AI needed)

Usage:
  python bro_bridge.py            # serves on http://127.0.0.1:8766
  python bro_bridge.py --port 9000

Bro's Groq key is read from (first match wins):
  1. the GROQ_API_KEY environment variable
  2. D:\\Desktop\\bro\\.env   (a file with one line: GROQ_API_KEY=gsk_...)

No dependencies — pure Python stdlib.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766
OPENCODE = os.path.join(os.environ.get("APPDATA", r"C:\Users\HP\AppData\Roaming"), "npm", "opencode.cmd")
KEY_FILE = r"D:\Desktop\bro\.env"
ALLOWED_TASKS = ("disk", "files", "whoami", "uptime")

# ---------- key loading ----------

def load_key():
    """Return the Groq API key or None. Never prints it."""
    env = os.environ.get("GROQ_API_KEY", "").strip()
    if env:
        return env
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GROQ_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None

# ---------- quick tasks (pure Python, no AI, instant) ----------

def task_disk():
    lines = []
    for drive in "CDEFGH":
        root = f"{drive}:\\"
        try:
            total, used, free = shutil.disk_usage(root)
            gb = 1024 ** 3
            lines.append(f"{drive}:\\  total {total / gb:8.1f} GB  used {used / gb:8.1f} GB  free {free / gb:8.1f} GB")
        except OSError:
            continue
    return "\n".join(lines) if lines else "No drives found."


def task_files(path):
    if not path:
        path = r"D:\Desktop\faceless_project\faceless-video-platform"
    try:
        names = sorted(os.listdir(path))
    except OSError as e:
        return f"Could not list {path}: {e}"
    items = []
    for n in names[:40]:
        full = os.path.join(path, n)
        kind = "DIR " if os.path.isdir(full) else "FILE"
        try:
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            size_s = f"{size:,} B" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        except OSError:
            size_s = "?"
        items.append(f"{kind}  {size_s:>12}  {n}")
    if len(names) > 40:
        items.append(f"... and {len(names) - 40} more")
    return f"{path}  ({len(names)} entries)\n" + "\n".join(items)


def task_whoami():
    try:
        out = subprocess.run(
            ["whoami"], capture_output=True, text=True, timeout=10,
            creationflags=0x08000000  if os.name == "nt" else 0,  # CREATE_NO_WINDOW
        )
        user = out.stdout.strip() or "unknown"
    except Exception:
        user = "unknown"
    import socket
    host = socket.gethostname()
    py = sys.version.split()[0]
    return f"User: {user}\nHost: {host}\nPython: {py}\nOS: Windows 10 (per Bro's brain)"


def task_uptime():
    try:
        out = subprocess.run(
            ["systeminfo", "/FO", "CSV"], capture_output=True, text=True, timeout=25,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        for line in out.stdout.splitlines():
            if "System Boot Time" in line or '"System Boot Time"' in line:
                return "System Boot Time: " + line.split(",")[-1].strip('"')
        return out.stdout[:200] if out.stdout else "Could not read boot time."
    except Exception as e:
        return f"Could not read boot time: {e}"


def run_task(kind, path):
    if kind == "disk":
        return task_disk()
    if kind == "files":
        return task_files(path)
    if kind == "whoami":
        return task_whoami()
    if kind == "uptime":
        return task_uptime()
    return f"Unknown task '{kind}'. Known: {', '.join(ALLOWED_TASKS)}"

# ---------- Bro (opencode) ----------

def run_bro(prompt):
    """Run opencode with the prompt, return (text, error)."""
    key = load_key()
    if not key:
        return None, ("Bro has no Groq key. Add one to D:\\Desktop\\bro\\.env as "
                      "GROQ_API_KEY=gsk_... (or set the GROQ_API_KEY env var), "
                      "then restart this bridge.")
    if not os.path.exists(OPENCODE):
        return None, f"opencode not found at {OPENCODE}. Run setup_bro.bat first."
    env = dict(os.environ)
    env["GROQ_API_KEY"] = key
    # --format json prints one JSON event per line; parse the assistant text.
    cmd = [OPENCODE, "run", "--format", "json", prompt]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=env,
            cwd=r"D:\Desktop\faceless_project\faceless-video-platform",
            creationflags=0x08000000 if os.name == "nt" else 0,  # no console window
        )
    except subprocess.TimeoutExpired:
        return None, "Bro took too long (>5 min) and the bridge gave up. Try a shorter question."
    except Exception as e:
        return None, f"Could not launch opencode: {e}"

    parts = []
    error = None
    for raw in proc.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "message":
            msg = ev.get("message") or {}
            role = msg.get("role")
            content = msg.get("content") or []
            if role == "assistant":
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                        parts.append(c["text"])
        elif t == "error":
            err = ev.get("error") or {}
            if isinstance(err, dict):
                data = err.get("data") or {}
                error = data.get("message") or err.get("message") or "unknown error"
            else:
                error = str(err)
    text = "".join(parts).strip()
    if text:
        return text, None
    if error:
        return None, f"Bro hit an error: {error}"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return None, f"opencode exited {proc.returncode}: {tail[-1] if tail else 'no output'}"
    return None, "Bro returned an empty answer. Try rephrasing."

# ---------- HTTP server ----------

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/status":
            self._send(200, {
                "ok": True,
                "bro": os.path.exists(OPENCODE),
                "key": bool(load_key()),
                "port": PORT,
                "tasks": list(ALLOWED_TASKS),
            })
            return
        if url.path == "/api/task":
            q = urllib.parse.parse_qs(url.query)
            kind = (q.get("kind") or ["disk"])[0]
            path = (q.get("path") or [""])[0]
            if kind not in ALLOWED_TASKS:
                self._send(400, {"ok": False, "error": f"Unknown task '{kind}'"})
                return
            self._send(200, {"ok": True, "kind": kind, "text": run_task(kind, path)})
            return
        if url.path == "/" or url.path == "/room":
            self._serve_room()
            return
        if url.path == "/agent_state.json":
            self._serve_state()
            return
        self._send(404, {"ok": False, "error": "Not found. Try /api/status, /api/task, /api/ask, or /room"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/api/ask":
            self._send(404, {"ok": False, "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"ok": False, "error": "Bad JSON body"})
            return
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            self._send(400, {"ok": False, "error": "Missing 'prompt'"})
            return
        text, error = run_bro(prompt)
        if error:
            self._send(500, {"ok": False, "error": error})
            return
        self._send(200, {"ok": True, "answer": text})

    def _serve_room(self):
        here = os.path.dirname(os.path.abspath(__file__))
        room = os.path.join(here, "dashboard", "agent_room.html")
        try:
            with open(room, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            self._send(500, {"ok": False, "error": f"agent_room.html not found at {room}"})
            return
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_state(self):
        here = os.path.dirname(os.path.abspath(__file__))
        state = os.path.join(here, "dashboard", "agent_state.json")
        try:
            with open(state, encoding="utf-8") as f:
                data = f.read()
        except OSError:
            self._send(500, {"ok": False, "error": f"agent_state.json not found at {state}"})
            return
        self._send(200, data.encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")


def main():
    port = PORT
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except (ValueError, IndexError):
            pass
    # ASCII-safe output (Windows cp1252 consoles choke on emoji/em-dashes)
    print(f"[OK] Bro bridge listening on http://127.0.0.1:{port}")
    print(f"     Room page:  http://127.0.0.1:{port}/room")
    if load_key():
        print("     Bro (AI):   READY (Groq key found)")
    else:
        print(f"     Bro (AI):   NO KEY - add GROQ_API_KEY=<key> to {KEY_FILE} and restart")
    print("     Press Ctrl+C to stop.")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as e:
        print(f"Could not bind port {port}: {e}. Try a different --port.")


if __name__ == "__main__":
    main()
