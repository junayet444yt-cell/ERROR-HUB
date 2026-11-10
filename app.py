"""
ERROR HUB DOWNLODER - public-ready version.
Paste a video link -> the server fetches it -> the video plays in the page and can be downloaded.
"""
import os, re, json, glob, shutil, subprocess, tempfile, time, uuid, base64, socket, secrets, hmac, hashlib, ipaddress, mimetypes, threading, datetime
from functools import wraps
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from flask import Flask, request, jsonify, send_file, session, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from curl_cffi import requests as cffi
except Exception:
    cffi = None

# ---------- settings (all can be changed with Environment Variables on Render) ----------
PASSWORD = os.environ.get("ACCESS_PASSWORD", "")            # empty = public, set = private
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")        # set this to turn on /admin (site usage stats, no personal data)
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

app = Flask(__name__, static_folder=os.environ.get("STATIC_DIR") or "static")
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

# ---------- anonymous usage stats for the admin panel ----------
# No link, title, IP or name is ever stored here - only counts per website domain and per day,
# plus a random per-browser/app id (DEVICES) used only to count how many separate installs are active.
SITE_STATS = {}     # domain -> [ok_count, fail_count]
DAILY = {}          # "YYYY-MM-DD" -> number of attempts that day
DEVICES = {}        # random id -> last time it was seen (prunes itself, oldest first, after 8 days)
JOBS_ACTIVE = [0]   # downloads running right now

def record_site(url, ok):
    host = (urlparse(url).hostname or "unknown").lower()
    host = re.sub(r"^(www|m|mobile)\.", "", host)
    day = datetime.date.today().isoformat()
    with lock:
        e = SITE_STATS.setdefault(host, [0, 0])
        e[0 if ok else 1] += 1
        DAILY[day] = DAILY.get(day, 0) + 1
        if len(SITE_STATS) > 500:
            for k in sorted(SITE_STATS, key=lambda k: sum(SITE_STATS[k]))[:50]:
                SITE_STATS.pop(k, None)



@app.errorhandler(Exception)
def any_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    msg = re.sub(r"\s+", " ", str(e))[:200] or e.__class__.__name__
    return jsonify(error="Server error: " + msg), 500


@app.after_request
def security_headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    if request.path in ("/", "/api/status", "/sw.js", "/manifest.webmanifest"):
        r.headers["Cache-Control"] = "no-cache"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'")
    return r


def need_auth(f):
    @wraps(f)
    def w(*a, **k):
        if PASSWORD and not session.get("ok"):
            return jsonify(error="Login required"), 401
        return f(*a, **k)
    return w


def admin_auth(f):
    @wraps(f)
    def w(*a, **k):
        if not ADMIN_PASSWORD:
            return jsonify(error="The admin panel is off. Set the ADMIN_PASSWORD setting on the server to turn it on."), 404
        if not session.get("admin_ok"):
            return jsonify(error="Admin login required"), 401
        return f(*a, **k)
    return w


_SKIP_TRACK = ("/api/admin", "/admin", "/media/", "/icons/", "/manifest.webmanifest", "/sw.js", "/health", "/api/ping")


@app.before_request
def track_device():
    """Counts active installs. Uses a random id already stored in the session cookie - never the IP or name."""
    if request.path.startswith(_SKIP_TRACK):
        return
    duid = session.get("duid")
    if not duid:
        duid = secrets.token_hex(8)
        session["duid"] = duid
        session.permanent = True
    now = time.time()
    with lock:
        DEVICES[duid] = now
        if len(DEVICES) > 20000:
            cutoff = now - 8 * 86400
            for k in [k for k, v in DEVICES.items() if v < cutoff]:
                DEVICES.pop(k, None)


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


OG_VIDEO = re.compile(r"<meta[^>]+(?:property|name)=[\"'](?:og:video(?::secure_url|:url)?|twitter:player:stream)[\"'][^>]*?content=[\"']([^\"']+)|<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"'](?:og:video(?::secure_url|:url)?|twitter:player:stream)[\"']", re.I)
PRIMARY = re.compile(r"(?:video_url|video_alt_url\d*|contentUrl|content_url|hlsUrl)[\"']?\s*[:=]\s*[\"']((?:function/\d+/)?https?://[^\"'\s<>]+)", re.I)
VIDTAG = re.compile(r"<(?:video|source)[^>]+src=[\"']([^\"']+)", re.I)
MEDIA_EXT = re.compile(r"\.(?:m3u8|mp4|webm|mov)(?:$|[?#])", re.I)


