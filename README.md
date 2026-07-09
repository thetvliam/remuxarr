# Remuxarr

A Dockerized media-library tool for Unraid (and any Docker host) that cleans up your Sonarr/Radarr library for better playback compatibility — **without ever re-encoding video.**

If you've used [Unmanic](https://github.com/Unmanic/unmanic) or similar tools and found yourself wanting *less* transcoding, not more, this is built specifically for that: it remuxes containers, drops audio/subtitle tracks you don't want, and fixes broken metadata — all through lossless stream copying, never touching the actual video data.

---

## Screenshots

![screenshots of dashboard](https://github.com/thetvliam/remuxarr/blob/e9bb7dabbfbf8fe6261dbbfc79dbe0568b900475/images_and_demo/screenshot.png)

![demo video](https://github.com/thetvliam/remuxarr/blob/e9bb7dabbfbf8fe6261dbbfc79dbe0568b900475/images_and_demo/demo_video.mp4)

---

## Why this exists

Most media coming out of Sonarr/Radarr carries far more than you actually need — five or six audio languages, subtitle tracks for every region, sometimes a container your TV or Plex client doesn't handle as cleanly as it could. Re-encoding to "fix" this wastes CPU, time, and quality for no benefit, since the video itself was already fine.

Remuxarr only ever touches what's cheap and lossless to touch:

- **Container remuxing** — MKV → MP4 when every track inside is already MP4-compatible, so no video re-encode is ever needed for this.
- **Track filtering** — drops audio/subtitle tracks outside your kept languages, using stream copy, never transcoding video to do it.
- **Metadata correction** — fixes missing (`und`) language tags, and can correct *wrong* ones too (e.g. a track mistagged in a completely different language than what's actually spoken).
- **The one exception** — AAC 5.1 → AC3 5.1, purely for older AV receivers that need bitstream passthrough. Off by default; see Settings → Audio.

Video is never re-encoded, under any setting, for any reason. That's not a preference here — it's the entire point of the project.

This application was generated entirely using Claude. I acted as the architect, prompt engineer, and primary tester to solve a specific problem for myself, and wanted to share the functional result with the open-source community!

## Features

- **Automatic library scanning** — full probe on first sight, fast delta (size/mtime) scans afterward.
- **Sonarr / Radarr webhook integration** — reacts to imports and upgrades directly, with automatic path translation between how each *arr sees files and how Remuxarr does.
- **Plex integration** — refreshes affected library sections after every job. A separate, opt-in backlog can additionally verify Plex's own metadata and force an explicit re-analyze on the rare files Plex's own maintenance misses — most installs won't need this turned on; see Settings → Plex Analyze Backlog for why.
- **AC3 Forge** — a dedicated tool for finding AAC 5.1 files and converting just the audio to AC3, independent of the main processing pipeline, with the ability to undo.
- **Manual review, with bulk resolution** — files with genuinely ambiguous tracks (several undefined-language audio tracks, or image-based subtitles that can't be converted to SRT) are held for a human decision rather than guessed at. A settable policy (Settings → Subtitles) can auto-resolve the subtitle case going forward, with a one-click bulk action for anything already sitting in review.
- **Audio Language Review** — search-and-bulk-correct tool for tracks that have a *wrong* language tag rather than a missing one (common with some release groups) — confirm it's actually correct, or apply the right tag to every matching file at once.
- **Dry run mode** — see every planned action across your whole library before anything real is touched. **On by default** for a fresh install — see Settings below.
- **Email notifications** — on job failure, with a circuit breaker so a bad setting doesn't flood your inbox.
- **Scheduled scans**, **manual and orphaned-file cleanup**, **abort/pause controls**, and a live log viewer, all from the web UI.

## Installation

Docker Compose is the primary supported path:

```bash
git clone https://github.com/<your-username>/remuxarr.git
cd remuxarr
cp .env.example .env   # adjust as needed
docker compose up -d
```

See [`docker-compose.yml`](docker-compose.yml) for the exact volume mounts expected — at minimum, a persistent `/config` volume and your actual media paths (e.g. `/media/movies`, `/media/tv`).

For Unraid specifically, see [`UNRAID_DEPLOYMENT.md`](UNRAID_DEPLOYMENT.md) for a step-by-step GUI walkthrough.

Once running, the web UI is available on port 8000 by default.

## Configuration

Everything is configured from Settings in the web UI — no config files to hand-edit. A few things worth knowing before your first real scan:

- **Dry run is on by default.** Your first scan will show you exactly what would happen to every file, without touching anything. Review the Dry Run tab, and once you're confident the decisions match what you actually want, turn dry run off in Settings → Library.
- **Set your scan paths** in Settings → Library before scanning — this is empty on a fresh install, deliberately, so nothing happens until you point it at your actual library.
- **Set your kept audio/subtitle languages** in Settings → Audio / Subtitles — defaults to English; change this if that's not what you want kept.
- **Auto-start is on by default**, meaning the queue processes itself once dry run is off. If you'd rather review the queue manually before anything runs, turn this off in Settings → Worker.
- Sonarr, Radarr, Plex, and email integrations are all off until you provide real connection details — nothing is assumed enabled.

## Development

The backend has a real, if still-growing, test suite covering the pure decision-logic engine — the part of the codebase responsible for deciding what happens to every file:

```bash
pip install -r tests/requirements-test.txt
pytest tests/ -v
```

See [`tests/README.md`](tests/README.md) for more detail, including how to run this same suite inside a deployed container against production ffmpeg.

## License

MIT — see [`LICENSE`](LICENSE).
