"""
ERROR HUB DOWNLODER - public-ready version.
Paste a video link -> the server fetches it -> the video plays in the page and can be downloaded.
"""
import os, re, json, glob, shutil, subprocess, tempfile, time, uuid, base64, socket, secrets, hmac, hashlib, ipaddress, mimetypes, threading
from functools import wraps
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify, send_file, session, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

_yt = None
_cffi_mod = False


def ytdlp():
    """yt-dlp is big: import it on first use (and in a background thread at start) so the app opens fast."""
    global _yt
    if _yt is None:
        import yt_dlp as m
        _yt = m
    return _yt


def get_cffi():
    global _cffi_mod
    if _cffi_mod is False:
        try:
            from curl_cffi import requests as m
            _cffi_mod = m
        except Exception:
            _cffi_mod = None
    return _cffi_mod

# ---------- settings (all can be changed with Environment Variables on Render) ----------
PASSWORD = os.environ.get("ACCESS_PASSWORD", "")            # empty = public, set = private
MAX_MB = int(os.environ.get("MAX_MB", "2048"))                # biggest single video
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "300"))       # longest video
TTL = int(os.environ.get("KEEP_MINUTES", "20")) * 60         # how long files stay on the server
MAX_CACHE_MB = int(os.environ.get("MAX_CACHE_MB", "4500"))   # total disk used by saved videos
MAX_JOBS = int(os.environ.get("MAX_JOBS", "2"))              # downloads running at the same time
RATE_MAX = int(os.environ.get("RATE_MAX", "8"))              # requests per person...
RATE_WINDOW = int(os.environ.get("RATE_WINDOW_MIN", "10")) * 60  # ...per this many minutes
PROXY = os.environ.get("PROXY_URL", "")                      # optional proxy for blocked sites
USE_COOKIES = os.environ.get("USE_COOKIES", "") == "1"       # off by default on a public site
CONTACT = os.environ.get("CONTACT_EMAIL", "")                # shown in the page footer
COOKIES = os.environ.get("COOKIES_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
CACHE = os.environ.get("VCACHE") or os.path.join(tempfile.gettempdir(), "vcache")  # works on Linux, Windows and Android
os.makedirs(CACHE, exist_ok=True)
ANDROID = os.environ.get("ERRORHUB_ANDROID") == "1"

# No site is blocked by default (YouTube, adult sites, everything is allowed).
# To block something set BLOCKED_SITES (comma separated, "none" = block nothing).
_default_block = "none"
_raw_block = os.environ.get("BLOCKED_SITES", _default_block).strip().lower()
BLOCKED = tuple(d.strip() for d in _raw_block.split(",") if d.strip() and d.strip() != "none")
BLOCKED_EXTRACTORS = tuple(sorted({d.split(".")[0] for d in BLOCKED})) or ("\0",)
_LABELS = {"youtube": "YouTube", "youtu": "YouTube", "youtube-nocookie": "YouTube", "facebook": "Facebook",
           "fb": "Facebook", "instagram": "Instagram"}
BLOCKED_NAMES = list(dict.fromkeys(_LABELS.get(d.split(".")[0], d.split(".")[0].title()) for d in BLOCKED))
BLOCK_MSG = (" and ".join([", ".join(BLOCKED_NAMES[:-1]), BLOCKED_NAMES[-1]]) if len(BLOCKED_NAMES) > 1
             else (BLOCKED_NAMES[0] if BLOCKED_NAMES else "")) + " links are not supported on this site."

# Cookies: either upload cookies.txt next to app.py, or put the file text (base64) in the COOKIES_B64 setting.
if os.environ.get("COOKIES_B64"):
    try:
        COOKIES = os.path.join(tempfile.gettempdir(), "cookies.txt")
        with open(COOKIES, "wb") as _f:
            _f.write(base64.b64decode(os.environ["COOKIES_B64"]))
    except Exception:
        pass

app = Flask(__name__, static_folder=os.environ.get("STATIC_DIR") or "static", static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
# Stable secret: logins survive a restart / sleep (a random key would log everybody out each time).
app.secret_key = os.environ.get("SECRET_KEY") or (
    hashlib.sha256(("evd|" + PASSWORD).encode()).hexdigest() if PASSWORD else secrets.token_hex(32))
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)

files = {}
hits = {}
lock = threading.Lock()
slots = threading.BoundedSemaphore(MAX_JOBS)


@app.after_request
def security_headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    if request.path in ("/", "/api/status", "/sw.js", "/manifest.webmanifest"):
        r.headers["Cache-Control"] = "no-cache"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
        "img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'")
    return r


def need_auth(f):
    @wraps(f)
    def w(*a, **k):
        if PASSWORD and not session.get("ok"):
            return jsonify(error="Login required"), 401
        return f(*a, **k)
    return w


def rate_ok(bucket):
    """Simple per-visitor limit so one person cannot overload a public server."""
    key = bucket + ":" + (request.remote_addr or "?")
    now = time.time()
    with lock:
        recent = [t for t in hits.get(key, []) if now - t < RATE_WINDOW]
        if len(recent) >= RATE_MAX:
            hits[key] = recent
            return False
        recent.append(now)
        hits[key] = recent
        if len(hits) > 5000:
            for k in [k for k, v in hits.items() if not v or now - v[-1] > RATE_WINDOW]:
                hits.pop(k, None)
    return True


def is_blocked(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in BLOCKED)


def url_is_safe(u):
    """Only public internet addresses - never the server's own network."""
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        for info in socket.getaddrinfo(p.hostname, None):
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
    except Exception:
        return False
    return True


def drop(vid):
    for f in glob.glob(os.path.join(CACHE, vid + ".*")):
        try: os.remove(f)
        except OSError: pass
    files.pop(vid, None)


