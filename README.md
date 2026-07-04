# Remuxarr

Automatic media remuxer for a Sonarr/Radarr/Plex library. Remuxarr watches for
newly imported or upgraded files (via webhook or a library scan), decides what
each file needs, and runs FFmpeg to clean it up — no re-encoding of video, so
it's fast and lossless wherever possible.

## What it does

- **Audio** — drops tracks outside your preferred languages, keeps a default
  track as a safety net when no preferred-language track exists, and can
  transcode AAC 5.1 to AC3 5.1 for AVR bitstream passthrough.
- **Subtitles** — drops tracks outside your preferred languages, always keeps
  forced subtitles, and extracts kept text-based subtitles (SubRip, mov_text,
  ASS/SSA) to external `.srt` sidecars using Plex's naming convention
  (`Movie.en.srt`, `Movie.en.forced.srt`, `.sdh.srt`, `.dub.srt`). Image-based
  subtitles (PGS, VOBSUB, DVD/DVB) that would need to stay embedded are
  flagged for manual review instead of being silently dropped or kept.
- **Container** — converts to MP4 when every kept track is MP4-compatible,
  and adds `+faststart` to existing MP4s that aren't web-optimized.
- **Undefined language tags** — tags `und` audio/subtitle tracks with a
  configured language, with a choice of how conservative to be.
- **AC3 Forge** — an on-demand, fully reversible way to append an AC3 5.1
  track alongside an existing AAC 5.1 track (rather than replacing it) for
  AVR passthrough on a per-file basis.
- **Plex integration** — refreshes or re-analyzes the affected library
  section after each job, rate-limited to a configurable overnight window so
  a large backfill doesn't burst hundreds of API calls at once.
- **Email notifications** — alerts on job failure, with a circuit breaker
  that stops sending after repeated consecutive failures so a bad config
  can't flood your inbox.
- **Dry-run mode**, per-file processing history, and a live queue with
  progress over WebSocket.
- **Web UI** (React) for reviewing flagged files, tuning settings, watching
  logs, and managing the queue/history.

Everything runs one file at a time (configurable concurrency) via FFmpeg
stream-copy where possible, only transcoding the specific tracks that need it.

## Quick start (Docker)

```bash
git clone https://github.com/thetvliam/remuxarr.git
cd remuxarr
docker compose up -d
```

See [`docker-compose.yml`](./docker-compose.yml) for a working example. It
mounts two volumes:

- `/config` — SQLite database (`remuxarr.db`) holding all settings, scan
  history, and the queue. Survives container restarts.
- `/media` — your media library. Mount whatever paths you want scanned; you
  configure the actual scan paths inside the app afterward.

Once it's running, open `http://<host>:8000` and configure Sonarr, Radarr,
Plex, and your library rules from the **Settings** page — most configuration
lives in the app itself (persisted to `/config/remuxarr.db`), not in
environment variables.

## Connecting Sonarr / Radarr

In each app: **Settings → Connect → Add → Webhook**

| Field | Value |
|---|---|
| URL | `http://<remuxarr-host>:8000/api/webhooks/sonarr` (or `/radarr`) |
| Triggers | On Import, On Upgrade |

Remuxarr also needs each app's URL and API key (entered in Remuxarr's own
Settings page, under Sonarr/Radarr) so it can trigger a rescan after
processing a file. If Sonarr/Radarr and Remuxarr see your media at different
paths (e.g. separate containers), set the path prefix mapping in the same
settings section — Remuxarr translates webhook paths automatically.

Files can also be picked up without webhooks via a manual or scheduled
library scan of the paths configured under Settings → Library.

## Configuration

Most settings (languages to keep, MP4 preference, Sonarr/Radarr/Plex
connections, email, scan paths, etc.) are configured through the web UI and
persisted in the database. A small number of infrastructure-level settings
are environment variables, all prefixed `REMUXARR_`:

| Variable | Default | Purpose |
|---|---|---|
| `REMUXARR_DATABASE_PATH` | `/config/remuxarr.db` | SQLite database location |
| `REMUXARR_TEMP_DIR` | `/tmp/remuxarr` | Scratch space for in-progress FFmpeg output |
| `REMUXARR_MAX_CONCURRENT_JOBS` | `1` | Files processed simultaneously (also editable in-app) |
| `REMUXARR_WEBHOOK_DEBOUNCE_SECONDS` | `10` | Delay before acting on a webhook, to collapse season-pack bursts |
| `REMUXARR_DEBUG` | `false` | Verbose logging |
| `REMUXARR_HOST` / `REMUXARR_PORT` | `0.0.0.0` / `8000` | Bind address |

## Development

Backend (FastAPI):

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend (React + Vite), with hot reload against a live backend:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`, so
the two run on separate ports with no CORS issues.

Production build, served by FastAPI from a single port:

```bash
cd frontend
npm run build         # outputs to frontend/dist/
```

FastAPI detects `frontend/dist/` at startup and serves it at `/`; API docs
stay available at `/docs`. The provided `Dockerfile` runs this build as part
of the image, so the container serves everything from port 8000.

## License

[MIT](./LICENSE)
