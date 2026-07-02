# ── Stage 1: build the React frontend ────────────────────────────────────────
FROM node:20-slim AS ui-builder

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --frozen-lockfile 2>/dev/null || npm install

COPY frontend/ ./
RUN npm run build          # outputs to /ui/dist


# ── Stage 2: download static FFmpeg binaries ──────────────────────────────────
# We use BtbN's stable *release* build for FFmpeg 8.1, NOT the nightly master.
#   • master-latest = unreleased nightly commits → sometimes unstable (SIGSEGV)
#   • n8.1-latest   = the FFmpeg 8.1.x stable branch → same version as Fedora
#     8.x, which correctly reads MP4 track name atoms (©nam / udta metadata)
#     needed for SDH subtitle detection.  7.x builds (John Van Sickle static)
#     lack this parsing and fall back to the atom parser for SDH detection.
FROM debian:bookworm-slim AS ffmpeg-downloader

RUN apt-get update \
 && apt-get install -y --no-install-recommends wget xz-utils ca-certificates jq \
 && rm -rf /var/lib/apt/lists/*

# BtbN only keeps the last 14 daily builds (plus one build per month for two
# years) — a hardcoded "autobuild-YYYY-MM-DD-HH-MM" release tag WILL 404 once
# it inevitably rolls off that window. Resolve the current download URL via
# BtbN's GitHub Releases API instead, filtering by asset name for the
# "-linux64-gpl-8.1.tar.xz" suffix — this stays correct indefinitely, even as
# the embedded FFmpeg point version (currently n8.1.2) changes over time.
#
# Falls back to the most recent known-good dated release if the API call
# fails for any reason — most notably GitHub's unauthenticated API rate
# limit (60 requests/hour per IP), which a Docker host could plausibly hit
# during repeated rebuilds. The fallback tag will itself eventually roll off
# BtbN's 14-day retention window too — if this build ever fails on BOTH
# paths, check https://github.com/BtbN/FFmpeg-Builds/releases/latest for
# the current linux64-gpl-8.1 asset and update FALLBACK_URL below.
RUN FFMPEG_URL=$(wget -qO- --header="Accept: application/vnd.github+json" \
        https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest 2>/dev/null \
      | jq -r '.assets[]? | select(.name | test("-linux64-gpl-8\\.1\\.tar\\.xz$")) | .browser_download_url' \
      | head -1) \
 && FALLBACK_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-06-30-13-34/ffmpeg-n8.1.2-linux64-gpl-8.1.tar.xz" \
 && if [ -z "$FFMPEG_URL" ]; then \
      echo "GitHub API lookup failed or returned no match — using fallback URL" >&2; \
      FFMPEG_URL="$FALLBACK_URL"; \
    fi \
 && echo "Downloading FFmpeg from: $FFMPEG_URL" \
 && wget -q -O /tmp/ffmpeg.tar.xz "$FFMPEG_URL" \
 && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
 && find /tmp -name ffmpeg  -type f ! -name '*.so' -exec cp {} /usr/local/bin/ffmpeg \; \
 && find /tmp -name ffprobe -type f ! -name '*.so' -exec cp {} /usr/local/bin/ffprobe \; \
 && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
 && rm -rf /tmp/ffmpeg*


# ── Stage 3: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

# Copy static ffmpeg/ffprobe from the downloader stage — no apt dependency
COPY --from=ffmpeg-downloader /usr/local/bin/ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg-downloader /usr/local/bin/ffprobe /usr/local/bin/ffprobe

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Copy built UI into the location FastAPI expects
COPY --from=ui-builder /ui/dist ./frontend/dist

# Persistent volumes
VOLUME ["/config", "/media"]

EXPOSE 8000

ENV REMUXARR_DATABASE_PATH=/config/remuxarr.db \
    REMUXARR_TEMP_DIR=/tmp/remuxarr

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]
