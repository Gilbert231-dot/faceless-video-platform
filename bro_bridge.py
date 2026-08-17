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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766
KEY_FILE = r"D:\Desktop\bro\.env"
ALLOWED_TASKS = ("disk", "files", "whoami", "uptime")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
BRO_MODEL = "openai/gpt-oss-120b"  # free tier, small prompts only (8K TPM)

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

# ---------- Bro's brain (direct Groq, compact prompt + safe terminal tools) ----------
#
# Why not opencode? opencode's own system prompt is ~8,312 tokens, which exceeds
# Groq's free-tier 8K TPM cap for every chat model (gpt-oss-120b/20b). Groq's
# compound models have 70K TPM but only accept the Responses API, which opencode
# does not use for Groq. So Bro calls Groq directly with a COMPACT persona prompt
# (well under the cap) plus a small set of SAFE terminal tools he can run.

BRO_SYSTEM = (
    "You are Bro, Gilbert's personal terminal AI agent on his Windows 10 laptop. "
    "Talk warm and plain-English like a close friend who knows his stuff. Keep answers concise. "
    "You can run SAFE read-only terminal commands via the run_terminal tool to check real facts "
    "(disk space, file listings, whoami, git status, etc.). NEVER run anything destructive "
    "(delete/rm/format/shutdown/install). NEVER reveal API keys or secrets. "
    "Laptop: Intel i3 dual-core, ~6 GB RAM, C: ~15 GB free, D: ~678 GB free. "
    "Projects: faceless-video-platform (Reddit stories -> narrated videos, GitHub Actions), "
    "cartoon-clipper, Get_stories, Get_video_link."
)

BRO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Run a SAFE read-only terminal command (dir, type, cd+pwd, where, git status/log/diff, wmic, tasklist, ipconfig, ping). Refuse anything that could change or destroy data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run, e.g. 'dir C:\\Users\\HP'"}
                },
                "required": ["command"]
            }
        }
    }
]

# Executables Bro may never run (checked against the FIRST token of the command).
FORBIDDEN_EXES = {
    "del", "rm", "rmdir", "rd", "format", "shutdown", "restart", "reg",
    "taskkill", "net", "diskpart", "erase", "move", "ren", "mkdir", "md",
    "copy", "xcopy", "robocopy", "mklink", "sc", "attrib", "cipher",
    "icacls", "takeown", "setx", "powershell", "pwsh", "wsl", "cmd",
    "msiexec", "dism", "sfc", "bcdedit", "fsutil", "mountvol", "subst",
    "diskpart", "regsvr32", "wmic", "certutil", "bitsadmin", "schtasks",
    "nltest", "gpupdate", "auditpol", "wevtutil", "vssadmin", "wusa",
}

# Git subcommands that change the repo (checked when the first token is git).
FORBIDDEN_GIT = {
    "push", "reset", "checkout", "clean", "rebase", "merge", "commit", "cherry-pick",
    "revert", "stash", "branch", "tag", "remote", "submodule", "apply", "am",
}


def safe_run_command(command):
    """Run a command ONLY if it's clearly read-only. Returns (ok, text)."""
    cmd = (command or "").strip()
    if not cmd:
        return False, "Empty command."
    low = cmd.lower()
    # Reject any command with & | ; (chaining / redirects).
    for bad in ["&", "|", ";", ">", "<"]:
        if bad in low:
            return False, f"Refused: command chaining/pipe/redirect '{bad}' is not allowed."
    # Check the executable (first token) - allow paths with separators but
    # still look at the bare name, so "type README.md" is fine but "rm -rf" isn't.
    first = low.split()[0] if low.split() else ""
    exe = first.split("\\")[-1].split("/")[-1].strip('"')
    if exe in FORBIDDEN_EXES:
        return False, f"Refused: '{exe}' could change data - not allowed."
    if exe == "git":
        sub = low.split()[1] if len(low.split()) > 1 else ""
        if sub in FORBIDDEN_GIT:
            return False, f"Refused: 'git {sub}' could change data - not allowed."
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=25,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        text = out if out else err
        if not text:
            text = f"(exit {proc.returncode}, no output)"
        return True, text[:3000]
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 25s."
    except Exception as e:
        return False, f"Could not run command: {e}"


def groq_chat(messages, key, max_tokens=2048):
    """One Groq chat completion call. Returns (text, error)."""
    body = json.dumps({
        "model": BRO_MODEL,
        "messages": messages,
        "tools": BRO_TOOLS,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL, data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Cloudflare (in front of Groq) returns 1010 to Python's default UA
            "User-Agent": "bro-bridge/1.0 (local laptop agent)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        return None, f"Groq API {e.code}: {detail}"
    except Exception as e:
        return None, f"Could not reach Groq: {e}"
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError):
        return None, f"Unexpected Groq response: {str(data)[:300]}"
    return msg, None


def run_bro(prompt):
    """Bro answers with tool use via Groq directly. Returns (text, error)."""
    key = load_key()
    if not key:
        return None, ("Bro has no Groq key. Add one to D:\\Desktop\\bro\\.env as "
                      "GROQ_API_KEY=gsk_... (or set the GROQ_API_KEY env var), "
                      "then restart this bridge.")
    messages = [{"role": "system", "content": BRO_SYSTEM},
                {"role": "user", "content": prompt}]
    steps = 0
    while steps < 5:
        steps += 1
        msg, error = groq_chat(messages, key)
        if error:
            return None, error
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            text = (msg.get("content") or "").strip()
            return (text or "Bro answered nothing. Try rephrasing."), None
        messages.append(msg)
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args = (fn.get("arguments") or "{}")
            try:
                parsed = json.loads(args) if isinstance(args, str) else (args or {})
            except json.JSONDecodeError:
                parsed = {}
            if name == "run_terminal":
                ok, text = safe_run_command(parsed.get("command", ""))
                result = text if ok else f"Refused: {text}"
            else:
                result = f"Unknown tool {name}"
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
    return None, "Bro kept using tools without finishing. Try a simpler question."

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
                "bro": bool(load_key()),
                "key": bool(load_key()),
                "port": PORT,
                "tasks": list(ALLOWED_TASKS),
                "model": BRO_MODEL,
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