def find_media(url, referer=None, depth=0, seen=None):
    """Fallback: open the embed page like a browser and collect real video links hidden inside it.
    Links from the main player (og:video, video_url, <video> tag) come first, related videos last."""
    seen = seen if seen is not None else set()
    if url in seen or depth > 2:
        return []
    seen.add(url)
    status, html = page_get(url, referer)
    if status >= 400 and depth == 0:
        raise RuntimeError("the site blocked the server (HTTP %d)" % status)
    text = expand(html)
    items = []  # (tier, link): tier 0 = main player, 1 = <video> tag, 2 = anything else

    def add(tier, u):
        u = re.sub(r"^function/\d+/", "", (u or "").strip()).rstrip("/")
        if u:
            items.append((tier, urljoin(url, u)))

    for m in OG_VIDEO.findall(html):
        u = (m[0] or m[1]).replace("&amp;", "&")
        if MEDIA_EXT.search(u):
            add(0, u)
    for k in PRIMARY.findall(text):
        if not urlparse(k).path.lower().endswith(BAD_EXT):
            add(0, k)
    for sv in VIDTAG.findall(html):
        if MEDIA_EXT.search(sv):
            add(1, sv.replace("&amp;", "&"))
    for u in MEDIA.findall(text):
        add(2, u)
    for rel in re.findall(r"[\"'(]([^\"'()\s]+\.(?:m3u8|mp4)(?:\?[^\"'()\s]*)?)[\"')]", text, re.I):
        add(2, rel)
    for k in KEYED.findall(text):
        k = re.sub(r"^function/\d+/", "", k).rstrip("/")
        path = urlparse(k).path.lower()
        if not path.endswith(BAD_EXT) and re.search(r"\.(?:mp4|m3u8|webm|mov)|get_file|/video|/stream|/media", k, re.I):
            add(2, k)
    best = {}
    for i, (t, u) in enumerate(items):
        if u not in best:
            best[u] = (t, i)
    found = [u for u in sorted(best, key=lambda u: best[u]) if url_is_safe(u)]
    if found:
        return [(u, url) for u in found[:6]]
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
        out = subprocess.run(cmd + [src], capture_output=True, text=True, timeout=25).stdout.strip().splitlines()
        return float(out[0]) if out and out[0] not in ("", "N/A") else None
    except Exception:
        return None


def rank_candidates(cands, with_dur=False):
    """Keep the page's own order (main player first). Only very short clips and links that look like
    ads/previews are moved to the back. We no longer pick the 'longest' video: that often chose a
    related video instead of the one the link points to."""
    cands = [c for c in cands if not (BLOCKED and is_blocked(c[0]))][:6]
    if not cands:
        return []
    with ThreadPoolExecutor(max_workers=len(cands)) as ex:
        durs = list(ex.map(lambda c: probe_duration(c[0], c[1]), cands))
    scored = [(c, d, i) for i, (c, d) in enumerate(zip(cands, durs))]
    scored.sort(key=lambda t: (1 if (t[1] is not None and t[1] < 45) else 0,
                               1 if AD_WORDS.search(t[0][0]) else 0, t[2]))
    return [(c, d) for c, d, _ in scored] if with_dur else [c for c, d, _ in scored]


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


def _ydl(url, vid, headers=None, yt_clients=None):
    opts = {
        "outtmpl": os.path.join(CACHE, vid + ".%(ext)s"),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "playlistend": 1,  # a page with several videos: never download them all
        "match_filter": yt_dlp.utils.match_filter_func("!is_live & duration<=?%d" % (MAX_MINUTES * 60)),
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "concurrent_fragment_downloads": 4,
        "max_filesize": MAX_MB * 1024 * 1024,
        "http_headers": {"User-Agent": UA, **(headers or {})},
    }
    if cffi:  # look like a real Chrome browser so Cloudflare lets us in
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget("chrome")
        except Exception:
            pass
        opts["extractor_args"] = {"generic": {"impersonate": [""]}}
    if PROXY:
        opts["proxy"] = PROXY
    if not shutil.which("ffmpeg"):  # no ffmpeg (Android build): only take ready-made single files, nothing to merge
        opts["format"] = "b[ext=mp4][height<=1080]/b[ext=mp4]/b"
        opts.pop("merge_output_format", None)
    host = (urlparse(url).hostname or "").lower()
    if yt_clients:  # YouTube: ask through a different "app" identity (some are not blocked on server IPs)
        opts.setdefault("extractor_args", {})["youtube"] = {"player_client": yt_clients}
    if os.path.exists(COOKIES) and (USE_COOKIES or any(d in host for d in ("instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch"))):  # these sites often want a login on server IPs
        opts["cookiefile"] = COOKIES
    with yt_dlp.YoutubeDL(opts) as y:
        return y.extract_info(url, download=True)


