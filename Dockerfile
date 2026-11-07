# ── Stage 1: build the React frontend ────────────────────────────────────────
# Node 24 — Node 20 reached its official end-of-life on April 30, 2026 (no
# further security patches from upstream) and its bundled npm version is
# the source of the punycode/url.parse deprecation warnings seen in CI;
# confirmed via direct dependency-tree search that neither warning traces
# to anything in this project's own package.json — it's npm's own
# internals. Node 24 is the current recommended LTS target for new work.
FROM node:24-slim AS ui-builder

WORKDIR /ui
# npm ci installs exactly what package-lock.json records, and stops if the
# lock does not match package.json. This used to be `npm install
# --frozen-lockfile 2>/dev/null || npm install`: --frozen-lockfile is Yarn's,
# npm has no such option, so every build resolved fresh from the ranges in
# package.json, and the fallback would have hidden a failure anyway. The
# lock went unused and fell far out of step with package.json. CI installs
# the same way, so the suite tests the tree that ships.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build          # outputs to /ui/dist


# ── Stage 2: download static FFmpeg binaries ──────────────────────────────────
# We use BtbN's build of the FFmpeg 8.1 stable branch, NOT the nightly master.
#   • master builds = unreleased nightly commits → sometimes unstable (SIGSEGV)
#   • n8.1 builds   = the FFmpeg 8.1.x stable branch → same version as Fedora
#     8.x, which correctly reads MP4 track name atoms (©nam / udta metadata)
#     needed for SDH subtitle detection.  7.x builds (John Van Sickle static)
#     lack this parsing and fall back to the atom parser for SDH detection.
FROM debian:bookworm-slim AS ffmpeg-downloader

RUN apt-get update \
 && apt-get install -y --no-install-recommends wget xz-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# FFmpeg is pinned to ONE exact build, checked by its SHA-256, so every