def cache_size_mb():
    return sum(os.path.getsize(f) for f in glob.glob(os.path.join(CACHE, "*")) if os.path.isfile(f)) / 1048576


def cleanup():
    now = time.time()
    with lock:
        for k in [k for k, v in files.items() if now - v["ts"] > TTL]:
            drop(k)
        for f in glob.glob(os.path.join(CACHE, "*")):          # leftovers from failed downloads
            try:
                if now - os.path.getmtime(f) > TTL + 600:
                    os.remove(f)
            except OSError:
                pass
        while cache_size_mb() > MAX_CACHE_MB and files:        # disk full: remove the oldest first
            drop(min(files, key=lambda k: files[k]["ts"]))


def cleaner():
    while True:
        time.sleep(60)
        try: cleanup()
        except Exception: pass


threading.Thread(target=cleaner, daemon=True).start()


# ---------- keep the server awake (Render free sleeps after 15 min without visitors) ----------
SELF_URL = (os.environ.get("KEEPALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")


def keepalive():
    import urllib.request
    while True:
        time.sleep(240)  # every 4 minutes
        try:
            urllib.request.urlopen(urllib.request.Request(SELF_URL + "/health", headers={"User-Agent": "keepalive"}),
                                   timeout=25).read()
        except Exception:
            pass


if SELF_URL:
    threading.Thread(target=keepalive, daemon=True).start()


# ---------- optional anonymous usage stats (for YOUR admin panel; see admin-worker/) ----------
# Sends only: random install id, platform (exe/apk/web), app version, event name, site name (e.g. youtube.com), error kind.
# Never sends video links, IP addresses or personal data. Off unless TELEMETRY_URL is set. User opt-out: NO_TELEMETRY=1
# or an empty file called "no-telemetry" inside the app data folder.
APP_VERSION = os.environ.get("ERRORHUB_VERSION", "30")
PLATFORM = os.environ.get("ERRORHUB_PLATFORM", "web")          # exe / apk / web
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(tempfile.gettempdir(), "errorhub-data")
os.makedirs(DATA_DIR, exist_ok=True)


def _tel_conf():
    url, key = os.environ.get("TELEMETRY_URL", ""), os.environ.get("TELEMETRY_KEY", "")
    if not url:
        try:
            with open(os.path.join(app.static_folder, "telemetry.json"), encoding="utf-8") as f:
                c = json.load(f)
            url, key = str(c.get("url", "")), str(c.get("key", ""))
        except Exception:
            pass
    if os.environ.get("NO_TELEMETRY") or os.path.exists(os.path.join(DATA_DIR, "no-telemetry")):
        return "", ""
    url = url.strip().rstrip("/")
    return (url, key.strip()) if url.startswith(("https://", "http://127.0.0.1")) and key.strip() else ("", "")


TEL_URL, TEL_KEY = _tel_conf()


def _install_id():
    p = os.path.join(DATA_DIR, "install_id")
    try:
        with open(p) as f:
            v = f.read().strip()
        if re.fullmatch(r"[a-f0-9]{16,64}", v):
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex
    try:
        with open(p, "w") as f:
            f.write(v)
    except OSError:
        pass
    return v


INSTALL_ID = _install_id() if PLATFORM != "web" else ""


def track(ev, host="", err=""):
    """Fire-and-forget: never slows or breaks a download."""
    if not TEL_URL:
        return
    try:
        uid = INSTALL_ID or hashlib.sha256(("%s|%s|%s" % (time.strftime("%Y-%m-%d"), request.remote_addr, app.secret_key)).encode()).hexdigest()[:16]
        body = json.dumps({"k": TEL_KEY, "id": uid, "ev": ev, "p": PLATFORM, "v": APP_VERSION, "h": host, "e": err}).encode()
    except Exception:
        return

    def send():
        try:
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(TEL_URL + "/t", data=body, headers={"Content-Type": "application/json", "User-Agent": "errorhub"}), timeout=8).read()
        except Exception:
            pass
    threading.Thread(target=send, daemon=True).start()


def site_of(url):
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def err_kind(msg, url):
    m = (msg or "").lower()
    yt = re.search(r"youtu(\.be|be\.com)", url.lower())
    if "does not pass filter" in m or "duration" in m or "live" in m:
        return "too_long_or_live"
    if yt and ANDROID:
        return "youtube_on_android"
    if yt and not shutil.which("deno"):
        return "no_deno"
    if re.search(r"sign in|not a bot|429|too many|login|log in|cookie|private|members|age|forbidden|403|blocked", m):
        return "blocked_or_login"
    if re.search(r"unsupported url|no video", m):
        return "unsupported"
    return "other"


if TEL_URL and PLATFORM != "web":
    def _beat():
        track("open")
        while True:
            time.sleep(600)
            track("hb")
    threading.Thread(target=_beat, daemon=True).start()


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
PACKED = re.compile(r"\}\('((?:[^'\\]|\\.)*)',(\d+),(\d+),'((?:[^'\\]|\\.)*)'\.split\('\|'\)")
MEDIA = re.compile(r"https?:(?:\\?/){2}[^\s\"'<>]+?\.(?:m3u8|mp4|webm|mov)(?:\?[^\s\"'<>]*)?", re.I)
# player settings such as  video_url: '...'   "file":"..."   contentUrl   hlsUrl   <source src=...>
KEYED = re.compile(r"(?:video_url|video_alt_url\d*|contentUrl|content_url|hlsUrl|hls|mp4|file|src|source|stream|url)[\"']?\s*[:=]\s*[\"']((?:function/\d+/)?https?://[^\"'\s<>]+)", re.I)
BAD_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".js", ".css", ".vtt", ".srt", ".json", ".html", ".php", ".ico", ".woff", ".woff2")