YT_BLOCKED = re.compile(r"429|too many requests|sign in|not a bot|confirm you|forbidden|403|unavailable|blocked|po.?token|format", re.I)


def run_ydl(url, vid, headers=None):
    host = (urlparse(url).hostname or "").lower()
    if not any(d in host for d in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return _ydl(url, vid, headers)
    url = re.sub(r"([?&])si=[^&#]*&?", r"\1", url).rstrip("?&")  # drop the share-tracking part
    last = None
    for clients in (None, ["tv", "web_safari"], ["android_vr"], ["web_embedded", "mweb"]):
        try:
            return _ydl(url, vid, headers, clients)
        except Exception as e:
            last = e
            drop(vid)
            if not YT_BLOCKED.search(str(e)):
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


@app.route("/api/status")
def status():
    return jsonify(need=bool(PASSWORD), ok=bool(session.get("ok")) or not PASSWORD, contact=CONTACT,
                   blocked=BLOCKED_NAMES, max_mb=MAX_MB)


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


def reply(vid, info, alts):
    found = [f for f in glob.glob(os.path.join(CACHE, vid + ".*")) if not f.endswith((".part", ".ytdl"))]
    if not found:
        drop(vid)
        return jsonify(error="Video file not found (it may be too large - limit %d MB)." % MAX_MB), 400
    path = max(found, key=os.path.getsize)
    title = (info.get("title") or "video")[:100]
    mine = info.get("duration") or probe_duration(path)
    others = []
    for i, (u, r, d) in enumerate(alts[:6]):  # other videos found on the same page (not the one we are returning)
        if mine and d and abs(d - mine) < 2:
            continue
        others.append({"n": i, "dur": d})
    with lock:
        files[vid] = {"path": path, "title": title, "ts": time.time(), "alts": alts[:6]}
    return jsonify(id=vid, url="/media/" + vid, title=title, thumbnail=info.get("thumbnail"),
                   duration=mine, size=os.path.getsize(path), others=others)


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
    with lock:
        JOBS_ACTIVE[0] += 1
    try:
        vid = uuid.uuid4().hex[:12]
        try:
            info = grab_candidate(u, r, vid)
        except Exception as e:
            drop(vid)
            record_site(u, False)
            return jsonify(error="Could not get that video: " + re.sub(r"\s+", " ", str(e).replace("ERROR: ", ""))[:160]), 400
        record_site(u, True)
        return reply(vid, info, item["alts"])
    finally:
        with lock:
            JOBS_ACTIVE[0] -= 1
        slots.release()


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
    ref_in = str(body.get("referer") or "").strip()
    if ref_in and (len(ref_in) > 2000 or not url_is_safe(ref_in)):
        ref_in = ""

    cleanup()
    if cache_size_mb() > MAX_CACHE_MB:
        return jsonify(error="Server storage is full right now. Try again in a few minutes."), 503
    if not slots.acquire(blocking=False):
        return jsonify(error="Server is busy with other downloads. Try again in a minute."), 503
    with lock:
        JOBS_ACTIVE[0] += 1
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
                info = run_ydl(url, vid, {"Referer": ref_in} if ref_in else None)
            except Exception as e:
                err = e
        if info is None and is_x(url) and os.path.exists(COOKIES):  # cookies were tried first, now the services
            try:
                info = x_download(url, vid)
            except Exception as e:
                err = err or e
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
                            info = run_ydl(url, vid, {"Referer": ref_in} if ref_in else None)  # keep the first result
            except Exception:
                pass
        if info is None:
            drop(vid)
            record_site(url, False)
            msg = str(err or "no video found on this page").replace("ERROR: ", "")
            msg = re.sub(r"\s+", " ", msg)[:200]
            if "does not pass filter" in msg or "duration" in msg:
                msg = "This video is too long (limit %d minutes) or is a live stream." % MAX_MINUTES
            elif re.search(r"youtu(\.be|be\.com)", url.lower()) and re.search(r"429|too many|sign in|not a bot|confirm you|cookies|login|age|private|members|blocked|forbidden|403", msg, re.I):
                msg = "YouTube refused this request (" + msg[:120] + "). The app already tried several YouTube client identities. Try again in a minute, turn on a VPN, or add your YouTube cookies (cookies.txt). Private, members-only and age-restricted videos always need cookies."
            elif re.search(r"facebook\.com|fb\.watch", url.lower()) and re.search(r"login|cookie|private|not available|permission|log in", msg, re.I):
                msg = "Facebook says this video is not public (or wants a login). Public videos work. For friends-only or restricted ones add your cookies (COOKIES_B64 setting)."
            elif re.search(r"(twitter|x)\.com", url.lower()) and re.search(r"login|cookie|guest|sensitive|nsfw|age|protected|not authorized|no video|authentication", msg, re.I):
                msg = "Twitter/X wants a login for this post (sensitive or age-restricted content, or the guest limit). Add your cookies (COOKIES_B64 setting or cookies.txt) and try again. Posts without a video cannot be downloaded."
            elif "instagram" in url.lower() and re.search(r"login|cookie|rate-limit|not available|empty media|private", msg, re.I):
                msg = "Instagram wants a login. Add your cookies (COOKIES_B64 setting or cookies.txt) and try again. Private accounts need the cookies of an account that follows them."
            elif "403" in msg or "blocked" in msg:
                msg += " | Try again in a few minutes, or paste the link of the page that shows the video."
            return jsonify(error="Could not get the video: " + msg), 400
        if BLOCKED and str(info.get("extractor_key", "")).lower().startswith(BLOCKED_EXTRACTORS):
            drop(vid)
            record_site(url, False)
            return jsonify(error=BLOCK_MSG), 403
        record_site(url, True)
        return reply(vid, info, alts)
    finally:
        with lock:
            JOBS_ACTIVE[0] -= 1
        slots.release()


@app.route("/media/<vid>")
@need_auth
def media(vid):
    item = files.get(vid)
    if not item or not os.path.exists(item["path"]):
        return "File expired. Paste the link again.", 404
    item["ts"] = time.time()  # still in use: keep it
    ext = os.path.splitext(item["path"])[1]
    safe = "".join(c for c in item["title"] if c not in '\\/:*?"<>|').strip() or "video"
    return send_file(item["path"], mimetype=mimetypes.guess_type(item["path"])[0] or "video/mp4",
                     conditional=True, as_attachment=request.args.get("dl") == "1",
                     download_name=safe + ext)


# ---------- admin panel: anonymous usage stats only (no links, no titles, no names, no IPs stored) ----------
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    if not ADMIN_PASSWORD:
        return jsonify(error="The admin panel is off. Set the ADMIN_PASSWORD setting on the server to turn it on."), 404
    if not rate_ok("admin-login"):
        return jsonify(error="Too many tries. Wait a few minutes."), 429
    given = str((request.get_json(silent=True) or {}).get("password", ""))
    if hmac.compare_digest(given.encode(), ADMIN_PASSWORD.encode()):
        session.permanent = True
        session["admin_ok"] = True
        return jsonify(ok=True)
    return jsonify(error="Wrong password"), 403


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_ok", None)
    return jsonify(ok=True)


def _within_days(day_str, days):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(day_str)).days < days
    except Exception:
        return False