# image (and every CI run) gets the same bytes.
#
# Why this tag. BtbN keeps "the last build of each month for two years" and
# every other daily build for only 14 days. An earlier pin to a daily tag
# (autobuild-2026-06-30) was pruned inside that window and broke a real
# build. The floating "latest" release that replaced it never 404s, but it
# is rebuilt every day from the tip of the 8.1 branch, so no two builds
# were guaranteed to ship the same FFmpeg. A MONTH-END tag is the one kind
# that stays put: autobuild-2026-10-31-12-59 is the last build of October
# 2026, so BtbN keeps it until about 31 October 2028.
#
# Before then, move to a newer month-end build (tests/test_deployment_config.py
# starts failing 90 days ahead as a reminder):
#   1. Pick the LAST autobuild-YYYY-MM-DD-* tag of a finished month at
#      https://github.com/BtbN/FFmpeg-Builds/tags - never a mid-month one,
#      which is deleted after 14 days.
#   2. In that release's checksums.sha256, find the
#      ffmpeg-n8.1...-linux64-gpl-8.1.tar.xz line: the part between
#      "ffmpeg-" and "-linux64" is FFMPEG_VERSION, the hash is FFMPEG_SHA256.
#   3. Change all three values here AND in .github/workflows/ci.yml, which
#      installs the same build so the tests run against what ships.
#
# The download goes straight to the release asset, with no GitHub API call
# (the unauthenticated API is rate limited and CI runners share IPs). The
# checksum makes a truncated or substituted download fail HERE, at build
# time, and the version check confirms the binary runs.
RUN set -eu; \
    FFMPEG_TAG="autobuild-2026-10-31-12-59"; \
    FFMPEG_VERSION="n8.1.3-27-g8dca1586f4"; \
    FFMPEG_SHA256="41e9c565594c390622cf968d82fa4135d9e9a11d06c1515644288cbc844e3380"; \
    asset="ffmpeg-${FFMPEG_VERSION}-linux64-gpl-8.1.tar.xz"; \
    url="https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_TAG}/${asset}"; \
    echo "Downloading FFmpeg: $url"; \
    wget --tries=3 --waitretry=5 --retry-connrefused \
         --timeout=30 -q -O /tmp/ffmpeg.tar.xz "$url" \
      || { echo "ERROR: could not download $url. If BtbN has pruned ${FFMPEG_TAG}, move to a newer month-end build (see the comment above)." >&2; exit 1; }; \
    echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
      || { echo "ERROR: the FFmpeg download does not match its pinned SHA-256" >&2; exit 1; }; \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp; \
    find /tmp -name ffmpeg  -type f ! -name '*.so' -exec cp {} /usr/local/bin/ffmpeg \; ; \
    find /tmp -name ffprobe -type f ! -name '*.so' -exec cp {} /usr/local/bin/ffprobe \; ; \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe; \
    rm -rf /tmp/ffmpeg*; \
    /usr/local/bin/ffmpeg  -version | head -1 | grep -qF "version ${FFMPEG_VERSION}-" \
      || { echo "ERROR: ffmpeg does not report the pinned version ${FFMPEG_VERSION}" >&2; exit 1; }; \
    /usr/local/bin/ffprobe -version | head -1 | grep -qF "version ${FFMPEG_VERSION}-" \
      || { echo "ERROR: ffprobe does not report the pinned version ${FFMPEG_VERSION}" >&2; exit 1; }; \
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
# Upgrade pip first so the pip that ends up in the final image is current.
# The base python:3.12-slim ships an older pip that vulnerability scanners
# flag (it sits in site-packages even though it only runs at build time);
# upgrading here clears those findings. Left unpinned deliberately, so any
# build picks up the current pip without editing the Dockerfile. Builds are
# not periodic, though: publish.yml builds only when a file the image is made
# from changes, so a newer pip (or base image, or FFmpeg 8.1.x) with nothing
# else changed waits for the next such change, or for Run workflow in the
# Actions tab. The application's own dependencies stay pinned in
# requirements.txt for reproducibility.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# The pending release notes, served at /api/release-notes/ and shown to the
# user once per change. It lands at /app/RELEASE_NOTES.md, which is where
# the route resolves to from its own __file__ — four directories up from
# app/api/routes/. Without this line the endpoint finds nothing and returns
# "no notes", which is indistinguishable from a quiet release: the feature
# would fail exactly as silently as the problem it exists to solve.
COPY RELEASE_NOTES.md .

# Copy built UI into the location FastAPI expects
COPY --from=ui-builder /ui/dist ./frontend/dist

# Persistent volumes
#
# /recycle is deliberately NOT listed here, and must not be added. Docker
# creates an anonymous volume for any declared VOLUME the user did not map,
# which would make /recycle exist inside the container whether or not anyone
# chose where it lives. The app treats that directory's presence as proof the
# recycle volume was mounted, so an anonymous one would send revert sidecars
# to storage nobody sized, monitors, or knows to back up — and they would
# look fine right up until the volume was pruned. Leaving it undeclared is
# what lets a missing mount be reported instead of silently invented.
VOLUME ["/config", "/media"]

EXPOSE 9191

# Build identity. Passed by publish.yml; both have defaults so a plain
# `docker build .` still produces a working image that is honest about not
# knowing which build it is, rather than failing or claiming a version.
#
# ARG must be re-declared in this stage to be visible here — the ones at the
# top of a multi-stage file do not carry across FROM lines, and getting that
# wrong yields an empty string rather than an error.
#
# Declared here at the end rather than near the top on purpose. These values
# change on every single build, and an ARG invalidates the layer cache from
# its own line downwards, so hoisting them would rebuild the pip install and
# the FFmpeg copy for every commit. Below the COPY layers there is nothing
# left to invalidate but CMD.
ARG VERSION=dev
ARG COMMIT=unknown

ENV REMUXARR_DATABASE_PATH=/config/remuxarr.db \
    REMUXARR_TEMP_DIR=/tmp/remuxarr \
    REMUXARR_RECYCLE_DIR=/recycle \
    REMUXARR_VERSION=$VERSION \
    REMUXARR_COMMIT=$COMMIT

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "9191", \
     "--workers", "1"]
