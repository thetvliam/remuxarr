# ── Stage 1: build the React frontend ────────────────────────────────────────
# Node 24 — Node 20 reached its official end-of-life on April 30, 2026 (no
# further security patches from upstream) and its bundled npm version is
# the source of the punycode/url.parse deprecation warnings seen in CI;
# confirmed via direct dependency-tree search that neither warning traces
# to anything in this project's own package.json — it's npm's own
# internals. Node 24 is the current recommended LTS target for new work.
FROM node:24-slim AS ui-builder

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

# FFmpeg 8.1 static binaries, downloaded WITHOUT depending on the GitHub
# API. The previous approach hit the API first (to resolve a dated
# autobuild asset) and 404'd on a hardcoded dated fallback — both failed
# together in a real build: the unauthenticated API returned 403 "rate
# limit exceeded" (60 req/hr/IP, and CI runners share pooled egress IPs)
# so the asset lookup came back empty, and BtbN had already pruned the
# pinned "autobuild-2026-06-30" release off its ~14-day retention window.
#
# Fix: BtbN maintains a PERMANENT "latest" release whose asset filenames
# are version-pinned but NOT date-stamped —
#   .../releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz
# The "latest" tag never rolls off and the file is re-uploaded in place as
# new 8.1.x point builds ship (verified: currently serves n8.1.2), so this
# URL needs no API call (no rate limit) and no dated tag (no retention
# 404). We try two forms of that same stable asset (direct /download/ and
# the /releases/latest/download/ redirect), each with wget retries for
# transient blips, and only fall back to the rate-limited API as a LAST
# resort. Finally we verify the binary actually runs and reports 8.1
# before the stage succeeds, so a truncated/corrupt archive fails HERE
# (loud, at build time) rather than at container runtime.
#
# If this ever needs bumping to a new major FFmpeg line, change the "8.1"
# in the two URLs (and the grep check) to the new version — the "latest"
# tag itself stays the same.
RUN set -eu; \
    STABLE_ASSET="ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"; \
    URLS="\
https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/${STABLE_ASSET} \
https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/${STABLE_ASSET}"; \
    ok=""; \
    for url in $URLS; do \
      echo "Trying FFmpeg download: $url"; \
      if wget --tries=3 --waitretry=5 --retry-connrefused \
              --timeout=30 -q -O /tmp/ffmpeg.tar.xz "$url"; then \
        ok=1; break; \
      fi; \
      echo "  → failed, trying next source" >&2; \
    done; \
    if [ -z "$ok" ]; then \
      echo "Stable URLs failed — falling back to GitHub API lookup" >&2; \
      api_url=$(wget -qO- --header="Accept: application/vnd.github+json" \
          https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest 2>/dev/null \
        | jq -r '.assets[]? | select(.name | test("-linux64-gpl-8\\.1\\.tar\\.xz$")) | .browser_download_url' \
        | head -1); \
      if [ -n "$api_url" ] && [ "$api_url" != "null" ]; then \
        echo "API resolved: $api_url"; \
        wget --tries=3 --waitretry=5 --retry-connrefused \
             --timeout=30 -q -O /tmp/ffmpeg.tar.xz "$api_url" && ok=1; \
      fi; \
    fi; \
    if [ -z "$ok" ]; then \
      echo "ERROR: could not download FFmpeg 8.1 from any source." >&2; \
      echo "Check https://github.com/BtbN/FFmpeg-Builds/releases/tag/latest for the current linux64-gpl-8.1 asset name." >&2; \
      exit 1; \
    fi; \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp; \
    find /tmp -name ffmpeg  -type f ! -name '*.so' -exec cp {} /usr/local/bin/ffmpeg \; ; \
    find /tmp -name ffprobe -type f ! -name '*.so' -exec cp {} /usr/local/bin/ffprobe \; ; \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe; \
    rm -rf /tmp/ffmpeg*; \
    /usr/local/bin/ffmpeg  -version | head -1 | grep -q 'version n8.1' \
      || { echo "ERROR: downloaded ffmpeg is not the expected 8.1 build" >&2; exit 1; }; \
    /usr/local/bin/ffprobe -version >/dev/null \
      || { echo "ERROR: ffprobe failed to run" >&2; exit 1; }; \
    echo "FFmpeg installed: $(/usr/local/bin/ffmpeg -version | head -1)"


# ── Stage 3: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

# Links the published GHCR package back to this repository — shows up on
# the repo's own Packages sidebar, uses this README as its description.
LABEL org.opencontainers.image.source="https://github.com/thetvliam/remuxarr"

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

EXPOSE 9191

ENV REMUXARR_DATABASE_PATH=/config/remuxarr.db \
    REMUXARR_TEMP_DIR=/tmp/remuxarr

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "9191", \
     "--workers", "1"]
