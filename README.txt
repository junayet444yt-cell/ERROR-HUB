ERROR HUB DOWNLODER - public-ready version
=======================================

WHAT IT DOES
Paste a video link -> the video appears and plays -> Download button saves it.
Works with most sites (TikTok, Twitter/X (x.com and twitter.com links), embed players, direct .mp4/.m3u8 links ...).
Instagram videos/reels work (add cookies, see below). Facebook, Instagram, TikTok and X work. YouTube and all other sites are allowed (BLOCKED_SITES defaults to none).
Gear button (bottom right) > "Add background video" plays a video from your own device behind the page (not uploaded anywhere).
Background: gear button (bottom right) = blur slider, darkness and floating embers.

DEPLOY ON RENDER
1. Upload ALL files of this folder to your GitHub repo (Dockerfile must be at the top level).
2. Render > New > Web Service > pick the repo > Runtime: Docker > Deploy.
3. PUBLIC site : do nothing more.
   PRIVATE site: Environment > add ACCESS_PASSWORD = your password.
4. Open the Render link. Test with one video link.

SETTINGS (Render > Environment, all optional)
ACCESS_PASSWORD  password (empty = public)
MAX_MB           biggest video in MB, default 2048 (2 GB)
MAX_MINUTES      longest video in minutes, default 300
RATE_MAX         requests per visitor per 10 minutes, default 8
KEEP_MINUTES     how long videos stay on the server, default 20
MAX_JOBS         downloads at the same time, default 2
MAX_CACHE_MB     total disk for saved videos, default 4500
CONTACT_EMAIL    shown in the page footer (good for a public site)
PROXY_URL        proxy for sites that block the server, e.g. socks5://user:pass@host:port
BLOCKED_SITES   comma list of blocked sites, "none" = block nothing (add instagram.com to block it)
COOKIES_B64      Instagram login cookies (base64 of cookies.txt) - or upload cookies.txt
KEEPALIVE_URL    only if not on Render; Render fills it automatically
USE_COOKIES      set to 1 to use cookies.txt (do NOT use your personal cookies on a public site)

IF A SITE SAYS "403"
Fill the "Embedded on" box with the page where that video is shown. If it still fails,
the site blocks the server's IP: use PROXY_URL, or run this app on your own PC.

RUN ON YOUR OWN PC (no Docker)
pip install -r requirements.txt   (also install ffmpeg)
python app.py    ->  open http://localhost:10000

PUBLIC SITE ADVICE
- Free Render sleeps when unused and has little RAM/disk; keep the limits low.
- You are responsible for what people download through your server. Keep the footer notice,
  set CONTACT_EMAIL, and remove complaints quickly.
- Update yt-dlp now and then (redeploy rebuilds it) because sites change often.

KEEP THE SERVER AWAKE 24 HOURS
The app pings itself every 4 minutes. For a stronger guarantee make a free monitor on uptimerobot.com
that opens https://YOUR-APP.onrender.com/health every 5 minutes. (Free Render has 750 hours a month = enough for one app.)

INSTALL AS AN APP
Open your site in Chrome (phone or PC). Press the "Install as App" button on the page (or Chrome menu > Install app / Add to Home screen). It then opens like a normal app with its own icon.

COLOURS
All colours drift slowly around the colour wheel by themselves. Gear button > "Auto colour change (RGB)" turns it off.

X (TWITTER) VIDEOS
For x.com / twitter.com links the app first asks FxTwitter, then VxTwitter (no login needed), then yt-dlp. These are third-party services: the tweet link is sent to them and they can change or stop working. If cookies.txt / COOKIES_B64 is present, yt-dlp is tried first and the services second. 18+ posts may still need your own cookies.

YOUTUBE / FACEBOOK
Both are allowed now. YouTube needs a JavaScript runtime (the "deno" package in requirements.txt). YouTube often refuses data-centre servers like Render ("confirm you are not a bot"): then add your cookies (COOKIES_B64) or a proxy (PROXY_URL), or run the app on your own PC/home internet. Facebook: public videos work, private ones need cookies. In Render > Environment, delete BLOCKED_SITES if it is still set there.

YOUTUBE IS OFF
YouTube links are blocked again (BLOCKED_SITES = youtube.com,youtu.be,youtube-nocookie.com). In Render > Environment make sure BLOCKED_SITES is not set to "none".

==========================================================
GITHUB-ONLY: WINDOWS .EXE AND ANDROID .APK (no Render needed)
==========================================================
1. Upload ALL files of this folder to your GitHub repository (including the hidden folder ".github").
   If ".github" is not accepted by the upload page: GitHub > Add file > Create new file, type the name
   .github/workflows/build-apps.yml and paste the content of that file.
2. GitHub > Actions tab > "Build apps (Windows EXE + Android APK)" > Run workflow (give a name like v1.0.0).
   (If GitHub asks, press "I understand my workflows, go ahead and enable them".)
3. Wait 10-20 minutes. Then open the repository's "Releases" page:
   - ERROR-HUB-DOWNLODER.exe  -> Windows
   - ERROR-HUB-DOWNLODER.apk  -> Android (allow "install unknown apps" once)
   (They are also under the finished run > Artifacts.)

Windows: double-click the exe. Windows may say "unknown publisher": More info > Run anyway.
It opens in its own Edge/Chrome app window; closing the window stops the program. Downloads use your own home internet.
Optional: put your own cookies.txt next to the exe for Instagram/Facebook/X posts that want a login.

Android: runs the downloader inside the phone (Python + yt-dlp). No ffmpeg on the phone, so only ready-made
single-file videos (most Instagram / TikTok / X / Facebook posts) are saved; videos that are stored as separate
video+audio streams may fail. Files are saved into the phone's Downloads folder.
