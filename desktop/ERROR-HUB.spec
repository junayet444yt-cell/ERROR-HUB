# -*- mode: python ; coding: utf-8 -*-
# Build from the repository root:  pyinstaller desktop/ERROR-HUB.spec
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.getcwd()
datas = [(os.path.join(ROOT, "static"), "static")]
binaries = []
hidden = []
for exe in ("ffmpeg.exe", "ffprobe.exe"):
    p = os.path.join(ROOT, "desktop", exe)
    if os.path.exists(p):
        binaries.append((p, "."))
for pkg in ("yt_dlp", "curl_cffi", "certifi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass

a = Analysis([os.path.join(ROOT, "desktop", "desktop.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hidden + ["app"], hookspath=[], runtime_hooks=[], excludes=["tkinter"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ERROR-HUB-DOWNLODER", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, runtime_tmpdir=None, console=False, icon=os.path.join(ROOT, "desktop", "icon.ico"))
