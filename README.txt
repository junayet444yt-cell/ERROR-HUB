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
Open your site in Chrome (phone or PC). Use the Chrome menu > Install app / Add to Home screen. It then opens like a normal app with its own icon.

COLOURS
All colours drift slowly around the colour wheel by themselves. Gear button > "Auto colour change (RGB)" turns it off.

X (TWITTER) VIDEOS
For x.com / twitter.com links the app first asks FxTwitter, then VxTwitter (no login needed), then yt-dlp. These are third-party services: the tweet link is sent to them and they can change or stop working. If cookies.txt / COOKIES_B64 is present, yt-dlp is tried first and the services second. 18+ posts may still need your own cookies.

YOUTUBE / FACEBOOK
Both are allowed now. YouTube needs a JavaScript runtime (the "deno" package in requirements.txt). YouTube often refuses data-centre servers like Render ("confirm you are not a bot"): then add your cookies (COOKIES_B64) or a proxy (PROXY_URL), or run the app on your own PC/home internet. Facebook: public videos work, private ones need cookies. In Render > Environment, delete BLOCKED_SITES if it is still set there.

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

==========================================================
ADMIN PANEL (who uses the app, how many downloads) - optional
==========================================================
Folder admin-worker/ is a tiny free Cloudflare Worker + D1 database. It is NOT part of the exe/apk.
1. Cloudflare (free account) > D1 > create a database called errorhub.
2. Workers & Pages > Create Worker > paste admin-worker/worker.js > Deploy.
3. Worker > Settings > Bindings > add D1 database, variable name DB, pick errorhub.
4. Worker > Settings > Variables and Secrets > add secrets ADMIN_PASSWORD (your password) and INGEST_KEY (any long random text).
5. Open  https://YOUR-WORKER.workers.dev/admin  and log in.
6. GitHub repo > Settings > Secrets and variables > Actions > New repository secret:
     TELEMETRY_URL = https://YOUR-WORKER.workers.dev      TELEMETRY_KEY = the same text as INGEST_KEY
7. Run "Build apps" again. The new exe/apk report anonymous counts (random id, site name, no links, no IP).
On Render/web set the environment variables TELEMETRY_URL and TELEMETRY_KEY instead.
Opt-out for a user: environment variable NO_TELEMETRY=1, or an empty file named no-telemetry in the app data folder
(Windows: %LOCALAPPDATA%\ERROR-HUB-DOWNLODER).

==========================================================
WINDOWS: INSTALLER OR PORTABLE (fast start)
==========================================================
The "Build apps" workflow now makes two Windows files (the old single-file .exe unpacked ~400 MB on every start = slow):
  ERROR-HUB-DOWNLODER-Setup.exe       installs once (no admin needed), Start-menu + desktop shortcut, opens fast
  ERROR-HUB-DOWNLODER-portable.zip    unzip anywhere, run ERROR-HUB-DOWNLODER.exe inside the folder
If the Setup step fails in GitHub Actions the portable zip is still built (that step is optional).
The app opens a start screen at once, uses the same address every time (47821) so your saved settings/background stay.

Quality: pick Best / 1080p / 720p / 480p / 360p / 240p / 144p / MP3 before pressing Get Video. Never upscaled.
Own background: settings (gear) > Add background > any photo or video from your device (stored only on that device).

----------------------------------------------------------
v31 notes
----------------------------------------------------------
* Reddit videos (normal and 18+) are supported through yt-dlp: post links, v.redd.it and redd.it links.
  18+/NSFW, private or quarantined posts usually need a login: put your own cookies.txt next to the Windows exe.
* Android app: ffmpeg is now bundled (arm64 phones), so videos that keep sound separately (Reddit, Facebook HD, YouTube)
  can be merged. YouTube on the phone has no JavaScript engine (deno), so the app tries the YouTube client identities that
  need none (android_vr, web_safari, tv, mweb); some YouTube videos may still fail, the Windows exe handles all of them.
* Windows: the release now always contains an .exe: ERROR-HUB-DOWNLODER-Setup.exe (installer) or, if the installer could not
  be made, a single ERROR-HUB-DOWNLODER.exe; the portable zip is always there too.

----------------------------------------------------------
v33 notes
----------------------------------------------------------
* Android app: opens only after you choose a Google account that is on the phone. To allow only certain accounts, set the
  GitHub repository VARIABLE ALLOWED_EMAILS (Settings > Secrets and variables > Actions > Variables), e.g.
  me@gmail.com,brother@gmail.com  (empty = any Google account that is on the phone). Build the apk again after changing it.
* Android app: Settings (gear) > Login: log in to YouTube/Google, Instagram, Facebook, Reddit or X inside the app, press Done.
  The cookies stay in the app folder and are used by the downloader for those sites.
* Windows: put cookies.txt next to the exe, or a file browser.txt containing one word (firefox / edge / chrome / brave) to use
  the logins of that browser. Firefox is the most reliable (Chrome/Edge protect their cookies).
* Admin panel: see admin-worker/ (Cloudflare Worker + D1). Set the GitHub secrets TELEMETRY_URL and TELEMETRY_KEY, then build again.

v36: the Android app now also ships QuickJS (arm64), the small JavaScript engine yt-dlp needs for YouTube, so YouTube can work on the phone like on Windows.

v37: Reddit - a link to a subreddit/profile is refused with a clear message (paste the link of ONE post, it contains /comments/).
If yt-dlp is refused, the app looks at the old.reddit.com page (with the "I am over 18" cookie, no login) for the real video (v.redd.it, RedGIFs, Streamable ...).

v38: new mobile-first design (glass cards, scrolling quality chips, bottom-sheet settings, logo in the top bar).
Facebook share links (facebook.com/share/...) are resolved to the real page and, if needed, the public preview video is used.
Android: ffmpeg is downloaded from GitHub during the build; Settings shows whether ffmpeg / JavaScript work on the device.
