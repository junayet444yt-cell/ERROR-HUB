"""ERROR HUB DOWNLODER - Windows launcher.
Starts the downloader on this PC (127.0.0.1) and opens it in its own app window (Microsoft Edge / Chrome app mode)."""
import os, sys, socket, subprocess, threading, time, webbrowser


def res(*p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *p)


# windowed exe has no console: give print()/logging somewhere to go
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

os.environ["PATH"] = res("") + os.pathsep + os.environ.get("PATH", "")  # bundled ffmpeg / ffprobe
home = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ERROR-HUB-DOWNLODER")
os.makedirs(home, exist_ok=True)
exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
for c in (os.path.join(exe_dir, "cookies.txt"), os.path.join(home, "cookies.txt")):  # optional: your own login cookies
    if os.path.exists(c):
        os.environ["COOKIES_FILE"] = c
        break
os.environ.update({"VCACHE": os.path.join(home, "cache"), "RATE_MAX": "100000", "MAX_JOBS": "3",
                   "DESKTOP_IDLE_EXIT": "1800", "STATIC_DIR": res("static")})

import app as server  # noqa: E402  (after the environment is ready)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port, secs=40):
    end = time.time() + secs
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def browser_exe():
    pf, pf86, la = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
    for p in (os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
              os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
              os.path.join(la, r"Google\Chrome\Application\chrome.exe")):
        if p and os.path.exists(p):
            return p
    return None


def main():
    port = free_port()
    threading.Thread(target=lambda: server.app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False), daemon=True).start()
    if not wait_port(port):
        return
    url = "http://127.0.0.1:%d/" % port
    exe = browser_exe()
    if exe:
        # own profile folder = own window; when that window is closed the app stops
        p = subprocess.Popen([exe, "--app=" + url, "--user-data-dir=" + os.path.join(home, "profile"),
                              "--no-first-run", "--no-default-browser-check", "--window-size=1100,800"])
        p.wait()
        os._exit(0)
    webbrowser.open(url)  # no Edge/Chrome found: normal browser tab; the server closes itself after 30 min without use
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
