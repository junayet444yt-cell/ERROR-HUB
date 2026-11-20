"""Starts the downloader (Flask) inside the Android app, on 127.0.0.1."""
import os, subprocess, threading, stat

_started = False


def _setup_ffmpeg(native_dir, files_dir):
    """Enable FFmpeg only after verifying that the packaged executable really runs on this Android device."""
    candidates = []
    if native_dir:
        candidates.append(os.path.join(native_dir, "libffmpeg.so"))
        candidates.append(os.path.join(native_dir, "ffmpeg"))
    bin_dir = os.path.join(files_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    # Android APKs extract jniLibs into nativeLibraryDir. Some devices allow direct execution,
    # while others need an app-private copy. Test both instead of trusting that the file exists.
    for source in candidates[:]:
        if not os.path.isfile(source):
            continue
        try:
            os.chmod(source, os.stat(source).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            result = subprocess.run([source, "-version"], capture_output=True, timeout=25)
            if result.returncode == 0:
                os.environ["PATH"] = os.path.dirname(source) + os.pathsep + os.environ.get("PATH", "")
                os.environ["FFMPEG_LOCATION"] = source
                return True
        except Exception:
            pass
        # Copy fallback for devices that reject execution through the native-library symlink.
        try:
            target = os.path.join(bin_dir, "ffmpeg")
            with open(source, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
            os.chmod(target, 0o755)
            result = subprocess.run([target, "-version"], capture_output=True, timeout=25)
            if result.returncode == 0:
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                os.environ["FFMPEG_LOCATION"] = target
                return True
        except Exception:
            pass

    os.environ.pop("FFMPEG_LOCATION", None)
    return False


def start(port, cache_dir, static_dir, files_dir, native_dir=""):
    global _started
    if _started:
        return
    _started = True
    os.environ.update({"VCACHE": cache_dir, "STATIC_DIR": static_dir, "RATE_MAX": "100000", "MAX_JOBS": "2", "HOME": files_dir, "BLOCKED_SITES": "none", "COOKIES_FILE": os.path.join(files_dir, "cookies.txt"), "ERRORHUB_ANDROID": "1", "ERRORHUB_PLATFORM": "apk", "DATA_DIR": files_dir})
    try:  # make sure HTTPS works on Android
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass
    _setup_ffmpeg(native_dir, files_dir)
    import app as server
    threading.Thread(target=lambda: server.serve("127.0.0.1", int(port)), daemon=True).start()