def unpack(src):
    """Decode p.a.c.k.e.d JavaScript that many embed players use to hide the video link."""
    out = []
    for m in PACKED.finditer(src):
        try:
            payload, radix, words = m.group(1).replace("\\'", "'"), int(m.group(2)), m.group(4).split("|")
            def dec(w):
                n = 0
                for ch in w:
                    n = n * radix + DIGITS.index(ch)
                return n
            def rep(mm):
                w = mm.group(0)
                try:
                    i = dec(w)
                    return words[i] if i < len(words) and words[i] else w
                except Exception:
                    return w
            out.append(re.sub(r"\b\w+\b", rep, payload))
        except Exception:
            continue
    return "\n".join(out)


def page_get(url, referer=None):
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        h["Referer"] = referer
    cffi = get_cffi()
    if cffi:
        kw = {"proxies": {"http": PROXY, "https": PROXY}} if PROXY else {}
        r = cffi.get(url, headers=h, impersonate="chrome", timeout=30, **kw)
        return r.status_code, r.text
    import urllib.request, urllib.error
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30)
        return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""


def expand(html):
    """Return the page text plus every hidden layer: packed JS, base64 strings, escaped characters."""
    text = html + "\n" + unpack(html)
    for b in re.findall(r"atob\(\s*[\"']([A-Za-z0-9+/=]{20,})[\"']", text):
        try:
            text += "\n" + base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore")
        except Exception:
            pass
    text = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("&amp;", "&").replace("\\/", "/")


def find_media(url, referer=None, depth=0, seen=None):
    """Fallback: open the embed page like a browser and collect real video links hidden inside it."""
    seen = seen if seen is not None else set()
    if url in seen or depth > 2:
        return []
    seen.add(url)
    status, html = page_get(url, referer)
    if status >= 400 and depth == 0:
        raise RuntimeError("the site blocked the server (HTTP %d)" % status)
    text = expand(html)
    found = [u for u in MEDIA.findall(text)]
    for rel in re.findall(r"[\"'(]([^\"'()\s]+\.(?:m3u8|mp4)(?:\?[^\"'()\s]*)?)[\"')]", text, re.I):
        if not rel.lower().startswith("http"):
            found.append(urljoin(url, rel))
    for k in KEYED.findall(text):
        k = re.sub(r"^function/\d+/", "", k).rstrip("/")
        path = urlparse(k).path.lower()
        if not path.endswith(BAD_EXT) and re.search(r"\.(?:mp4|m3u8|webm|mov)|get_file|/video|/stream|/media", k, re.I):
            found.append(k)
    found = [u for u in dict.fromkeys(found) if url_is_safe(u)]
    found.sort(key=lambda u: 0 if ".m3u8" in u.lower() else (1 if ".mp4" in u.lower() else 2))
    if found:
        return [(u, url) for u in found[:4]]
    for src in re.findall(r"<iframe[^>]+(?:data-src|src)=[\"']([^\"']+)", html, re.I)[:4]:
        src = urljoin(url, src)
        if src.startswith("http") and url_is_safe(src):
            try:
                r = find_media(src, url, depth + 1, seen)
                if r:
                    return r
            except Exception:
                pass
    return []


# ---------- X (Twitter): ask FxTwitter, then VxTwitter (no login needed), then yt-dlp ----------
# These are third-party services: the tweet link is sent to them. They can change or stop working at any time.
X_TWEET = re.compile(r"(?:^|\.)(?:x|twitter)\.com/([^/?#]+)/status(?:es)?/(\d+)", re.I)


def is_x(url):
    host = (urlparse(url).hostname or "").lower()
    return host in ("x.com", "twitter.com") or host.endswith((".x.com", ".twitter.com"))


def x_lookup(url):
    """Return (video_url, title, thumbnail, duration) for the first video of a tweet, or raise."""
    m = X_TWEET.search((urlparse(url).hostname or "") + urlparse(url).path)
    if not m:
        raise RuntimeError("not a tweet link")
    user, tid = m.group(1), m.group(2)
    last = "no lookup service answered"
    for api in ("https://api.fxtwitter.com/%s/status/%s", "https://api.vxtwitter.com/%s/status/%s"):
        try:
            code, text = page_get(api % (user, tid))
            if code >= 400 or not text:
                last = "lookup service answered HTTP %s" % code
                continue
            data = json.loads(text)
        except Exception as e:
            last = str(e)[:100]
            continue
        cands = []
        tw = data.get("tweet") or {}
        if tw:  # FxTwitter
            for t in (tw, tw.get("quote") or {}):
                for v in ((t.get("media") or {}).get("videos") or []):
                    cands.append((v.get("url"), (t.get("text") or "")[:90], v.get("thumbnail_url"), v.get("duration")))
        else:  # VxTwitter
            for v in (data.get("media_extended") or []):
                if v.get("type") in ("video", "gif"):
                    cands.append((v.get("url"), (data.get("text") or "")[:90], v.get("thumbnail_url"), (v.get("duration_millis") or 0) / 1000 or None))
        for u, title, thumb, dur in cands:
            if u and urlparse(u).hostname and urlparse(u).hostname.endswith("twimg.com"):  # only Twitter's own video servers
                return u, title or "X video", thumb, dur
        last = "this post has no video (or X hides it from the lookup services)"
    raise RuntimeError(last)


