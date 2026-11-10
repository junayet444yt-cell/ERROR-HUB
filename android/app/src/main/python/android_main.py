"""Starts the downloader (Flask) inside the Android app, on 127.0.0.1."""
import os, threading

_started = False


def start(port, cache_dir, static_dir, files_dir):
    global _started
    if _started:
        return
    _started = True
    os.environ.update({"VCACHE": cache_dir, "STATIC_DIR": static_dir, "RATE_MAX": "100000", "MAX_JOBS": "2", "HOME": files_dir, "BLOCKED_SITES": "none"})
    try:  # make sure HTTPS works on Android
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass
    import app as server
    threading.Thread(target=lambda: server.app.run(host="127.0.0.1", port=int(port), threaded=True, use_reloader=False), daemon=True).start()
