# Remuxarr

A Dockerized media-library tool for Unraid (and any Docker host) that cleans up your Sonarr/Radarr library for better playback compatibility - **without ever re-encoding video.**

If you've used [Unmanic](https://github.com/Unmanic/unmanic) or similar tools and found yourself wanting *less* transcoding, not more, this is built specifically for that: it remuxes containers, drops audio/subtitle tracks you don't want, and fixes broken metadata - all through lossless stream copying, never touching the actual video data.

---

## Screenshots

![screenshots of dashboard](https://github.com/thetvliam/remuxarr/blob/e9bb7dabbfbf8fe6261dbbfc79dbe0568b900475/images_and_demo/screenshot.png)

<video src="https://github.com/user-attachments/assets/05affe2d-0b6d-4e00-9d7b-1ffb1b633bbe" aria-label="Demo video" title="Demo video">Demo video</video>

---

## Why this exists

Most media coming out of Sonarr/Radarr carries far more than you actually need - five or six audio languages, subtitle tracks for every region, sometimes a container your TV or Plex client doesn't handle as cleanly as it could. Re-encoding to "fix" this wastes CPU, time, and quality for no benefit, since the video itself was already fine.

Remuxarr only ever touches what's cheap and lossless to touch:

- **Container remuxing** - MKV → MP4 when every track inside is already MP4-compatible, so no video re-encode is ever needed for this.
- **Track filtering** - drops audio/subtitle tracks outside your kept languages, using stream copy, never transcoding video to do it.
- **Metadata correction** - fixes missing (`und`) language tags, and can correct *wrong* ones too (e.g. a track mistagged in a completely different language than what's actually spoken).
- **The one exception** - AAC 5.1 → AC3 5.1, purely for older AV receivers that need bitstream passthrough. Off by default; see Settings → Audio.

Video is never re-encoded, under any setting, for any reason. That's not a preference here - it's the entire point of the project.

This application was generated entirely using Claude. I acted as the architect, prompt engineer, and primary tester to solve a specific problem for myself, and wanted to share the functional result with the open-source community!

## Features

- **Automatic library scanning** - full probe on first sight, fast delta (size/mtime) scans afterward.
- **Sonarr / Radarr webhook integration** - reacts to imports and upgrades directly, with automatic path translation between how each *arr sees files and how Remuxarr does.
- **Plex integration** - refreshes affected library sections after every job. A separate, opt-in backlog can additionally verify Plex's own metadata and force an explicit re-analyze on the rare files Plex's own maintenance misses - most installs won't need this turned on; see Settings → Plex Analyze Backlog for why.
- **AC3 Forge** - a dedicated tool for finding AAC 5.1 files and converting just the audio to AC3, independent of the main processing pipeline, with the ability to undo.
- **Manual review, with bulk resolution** - files with genuinely ambiguous tracks (several undefined-language audio tracks, or image-based subtitles that can't be converted to SRT) are held for a human decision rather than guessed at. A settable policy (Settings → Subtitles) can auto-resolve the subtitle case going forward, with a one-click bulk action for anything already sitting in review.
- **Audio Language Review** - search-and-bulk-correct tool for tracks that have a *wrong* language tag rather than a missing one (common with some release groups) - confirm it's actually correct, or apply the right tag to every matching file at once.
- **Dry run mode** - see every planned action across your whole library before anything real is touched. **On by default** for a fresh install - see Settings below.
- **Email notifications** - on job failure, with a circuit breaker so a bad setting doesn't flood your inbox.
- **Scheduled scans**, **manual and orphaned-file cleanup**, **abort/pause controls**, and a live log viewer, all from the web UI.

## Installation

Docker Compose is the primary supported path. For Unraid specifically, see [`UNRAID_DEPLOYMENT.md`](UNRAID_DEPLOYMENT.md) for a step-by-step GUI walkthrough instead — the steps below are for a standalone Docker host.

1. Clone the repo and enter it:
   ```bash
   git clone https://github.com/thetvliam/remuxarr.git
   cd remuxarr
   ```
2. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   Every value in it already matches the app's own built-in default — you only need to edit this if you want to change something specific (see the comments in the file itself).
3. Edit [`docker-compose.yml`](docker-compose.yml)'s volume paths to point at your own media library — the defaults (`/path/to/your/movies`, `/path/to/your/tv`) are placeholders and won't exist on your system. At minimum you need a persistent `/config` volume and your actual movie/TV paths.
4. Start it:
   ```bash
   docker compose up -d
   ```
   The first run builds the image from scratch (installing dependencies, building the frontend, fetching FFmpeg) — this can take a few minutes. Subsequent starts are fast.
5. Open `http://<your-host-ip>:8000`.

## First-time configuration

Everything from here happens in the web UI, not in any config file:

1. Go to **Settings → Library** and set your scan paths — this is empty on a fresh install, deliberately, so nothing happens until you point it at your actual library. Use the *container-side* paths (e.g. `/media/movies`, `/media/tv`), not your host paths.
2. If you keep audio/subtitles in a language other than English, set that in **Settings → Audio** / **Settings → Subtitles** — defaults to English.
3. Trigger a scan. **Dry run is on by default** — this first scan shows you exactly what would happen to every file, without touching anything.
4. Review the **Dry Run** tab. Once the planned actions look right, turn dry run off in **Settings → Library** — real processing begins from here.
5. **Auto-start is on by default**, meaning the queue processes itself once dry run is off. If you'd rather review the queue manually before anything runs, turn this off in **Settings → Worker**.
6. Sonarr, Radarr, Plex, and email integrations are all off until you provide real connection details — nothing is assumed enabled.

## Development

The backend has a real, if still-growing, test suite covering the pure decision-logic engine - the part of the codebase responsible for deciding what happens to every file:

```bash
pip install -r tests/requirements-test.txt
pytest tests/ -v
```

See [`tests/README.md`](tests/README.md) for more detail, including how to run this same suite inside a deployed container against production ffmpeg.

## License

MIT - see [`LICENSE`](LICENSE).