def download_direct(url, vid, referer="https://x.com/"):
    import urllib.request
    handlers = [urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})] if PROXY else []
    h = {"User-Agent": UA, "Referer": referer}
    p = urlparse(referer)
    if p.scheme and p.netloc:
        h["Origin"] = p.scheme + "://" + p.netloc
    r = urllib.request.build_opener(*handlers).open(urllib.request.Request(url, headers=h), timeout=30)
    if not url_is_safe(r.geturl()):
        raise RuntimeError("blocked redirect")
    ctype = (r.headers.get_content_type() or "").lower()
    if not (ctype.startswith("video/") or ctype in ("application/octet-stream", "application/mp4", "binary/octet-stream")):
        raise RuntimeError("link is not a video file (%s)" % ctype)
    path = os.path.join(CACHE, vid + ".mp4")
    total, limit = 0, MAX_MB * 1024 * 1024
    try:
        with open(path, "wb") as f:
            while True:
                chunk = r.read(512 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise RuntimeError("video is bigger than the %d MB limit" % MAX_MB)
                f.write(chunk)
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise
    return path


def x_download(url, vid):
    u, title, thumb, dur = x_lookup(url)
    download_direct(u, vid)
    return {"title": title, "thumbnail": thumb, "duration": dur, "extractor_key": "Twitter"}


AD_WORDS = re.compile(r"preview|trailer|teaser|promo|banner|sample|advert|/ads?/|[._/-]ads?[._/-]|vast|pre-?roll|intro|thumb|loop|short", re.I)


def probe_duration(src, referer=None):
    """Length in seconds of a video file or link (needs ffprobe), or None when it cannot be measured."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    cmd = [exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
    if src.startswith("http"):
        cmd += ["-protocol_whitelist", "http,https,tcp,tls,crypto", "-headers",
                "User-Agent: %s\r\n%s" % (UA, ("Referer: %s\r\n" % referer) if referer else "")]
    try:
        out = subprocess.run(cmd + [src], capture_output=True, text=True, timeout=25,
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout.strip().splitlines()
        return float(out[0]) if out and out[0] not in ("", "N/A") else None
    except Exception:
        return None


def rank_candidates(cands, with_dur=False):
    """A page often holds an ad, a preview or a trailer next to the real video. Longest video first,
    and links that look like ads/previews go last."""
    cands = [c for c in cands if not (BLOCKED and is_blocked(c[0]))][:6]
    if not cands:
        return []
    with ThreadPoolExecutor(max_workers=len(cands)) as ex:
        durs = list(ex.map(lambda c: probe_duration(c[0], c[1]), cands))
    scored = [(c, d) for c, d in zip(cands, durs)]
    scored.sort(key=lambda cd: (-(cd[1] or 0), 1 if AD_WORDS.search(cd[0][0]) else 0))
    return [(c, d) for c, d in scored] if with_dur else [c for c, d in scored]


def grab_candidate(direct, ref, vid):
    p = urlparse(ref)
    if ".m3u8" not in direct.lower():
        try:
            download_direct(direct, vid, ref)
            return {"title": (urlparse(ref).path.strip("/").split("/")[-1] or "video").replace("-", " ")[:80],
                    "thumbnail": None, "duration": None, "extractor_key": "Direct"}
        except Exception:
            drop(vid)
    return run_ydl(direct, vid, {"Referer": ref, "Origin": p.scheme + "://" + p.netloc})


QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "240", "144", "audio")


def pick_quality(q):
    q = str(q or "").strip().lower()
    return q if q in QUALITIES else "1080"


def build_format(q, has_ffmpeg):
    """yt-dlp format string for the quality the user chose (a height limit; it never upscales)."""
    q = pick_quality(q)
    if q == "audio":
        return "ba[ext=m4a]/ba/b"
    h = "" if q == "best" else "[height<=%s]" % q
    if not has_ffmpeg:  # no ffmpeg (Android): only ready-made single files
        return "b%s[ext=mp4]/b%s/b[ext=mp4]/b" % (h, h)
    if q == "best":     # true best (4K needs VP9/AV1, so do not force mp4 here)
        return "bv*+ba/b"
    if int(q) > 1080:   # 1440p / 4K only exist as VP9/AV1 (not mp4)
        return "bv*[height<=%s]+ba/b[height<=%s]/bv*+ba/b" % (q, q)
    return ("bv*%(h)s[ext=mp4]+ba[ext=m4a]/b%(h)s[ext=mp4]/bv*%(h)s+ba/b%(h)s/"
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b") % {"h": h}


def _ydl(url, vid, headers=None, yt_clients=None, quality="1080", fmt=None):
    opts = {
        "outtmpl": os.path.join(CACHE, vid + ".%(ext)s"),
        "format": build_format(quality, bool(shutil.which("ffmpeg"))),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "playlistend": 1,  # a page with several videos: never download them all
        "match_filter": ytdlp().utils.match_filter_func("!is_live & duration<=?%d" % (MAX_MINUTES * 60)),
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "concurrent_fragment_downloads": 4,
        "max_filesize": MAX_MB * 1024 * 1024,
        "http_headers": {"User-Agent": UA, **(headers or {})},
    }
    if get_cffi():  # look like a real Chrome browser so Cloudflare lets us in
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget("chrome")
        except Exception:
            pass
        opts["extractor_args"] = {"generic": {"impersonate": [""]}}
    if PROXY:
        opts["proxy"] = PROXY
    # YouTube needs a JavaScript runtime (deno) to solve its "n challenge", otherwise: "Requested format is not available"
    _deno = shutil.which("deno")
    if _deno:
        opts["js_runtimes"] = {"deno": {"path": _deno}}
    opts["remote_components"] = ["ejs:github"]  # fallback: fetch the challenge solver if the bundled one is missing/outdated
    if not shutil.which("ffmpeg"):  # no ffmpeg (Android build): nothing to merge
        opts.pop("merge_output_format", None)
    elif pick_quality(quality) == "audio":  # audio only: make an MP3
        opts.pop("merge_output_format", None)
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    host = (urlparse(url).hostname or "").lower()
    if yt_clients:  # YouTube: ask through a different "app" identity (some are not blocked on server IPs)
        opts.setdefault("extractor_args", {})["youtube"] = {"player_client": yt_clients}
    if os.path.exists(COOKIES) and (USE_COOKIES or any(d in host for d in ("instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch", "reddit.com", "redd.it"))):  # these sites often want a login on server IPs
        opts["cookiefile"] = COOKIES
    if os.environ.get("QJS_PATH"):  # phone: no deno, use the bundled QuickJS to solve YouTube's JavaScript puzzle
        opts["js_runtimes"] = {"quickjs": {"path": os.environ["QJS_PATH"]}}
    if fmt:  # last-resort format (any ready-made file)
        opts["format"] = fmt
        opts.pop("postprocessors", None)
    br = os.environ.get("COOKIES_BROWSER", "").strip().lower()  # Windows: use the login of Firefox / Edge / Chrome (file browser.txt)
    if br and "cookiefile" not in opts and any(d in host for d in ("instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch", "reddit.com", "redd.it")):
        opts["cookiesfrombrowser"] = (br,)
    with ytdlp().YoutubeDL(opts) as y:
        return y.extract_info(url, download=True)


YT_BLOCKED = re.compile(r"429|too many requests|sign in|not a bot|confirm you|forbidden|403|unavailable|blocked|po.?token|format|reload|page needs|unable to extract|player|challenge|nsig|signature|javascript|runtime", re.I)


def run_ydl(url, vid, headers=None, quality="1080"):
    host = (urlparse(url).hostname or "").lower()
    if not any(d in host for d in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return _ydl(url, vid, headers, None, quality)
    url = re.sub(r"([?&])si=[^&#]*&?", r"\1", url).rstrip("?&")  # drop the share-tracking part
    last = None
    # on a phone there is no JavaScript engine (deno): start with the client identities that do not need one
    order = ([None, ["android_vr"], ["web_safari"], ["tv"], ["mweb"]] if (ANDROID and os.environ.get("QJS_PATH")) else
             [["android_vr"], ["web_safari"], ["tv"], ["mweb"], None] if ANDROID
             else [None, ["tv", "web_safari"], ["android_vr"], ["web_embedded", "mweb"]])
    for clients in order:
        try:
            try:
                return _ydl(url, vid, headers, clients, quality)
            except Exception as e0:
                if re.search(r"format is not available|requested format|no video formats", str(e0), re.I) and pick_quality(quality) != "audio":
                    drop(vid)
                    return _ydl(url, vid, headers, clients, quality, fmt=("b/bv*+ba/best" if shutil.which("ffmpeg") else "b[ext=mp4]/b"))  # any ready-made file, any quality
                raise
        except Exception as e:
            last = e
            drop(vid)
            if not (ANDROID or YT_BLOCKED.search(str(e))):
                break  # a different problem (deleted video, too long ...): other identities will not help
    raise last


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    r = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    r.headers["Service-Worker-Allowed"] = "/"
    return r


@app.route("/icons/<path:name>")
def icons(name):
    r = send_from_directory(os.path.join(app.static_folder, "icons"), name)
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


# ---------- desktop app: close the server when nobody uses it any more ----------
LAST_PING = [0.0]


@app.route("/api/ping")
def ping():
    LAST_PING[0] = time.time()
    return "ok"


def _watchdog(idle):
    start = time.time()
    while True:
        time.sleep(10)
        lp = LAST_PING[0] or start
        if time.time() - lp > idle:
            os._exit(0)


if os.environ.get("DESKTOP_IDLE_EXIT"):
    threading.Thread(target=_watchdog, args=(int(os.environ["DESKTOP_IDLE_EXIT"]),), daemon=True).start()


@app.route("/health")
def health():
    return "ok"


@app.route("/api/diag")
def diag():
    return jsonify(yt_dlp=ytdlp().version.__version__, deno=bool(shutil.which("deno")),
                   ffmpeg=bool(shutil.which("ffmpeg")), cookies=os.path.exists(COOKIES), android=ANDROID)


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("unhandled error")
    return jsonify(error="Server error: " + re.sub(r"\s+", " ", str(e))[:160]), 500


@app.route("/api/status")
def status():
    return jsonify(need=bool(PASSWORD), ok=bool(session.get("ok")) or not PASSWORD, contact=CONTACT,
                   blocked=BLOCKED_NAMES, max_mb=MAX_MB, ffmpeg=bool(shutil.which("ffmpeg")),
                   js=bool(os.environ.get("QJS_PATH") or shutil.which("deno") or shutil.which("node")), stats=bool(TEL_URL))


@app.route("/api/login", methods=["POST"])
def login():
    if not PASSWORD:
        return jsonify(ok=True)
    if not rate_ok("login"):
        return jsonify(error="Too many tries. Wait a few minutes."), 429
    given = str((request.get_json(silent=True) or {}).get("password", ""))
    if hmac.compare_digest(given.encode(), PASSWORD.encode()):
        session.permanent = True
        session["ok"] = True
        return jsonify(ok=True)
    return jsonify(error="Wrong password"), 403


def reply(vid, info, alts, host="", quality=""):
    found = [f for f in glob.glob(os.path.join(CACHE, vid + ".*")) if not f.endswith((".part", ".ytdl"))]
    if not found:
        drop(vid)
        return jsonify(error="Video file not found (it may be too large - limit %d MB)." % MAX_MB), 400
    path = max(found, key=os.path.getsize)
    title = clean_title(info.get("title"))
    mine = info.get("duration") or probe_duration(path)
    others = []
    for i, (u, r, d) in enumerate(alts[:6]):  # other videos found on the same page (not the one we are returning)
        if mine and d and abs(d - mine) < 2:
            continue
        others.append({"n": i, "dur": d})
    with lock:
        files[vid] = {"path": path, "title": title, "ts": time.time(), "alts": alts[:6], "host": host}
    track("fetch_ok", host)
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    is_audio = ext in ("mp3", "m4a", "opus", "ogg", "wav")
    return jsonify(id=vid, url="/media/" + vid, title=title, thumbnail=info.get("thumbnail"),
                   duration=mine, size=os.path.getsize(path), others=others,
                   height=None if is_audio else info.get("height"), ext=ext, audio=is_audio, asked=quality)


@app.route("/api/alt", methods=["POST"])
@need_auth
def alt():
    body = request.get_json(silent=True) or {}
    item = files.get(str(body.get("id") or ""))
    try:
        u, r, d = item["alts"][int(body.get("n"))]
    except Exception:
        return jsonify(error="That choice expired. Paste the link again."), 400
    if not rate_ok("fetch"):
        return jsonify(error="Too many requests. Please wait a few minutes and try again."), 429
    if cache_size_mb() > MAX_CACHE_MB:
        return jsonify(error="Server storage is full right now. Try again in a few minutes."), 503
    if not slots.acquire(blocking=False):
        return jsonify(error="Server is busy with other downloads. Try again in a minute."), 503
    try:
        vid = uuid.uuid4().hex[:12]
        try:
            info = grab_candidate(u, r, vid)
        except Exception as e:
            drop(vid)
            return jsonify(error="Could not get that video: " + re.sub(r"\s+", " ", str(e).replace("ERROR: ", ""))[:160]), 400
        return reply(vid, info, item["alts"], item.get("host", ""))
    finally:
        slots.release()


REDDIT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def is_reddit(url):
    h = (urlparse(url).hostname or "").lower()
    return h.endswith("reddit.com") or h.endswith("redd.it")


def reddit_not_a_post(url):
    """True for a subreddit / user / home link: there is no single video to download there."""
    p = urlparse(url)
    h = (p.hostname or "").lower()
    if not h.endswith("reddit.com"):
        return False
    path = p.path.lower()
    return not ("/comments/" in path or re.search(r"/s/[a-z0-9]+/?$", path) or path.startswith("/video/") or path.startswith("/gallery/"))


def reddit_candidates(url):
    """No login needed for many 18+ posts when we look at the old.reddit page with the 'I am over 18' cookie:
    the page names the real video (v.redd.it, RedGIFs, Streamable ...). Returns links that yt-dlp can download."""
    import urllib.request
    p = urlparse(url)
    path = p.path
    m = re.search(r"/comments/([a-z0-9]+)", path, re.I)
    if not m:
        return []
    page = "https://old.reddit.com/comments/%s/" % m.group(1)
    req = urllib.request.Request(page, headers={"User-Agent": REDDIT_UA, "Cookie": "over18=1", "Accept-Language": "en-US,en;q=0.8"})
    try:
        html = urllib.request.build_opener().open(req, timeout=20).read(1500000).decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for vid in re.findall(r"https?://v\.redd\.it/([a-z0-9]{8,})", html, re.I):
        out.append("https://v.redd.it/%s/HLSPlaylist.m3u8" % vid)
    out += ["https://www.redgifs.com/watch/" + g.lower() for g in re.findall(r"redgifs\.com/(?:watch|ifr)/([a-zA-Z]+)", html)]
    out += re.findall(r"https?://(?:www\.)?(?:streamable\.com/[a-z0-9]+|gfycat\.com/[a-zA-Z]+|imgur\.com/(?:a/|gallery/)?[A-Za-z0-9]+)", html)
    return [u for u in dict.fromkeys(out) if url_is_safe(u)][:4]


FB_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uagent.php)"


def is_facebook(url):
    h = (urlparse(url).hostname or "").lower()
    return h.endswith("facebook.com") or h == "fb.watch" or h.endswith(".fb.watch") or h == "fb.me"


def facebook_preview(url):
    """Follow a Facebook share link and read its public preview tags (the same ones a chat app uses to show a preview).
    Returns (real_page_url, direct_video_url_or_None). No login needed for public videos."""
    import html as _html
    import urllib.request
    try:
        r = urllib.request.build_opener().open(urllib.request.Request(url, headers={"User-Agent": FB_UA, "Accept-Language": "en-US,en;q=0.8"}), timeout=20)
        final = r.geturl()
        page = r.read(1200000).decode("utf-8", "ignore")
    except Exception:
        return url, None
    def meta(prop):
        m = re.search(r'<meta[^>]+property=["\']%s["\'][^>]+content=["\']([^"\']+)' % re.escape(prop), page, re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']%s["\']' % re.escape(prop), page, re.I)
        return _html.unescape(m.group(1)) if m else None
    real = meta("og:url") or final
    real = real if (real and url_is_safe(real) and "login" not in real and "checkpoint" not in real) else url
    vid = meta("og:video:secure_url") or meta("og:video:url") or meta("og:video")
    if vid and not (url_is_safe(vid) and re.search(r"\.(mp4|m4v|mov)(\?|$)|video", vid, re.I)):
        vid = None
    return real, vid


def fix_reddit_url(url):
    """Reddit gives several kinds of links; turn them into ones yt-dlp understands."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host == "v.redd.it":  # bare video link -> its playlist (has the sound as a second stream; ffmpeg merges it)
        vid = p.path.strip("/").split("/")[0]
        return "https://v.redd.it/%s/HLSPlaylist.m3u8" % vid if vid else url
    if host == "redd.it":
        pid = p.path.strip("/").split("/")[0]
        return "https://www.reddit.com/comments/%s/" % pid if pid else url
    if host.endswith("reddit.com") and re.search(r"/s/[A-Za-z0-9]+/?$", p.path):  # share link: follow the redirect to the real post
        try:
            import urllib.request
            r = urllib.request.build_opener().open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15)
            final = r.geturl()
            if url_is_safe(final) and "/comments/" in final:
                return final.split("?")[0]
        except Exception:
            pass
    return url


