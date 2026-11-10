FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
USER app
ENV PORT=10000 PYTHONUNBUFFERED=1
# one worker on purpose: the app keeps its file list in memory
CMD ["sh", "start.sh"]
