#!/bin/sh
# Keep the video engine fresh (sites change often). If the update fails, the installed version is used.
python -m pip install -q --no-cache-dir --user -U "yt-dlp[default,curl-cffi]" >/dev/null 2>&1 || echo "yt-dlp update skipped"
exec gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 900 --graceful-timeout 30 --keep-alive 5
