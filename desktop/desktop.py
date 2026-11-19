"""ERROR HUB DOWNLODER - Windows launcher.
Shows a small start screen at once, loads the downloader in the background, then opens it in its own app window
(Microsoft Edge / Chrome app mode)."""
import os, sys, socket, subprocess, threading, time, webbrowser
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # running from source: find app.py
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def res(*p):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # frozen: bundle folder; source: repo root
    return os.path.join(base, *p)


# windowed exe has no console: give print()/logging somewhere to go
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

os.environ["PATH"] = res("") + os.pathsep + os.environ.get("PATH", "")  # bundled ffmpeg / ffprobe / deno
home = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ERROR-HUB-DOWNLODER")
os.makedirs(home, exist_ok=True)
exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
for c in (os.path.join(exe_dir, "cookies.txt"), os.path.join(home, "cookies.txt")):  # optional: your own login cookies
    if os.path.exists(c):
        os.environ["COOKIES_FILE"] = c
        break
os.environ.update({"VCACHE": os.path.join(home, "cache"), "RATE_MAX": "100000", "MAX_JOBS": "3",
                   "DESKTOP_IDLE_EXIT": "1800", "ERRORHUB_PLATFORM": "exe", "DATA_DIR": home, "BLOCKED_SITES": "none",
                   "STATIC_DIR": res("static")})

# the same port every time = the browser keeps your settings and background (they are stored per address)
PREFERRED_PORT = 47821

SPLASH = """<!doctype html><html><head><meta charset="utf-8"><title>ERROR HUB DOWNLODER</title>
<style>
html,body{height:100%;margin:0;background:#07040f;color:#e9e4ff;font:600 15px system-ui,Segoe UI,sans-serif;overflow:hidden}
body{display:grid;place-items:center;background:radial-gradient(900px 500px at 85% -10%,#7a1b66 0,transparent 60%),radial-gradient(800px 600px at 10% 110%,#2a1470 0,transparent 60%),#07040f}
.b{text-align:center}.t{font:800 30px system-ui,Segoe UI,sans-serif;letter-spacing:.16em;background:linear-gradient(90deg,#b58bff,#5cc8ff);-webkit-background-clip:text;background-clip:text;color:transparent}
.s{margin:22px auto 14px;width:46px;height:46px;border-radius:50%;border:3px solid #2a2058;border-top-color:#5cc8ff;animation:r 1s linear infinite;box-shadow:0 0 24px #5cc8ff55}
@keyframes r{to{transform:rotate(360deg)}}p{opacity:.7;margin:0;letter-spacing:.08em}
</style></head><body><div class="b"><div class="t">ERROR HUB</div><div class="s"></div><p id="m">Starting…</p></div>
<script>
var T="http://127.0.0.1:__PORT__/",t0=Date.now();
function go(){fetch(T+"health",{mode:"no-cors",cache:"no-store"}).then(function(){location.replace(T)}).catch(function(){
 if(Date.now()-t0>90000){document.getElementById("m").textContent="Could not start. Close this window and open the app again.";return}
 setTimeout(go,250)})}
go();
</script></body></html>"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_free(p):
    with socket.socket() as s:
        if os.name != "nt":      # (on Windows this option would let us steal a port that is really in use)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", p))
            return True
        except OSError:
            return False


def pick_app_port():
    for p in range(PREFERRED_PORT, PREFERRED_PORT + 8):
        if port_free(p):
            return p
    return free_port()


def app_running(p):
    try:
        return urllib.request.urlopen("http://127.0.0.1:%d/health" % p, timeout=1.5).read().strip() == b"ok"
    except Exception:
        return False


def start_splash(app_port):
    page = SPLASH.replace("__PORT__", str(app_port)).encode("utf-8")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/favicon"):
                self.send_response(204); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def browser_exe():
    pf, pf86, la = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
    for p in (os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
              os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
              os.path.join(la, r"Google\Chrome\Application\chrome.exe")):
        if p and os.path.exists(p):
            return p
    return None


def open_window(exe, url):
    """Own profile folder = own window; when that window is closed the app stops."""
    if not exe:
        webbrowser.open(url)
        return None
    return subprocess.Popen([exe, "--app=" + url, "--user-data-dir=" + os.path.join(home, "profile"),
                             "--no-first-run", "--no-default-browser-check", "--window-size=1100,800",
                             "--disable-features=Translate,msEdgeSidebarV2"])


def main():
    exe = browser_exe()
    if app_running(PREFERRED_PORT):          # already running: just open one more window
        open_window(exe, "http://127.0.0.1:%d/" % PREFERRED_PORT)
        return
    port = pick_app_port()
    p = open_window(exe, "http://127.0.0.1:%d/" % start_splash(port))   # window appears at once
    import app as server  # noqa: E402  (the slow part: loads while the start screen is showing)
    threading.Thread(target=lambda: server.serve("127.0.0.1", port), daemon=True).start()
    if p:
        p.wait()
        os._exit(0)
    while True:                              # no Edge/Chrome found: normal browser tab; the server closes itself after 30 min unused
        time.sleep(60)


if __name__ == "__main__":
    main()