@app.route("/api/fetch", methods=["POST"])
@app.route("/api/extract", methods=["POST"])
@need_auth
def fetch():
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify(error="Paste a video link first."), 400
    if len(url) > 2000 or not url_is_safe(url):
        return jsonify(error="Invalid link. Paste the full link starting with http:// or https://"), 400
    if BLOCKED and is_blocked(url):
        return jsonify(error=BLOCK_MSG), 403
    if not rate_ok("fetch"):
        return jsonify(error="Too many requests. Please wait a few minutes and try again."), 429
    url = re.sub(r"^(https?://)(?:www\.|mobile\.|m\.)?(?:fxtwitter|vxtwitter|fixupx|fixvx|twittpr)\.com", r"\1x.com", url, flags=re.I)
    if is_reddit(url) and reddit_not_a_post(url):
        return jsonify(error="This is a Reddit page (subreddit / profile), not one post. Open the post you want, tap Share > Copy link, and paste THAT link (it contains /comments/)."), 400
    url = fix_reddit_url(url)
    fb_direct = None
    if is_facebook(url):
        url = re.sub(r"[?&](mibextid|rdid|share_url|fs|sfnsn)=[^&#]*", "", url).replace("?&", "?").rstrip("?&")
        if re.search(r"/share/|fb\.watch|fb\.me", url, re.I):  # share links give "HTTP 400" in yt-dlp: use the real page
            url, fb_direct = facebook_preview(url)
    quality = pick_quality(body.get("quality"))
    ref_in = str(body.get("referer") or "").strip()
    if ref_in and (len(ref_in) > 2000 or not url_is_safe(ref_in)):
        ref_in = ""

    cleanup()
    if cache_size_mb() > MAX_CACHE_MB:
        return jsonify(error="Server storage is full right now. Try again in a few minutes."), 503
    if not slots.acquire(blocking=False):
        return jsonify(error="Server is busy with other downloads. Try again in a minute."), 503
    try:
        vid = uuid.uuid4().hex[:12]
        info, err, alts = None, None, []
        if is_x(url) and not os.path.exists(COOKIES):  # no login cookies: the lookup services are the best first try
            try:
                info = x_download(url, vid)
            except Exception as e:
                err = e
        if info is None:
            try:
                info = run_ydl(url, vid, {"Referer": ref_in} if ref_in else None, quality)
            except Exception as e:
                err = e
        if info is None and is_x(url) and os.path.exists(COOKIES):  # cookies were tried first, now the services
            try:
                info = x_download(url, vid)
            except Exception as e:
                err = err or e
        if info is None and is_facebook(url):  # Facebook: public preview tags often hold the plain video file
            try:
                direct = fb_direct or facebook_preview(url)[1]
                if direct:
                    drop(vid)
                    download_direct(direct, vid, "https://www.facebook.com/")
                    info = {"title": "Facebook video", "thumbnail": None, "duration": None, "extractor_key": "Direct"}
            except Exception as e6:
                err = err or e6
        if info is None and is_reddit(url):  # Reddit: try the real video links found on the old.reddit page
            try:
                for cu in reddit_candidates(url):
                    drop(vid)
                    try:
                        info = run_ydl(cu, vid, {"Referer": "https://www.reddit.com/"}, quality)
                        break
                    except Exception as e5:
                        err = err or e5
            except Exception:
                pass
        if info is None and not re.search(r"youtu(\.be|be\.com)", url.lower()):  # fallback: dig the real video link out of the page (not for YouTube pages)
            try:
                ranked = rank_candidates(find_media(url, ref_in or None), with_dur=True)
                alts = [(c[0], c[1], d) for c, d in ranked]
                for (direct, ref), _d in ranked:
                    drop(vid)  # clear any half-written file from the previous try
                    try:
                        info = grab_candidate(direct, ref, vid)
                        break
                    except Exception as e3:
                        err = e3
            except Exception as e2:
                err = e2
        elif info is not None and str(info.get("extractor_key", "")).lower() in ("generic", "html5"):
            # yt-dlp took the first video it saw on the page, which may be an ad or a short preview.
            # If the page holds a clearly longer video, use that one instead.
            try:
                got = [f for f in glob.glob(os.path.join(CACHE, vid + ".*")) if not f.endswith((".part", ".ytdl"))]
                d0 = probe_duration(max(got, key=os.path.getsize)) if got else None
                ranked = rank_candidates(find_media(url, ref_in or None), with_dur=True)
                alts = [(c[0], c[1], d) for c, d in ranked]
                if d0 is not None and d0 <= 120 and ranked:
                    (bu, br), d1 = ranked[0]
                    if d1 and d1 >= d0 + 30 and d1 >= d0 * 1.5:
                        drop(vid)
                        try:
                            info = grab_candidate(bu, br, vid)
                        except Exception:
                            drop(vid)
                            info = run_ydl(url, vid, {"Referer": ref_in} if ref_in else None, quality)  # keep the first result
            except Exception:
                pass
        if info is None:
            drop(vid)
            msg = str(err or "no video found on this page").replace("ERROR: ", "")
            msg = re.sub(r"\s+", " ", msg)[:200]
            raw_msg = msg
            if "does not pass filter" in msg or "duration" in msg:
                msg = "This video is too long (limit %d minutes) or is a live stream." % MAX_MINUTES
            elif re.search(r"youtu(\.be|be\.com)", url.lower()) and ANDROID:
                msg = "This YouTube video could not be loaded on the phone. A phone has no JavaScript engine, so YouTube works only for some videos here: try another video or link, update the app, or use the Windows EXE. Reason: " + msg[:110]
            elif re.search(r"youtu(\.be|be\.com)", url.lower()) and not shutil.which("deno"):
                msg = "YouTube needs the 'deno' JavaScript runtime and it is not installed on this machine. Reinstall with requirements.txt (it contains deno) or put deno.exe next to the app. Details: " + msg[:100]
            elif re.search(r"youtu(\.be|be\.com)", url.lower()) and re.search(r"429|too many|sign in|not a bot|confirm you|cookies|login|age|private|members|blocked|forbidden|403", msg, re.I):
                msg = "YouTube blocked this IP address (data-centre servers like Render are often blocked: sign-in / HTTP 429). The app already tried several YouTube client identities. Fix: run the Windows EXE on your own internet (works best), or add your YouTube cookies (COOKIES_B64 / cookies.txt), or set PROXY_URL to a residential proxy. Private, members-only and age-restricted videos always need cookies."
            elif re.search(r"reddit\.com|redd\.it", url.lower()) and re.search(r"login|log in|cookie|403|401|429|blocked|forbidden|nsfw|over 18|private|quarantin|too many|unable to download json|no video", msg, re.I):
                msg = "Reddit refused this request (18+/NSFW posts, private or quarantined subreddits and some networks need a login). Log in to Reddit and try again: Android app = Settings (gear) > Login > Reddit; Windows = put your own cookies.txt (or a browser.txt containing the word firefox) next to the exe. Text/image posts without a video cannot be downloaded."
            elif re.search(r"ffmpeg is not installed|ffmpeg not found|requested merging", msg, re.I):
                msg = "This video is stored as separate picture and sound and needs ffmpeg to join them, but ffmpeg is not working in this app. On the phone open Settings (gear): if 'ffmpeg' says NO, build the apk again (the ffmpeg download step must be green) and use a 64-bit phone. Meanwhile choose 360p or MP3."
            elif re.search(r"facebook\.com|fb\.watch", url.lower()) and re.search(r"login|cookie|private|not available|permission|log in", msg, re.I):
                msg = "Facebook says this video is not public (or wants a login). Public videos work. For friends-only or restricted ones add your cookies (COOKIES_B64 setting)."
            elif re.search(r"(twitter|x)\.com", url.lower()) and re.search(r"login|cookie|guest|sensitive|nsfw|age|protected|not authorized|no video|authentication", msg, re.I):
                msg = "Twitter/X wants a login for this post (sensitive or age-restricted content, or the guest limit). Add your cookies (COOKIES_B64 setting or cookies.txt) and try again. Posts without a video cannot be downloaded."
            elif "instagram" in url.lower() and re.search(r"login|cookie|rate-limit|not available|empty media|private", msg, re.I):
                msg = "Instagram wants a login. Add your cookies (COOKIES_B64 setting or cookies.txt) and try again. Private accounts need the cookies of an account that follows them."
            elif "403" in msg or "blocked" in msg:
                msg += " | Try again in a few minutes, or paste the link of the page that shows the video."
            track("fetch_fail", site_of(url), err_kind(raw_msg, url))
            return jsonify(error="Could not get the video: " + msg), 400
        if BLOCKED and str(info.get("extractor_key", "")).lower().startswith(BLOCKED_EXTRACTORS):
            drop(vid)
            return jsonify(error=BLOCK_MSG), 403
        return reply(vid, info, alts, site_of(url), quality)
    finally:
        slots.release()


