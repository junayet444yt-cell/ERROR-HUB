# -*- mode: python ; coding: utf-8 -*-
# Build from the repository root:  pyinstaller desktop/ERROR-HUB.spec
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.getcwd()
datas = [(os.path.join(ROOT, "static"), "static")]
binaries = []
hidden = []
for exe in ("ffmpeg.exe", "ffprobe.exe", "deno.exe"):
    p = os.path.join(ROOT, "desktop", exe)
    if os.path.exists(p):
        binaries.append((p, "."))
for pkg in ("yt_dlp", "yt_dlp_ejs", "curl_cffi", "certifi", "Cryptodome", "mutagen", "websockets", "brotli"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += [x for x in h if "SelfTest" not in x and ".tests" not in x]
    except Exception:
        pass

a = Analysis([os.path.join(ROOT, "desktop", "desktop.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hidden + ["app", "waitress"], hookspath=[], runtime_hooks=[], excludes=["tkinter"], noarchive=False)
pyz = PYZ(a.pure)
# one folder (not one file): starts fast because nothing has to be unpacked every time you open the app
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ERROR-HUB-DOWNLODER", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, icon=os.path.join(ROOT, "desktop", "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ERROR-HUB-DOWNLODER")