@app.route("/api/admin/stats")
@admin_auth
def admin_stats():
    now = time.time()
    with lock:
        active_now = sum(1 for v in DEVICES.values() if now - v < 300)        # seen in the last 5 minutes
        active_today = sum(1 for v in DEVICES.values() if now - v < 86400)
        devices_total = len(DEVICES)
        today = DAILY.get(datetime.date.today().isoformat(), 0)
        week = sum(v for k, v in DAILY.items() if _within_days(k, 7))
        daily = sorted(DAILY.items())[-14:]
        sites = sorted(SITE_STATS.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:40]
        jobs_running = JOBS_ACTIVE[0]
    return jsonify(
        active_now=active_now, active_today=active_today, devices_total=devices_total,
        downloads_today=today, downloads_week=week, daily=[{"day": d, "n": n} for d, n in daily],
        sites=[{"domain": d, "ok": o, "fail": f} for d, (o, f) in sites],
        jobs_running=jobs_running, jobs_max=MAX_JOBS, cache_mb=round(cache_size_mb(), 1), cache_max_mb=MAX_CACHE_MB,
    )


ADMIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin - ERROR HUB DOWNLODER</title>
<style>
:root{--bg:#0a0306;--card:#160911;--line:#3a1420;--txt:#f4e9e3;--mut:#c9a8a0;--a:#ff3860;--b:#ff9a3c;--ok:#36d17c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,sans-serif;padding:20px}
h1{font-size:22px;margin:0 0 18px}
.wrap{max-width:900px;margin:0 auto}
#login{max-width:320px;margin:60px auto;display:flex;flex-direction:column;gap:10px}
input{padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#000a;color:#fff;font:inherit}
button{padding:10px 14px;border-radius:10px;border:0;background:linear-gradient(90deg,var(--a),var(--b));color:#fff;font:600 15px inherit;cursor:pointer}
#err{color:var(--a);font-size:13px;min-height:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.card b{display:block;font-size:26px}
.card span{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:14px}
th{color:var(--mut);font-weight:600}
.ok{color:var(--ok)}.fail{color:var(--a)}
.muted{color:var(--mut);font-size:13px;margin:8px 0 18px}
#out{display:none}
.top{display:flex;justify-content:space-between;align-items:center}
.top button{background:transparent;border:1px solid var(--line);padding:7px 12px}
</style></head><body><div class="wrap">
<div id="login"><h1>Admin Login</h1><input id="pw" type="password" placeholder="Admin password"><button id="go">Enter</button><div id="err"></div></div>
<div id="out">
<div class="top"><h1>Usage overview</h1><button id="logout">Log out</button></div>
<p class="muted">Anonymous counts only - no links, titles, names or IP addresses are stored. Numbers reset if the server restarts.</p>
<div class="cards">
<div class="card"><b id="s_active">-</b><span>Active now (5 min)</span></div>
<div class="card"><b id="s_today_u">-</b><span>Active today (devices)</span></div>
<div class="card"><b id="s_devices">-</b><span>Devices seen (all time)</span></div>
<div class="card"><b id="s_today">-</b><span>Downloads today</span></div>
<div class="card"><b id="s_week">-</b><span>Downloads this week</span></div>
<div class="card"><b id="s_jobs">-</b><span>Downloads running now</span></div>
</div>
<h2>Sites being used</h2>
<table id="sites"><thead><tr><th>Site</th><th>Success</th><th>Failed</th><th>Total</th></tr></thead><tbody></tbody></table>
</div>
</div>
<script>
async function j(u,o){const r=await fetch(u,o);let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.error||'Error');return d}
async function load(){
  try{
    const d=await j('/api/admin/stats');
    document.getElementById('login').style.display='none';
    document.getElementById('out').style.display='block';
    s_active.textContent=d.active_now; s_today_u.textContent=d.active_today; s_devices.textContent=d.devices_total;
    s_today.textContent=d.downloads_today; s_week.textContent=d.downloads_week; s_jobs.textContent=d.jobs_running+' / '+d.jobs_max;
    const tb=document.querySelector('#sites tbody'); tb.innerHTML='';
    d.sites.forEach(s=>{const tr=document.createElement('tr');
      tr.innerHTML='<td>'+s.domain+'</td><td class="ok">'+s.ok+'</td><td class="fail">'+s.fail+'</td><td>'+(s.ok+s.fail)+'</td>';
      tb.appendChild(tr)});
  }catch(e){document.getElementById('login').style.display='flex';document.getElementById('out').style.display='none'}
}
go.onclick=async()=>{err.textContent='';try{await j('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw.value})});pw.value='';load()}catch(e){err.textContent=e.message}}
pw.addEventListener('keydown',e=>{if(e.key==='Enter')go.click()})
logout.onclick=async()=>{await j('/api/admin/logout',{method:'POST'});load()}
load(); setInterval(load,10000);
</script></body></html>"""


@app.route("/admin")
def admin_page():
    if not ADMIN_PASSWORD:
        return "The admin panel is off. Set the ADMIN_PASSWORD setting on the server to turn it on.", 404
    return ADMIN_HTML


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