def clean_title(t):
    t = re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", str(t or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return (t or "video")[:100]


def download_header(title, ext):
    import unicodedata
    from urllib.parse import quote
    t = re.sub(r'[\\/:*?"<>|]+', "", clean_title(title))
    t = re.sub(r"\s+", " ", t).strip(" .")[:80] or "video"
    full = t + ext
    stem = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", stem).strip(" .") or "video"
    plain = stem + ext
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (plain, quote(full, safe=""))


@app.route("/media/<vid>")
@need_auth
def media(vid):
    item = files.get(vid)
    if not item or not os.path.exists(item["path"]):
        return "File expired. Paste the link again.", 404
    item["ts"] = time.time()  # still in use: keep it
    rng = request.headers.get("Range", "")
    if request.args.get("dl") == "1" and (not rng or rng.startswith("bytes=0-")):
        track("download", item.get("host", ""))
    ext = os.path.splitext(item["path"])[1]
    r = send_file(item["path"], mimetype=mimetypes.guess_type(item["path"])[0] or "video/mp4", conditional=True)
    if request.args.get("dl") == "1":
        r.headers["Content-Disposition"] = download_header(item["title"], ext)
    return r


def serve(host, port):
    """Production-grade server for the desktop / Android app (the Flask dev server is only a fallback)."""
    try:
        from waitress import serve as _serve
    except ImportError:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
        return
    _serve(app, host=host, port=port, threads=8, channel_timeout=3600, cleanup_interval=30, ident="errorhub")


threading.Thread(target=lambda: (ytdlp(), get_cffi()), daemon=True).start()  # warm up while the page is loading


if __name__ == "__main__":
    serve("0.0.0.0", int(os.environ.get("PORT", 10000)))
