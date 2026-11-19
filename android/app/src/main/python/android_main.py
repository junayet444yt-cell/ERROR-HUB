"""Starts the downloader (Flask) inside the Android app, on 127.0.0.1."""
import os, subprocess, threading

_started = False


def _setup_ffmpeg(native_dir, files_dir):
    """The apk ships a static ffmpeg as libffmpeg.so (the only place Android lets an app run a program from).
    If it runs, put it on PATH as 'ffmpeg' so videos with separate sound (Reddit, Facebook HD, YouTube ...) can be merged."""
    lib = os.path.join(native_dir or "", "libffmpeg.so")
    if not os.path.exists(lib):
        return False
    try:
        b = os.path.join(files_dir, "bin")
        os.makedirs(b, exist_ok=True)
        link = os.path.join(b, "ffmpeg")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(lib, link)
        ok = subprocess.run([link, "-version"], capture_output=True, timeout=20).returncode == 0
        if ok:
            os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")
        return ok
    except Exception:
        return False


def start(port, cache_dir, static_dir, files_dir, native_dir=""):
    global _started
    if _started:
        return
    _started = True
    os.environ.update({"VCACHE": cache_dir, "STATIC_DIR": static_dir, "RATE_MAX": "100000", "MAX_JOBS": "2", "HOME": files_dir, "BLOCKED_SITES": "none", "ERRORHUB_ANDROID": "1", "ERRORHUB_PLATFORM": "apk", "DATA_DIR": files_dir})
    try:  # make sure HTTPS works on Android
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass
    _setup_ffmpeg(native_dir, files_dir)
    import app as server
    threading.Thread(target=lambda: server.serve("127.0.0.1", int(port)), daemon=True).start()
