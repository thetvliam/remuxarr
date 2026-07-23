# Remuxarr

A Dockerized media-library tool for Unraid (and any Docker host) that cleans up your Sonarr/Radarr library for better playback compatibility - **without ever re-encoding video.**

If you've used [Unmanic](https://github.com/Unmanic/unmanic) or similar tools and found yourself wanting *less* transcoding, not more, this is built specifically for that: it remuxes containers, drops audio/subtitle tracks you don't want, and fixes broken metadata - all through lossless stream copying, never touching the actual video data.

---

## Screenshots

![screenshots of dashboard](https://github.com/thetvliam/remuxarr/blob/afaa289c96df0abe8ec40bcd64a867732d2dcc7a/images_and_demo/screenshot.png)

<video src="https://github.com/user-attachments/assets/05affe2d-0b6d-4e00-9d7b-1ffb1b633bbe" aria-label="Demo video" title="Demo video">Demo video</video>

---

## Why this exists

Most media coming out of Sonarr/Radarr carries far more than you actually need - five or six audio languages, subtitle tracks for every region, sometimes a container your TV or Plex client doesn't handle as cleanly as it could. Re-encoding to "fix" this wastes CPU, time, and quality for no benefit, since the video itself was already fine.

Remuxarr only ever touches what's cheap and lossless to touch:

- **Container remuxing** - MKV → MP4 when every track inside is already MP4-compatible, so no video re-encode is ever needed for this.
- **Track filtering** - drops audio/subtitle tracks outside your kept languages, using stream copy, never transcoding anything to do it.
- **Metadata correction** - fixes missing (`und`) language tags, and can correct *wrong* ones too (e.g. a track mistagged in a completely different language than what's actually spoken).

Nothing is ever re-encoded - not video, not audio, under any setting, for any reason. That's not a preference here - it's the entire point of the project. (If you specifically need AAC 5.1 → AC3 for an older AV receiver's bitstream passthrough, that's a deliberate, reviewable, undoable operation in AC3 Forge - never something the main pipeline does automatically to every matching file.)

This application was generated entirely using Claude. I acted as the architect, prompt engineer, and primary tester to solve a specific problem for myself, and wanted to share the functional result with the open-source community!

## Features

- **Automatic library scanning** - full probe on first sight, fast delta (size/mtime) scans afterward.
- **Sonarr / Radarr webhook integration** - reacts to imports and upgrades directly, with automatic path translation between how each *arr sees files and how Remuxarr does.
- **Plex integration** - refreshes affected library sections after every job. A separate, opt-in backlog can additionally verify Plex's own metadata and force an explicit re-analyze on the rare files Plex's own maintenance misses - most installs won't need this turned on; see Settings → Integrations → Plex Analyze Backlog for why.
- **AC3 Forge** - a dedicated tool for finding AAC 5.1 files and converting just the audio to AC3, independent of the main processing pipeline, with the ability to undo.
- **Manual review, with bulk resolution** - files with genuinely ambiguous tracks (several undefined-language audio tracks, or image-based subtitles that can't be converted to SRT) are held for a human decision rather than guessed at. A settable policy (Settings → Library & Processing → Subtitles) can auto-resolve the subtitle case going forward, with a one-click bulk action for anything already sitting in review.
- **Audio Language Review** - search-and-bulk-correct tool for tracks that have a *wrong* language tag rather than a missing one (common with some release groups) - confirm it's actually correct, or apply the right tag to every matching file at once.
- **Dry run mode** - see every planned action across your whole library before anything real is touched. **On by default** for a fresh install - see First-time configuration below.
- **Email notifications** - on job failure, with a circuit breaker so a bad setting doesn't flood your inbox.
- **Scheduled scans**, **manual and orphaned-file cleanup**, **abort/pause controls**, and a live log viewer, all from the web UI.

## Installation

Prebuilt images are published to the GitHub Container Registry - you do not need to clone this repository to run Remuxarr.

| Branch | Image |
| --- | --- |
| Main (stable) | `ghcr.io/thetvliam/remuxarr:latest` |
| Testing (beta) | `ghcr.io/thetvliam/remuxarr:testing` |

### Unraid

See [`UNRAID_DEPLOYMENT.md`](UNRAID_DEPLOYMENT.md) for a step-by-step GUI walkthrough. Use `ghcr.io/thetvliam/remuxarr:latest` as the Repository value in the template.

### Docker Compose

Create a `docker-compose.yml` anywhere on your Docker host:

```yaml
services:
  remuxarr:
    image: ghcr.io/thetvliam/remuxarr:latest   # :testing for the beta branch
    container_name: remuxarr
    restart: unless-stopped

    ports:
      - "8000:8000"          # Web UI + API

    volumes:
      # Settings and database. Map this to a host path that survives
      # container updates - everything you configure lives here.
      - /path/to/appdata/remuxarr:/config

      # Your media library. Mount it at the same paths Sonarr/Radarr use
      # inside their own containers where you can - it makes the path
      # translation in Settings simpler (often unnecessary entirely).
      - /path/to/your/movies:/media/movies
      - /path/to/your/tv:/media/tv

    environment:
      - TZ=America/New_York   # see "Setting your time zone" below

    # Optional: stage FFmpeg's temp output in RAM instead of on the array.
    # Remuxarr checks free space first and falls back to the output file's
    # own directory when a file is too large for it, so this is safe to
    # leave enabled - and safe to delete if you'd rather not use RAM.
    tmpfs:
      - /tmp/remuxarr
```

Then:

1. Change the volume paths (`/path/to/...`) to match your host.
2. Start it:

   ```bash
   docker compose up -d
   ```

3. Open `http://<your-host-ip>:8000`.

### Setting your time zone

`TZ` controls the timestamps you see in the log viewer and in job history. It takes an **IANA time zone name**, which is an `Area/City` pair - not a country name, not an abbreviation like `EST`, and not an offset like `GMT+1`. Pick the listed city closest to you *within your own country*; large countries have several, because their regions follow different daylight-saving rules.

| Country | Example values |
| --- | --- |
| United States | `America/New_York`, `America/Chicago`, `America/Denver`, `America/Phoenix` (no DST), `America/Los_Angeles` |
| Canada | `America/Toronto`, `America/Winnipeg`, `America/Edmonton`, `America/Vancouver`, `America/Halifax` |
| United Kingdom | `Europe/London` |
| Ireland | `Europe/Dublin` |
| Germany | `Europe/Berlin` |
| France | `Europe/Paris` |
| Netherlands | `Europe/Amsterdam` |
| Spain | `Europe/Madrid` |
| Australia | `Australia/Sydney`, `Australia/Adelaide`, `Australia/Brisbane` (no DST), `Australia/Perth` |
| New Zealand | `Pacific/Auckland` |
| Japan | `Asia/Tokyo` |
| India | `Asia/Kolkata` |
| Singapore | `Asia/Singapore` |
| Brazil | `America/Sao_Paulo` |
| South Africa | `Africa/Johannesburg` |
| No local time | `Etc/UTC` |

A few things that trip people up:

- **The city is a label, not a location requirement.** `Europe/London` is correct for all of Scotland, Wales, and Northern Ireland too - the city just names the rule set that region follows.
- **Spelling is exact.** Names are case-sensitive and use underscores, so `America/New_York` works and `america/new_york` or `America/New York` do not.
- **Daylight saving is automatic.** That is the whole reason to use a zone name instead of a fixed offset - the clock shifts itself on the right dates.
- **Avoid `Etc/GMT±N` values.** Their signs are inverted from what you would expect (`Etc/GMT+5` is actually UTC−5), so they are an easy way to end up an hour or ten wrong.

To find your own, run `timedatectl list-timezones` on a Linux host (or check Unraid's **Settings → Date & Time**, which already displays it). After starting the container, `docker exec remuxarr date` confirms the setting took effect.

> **SELinux hosts (Fedora, RHEL, some Synology setups):** append `:Z` to the `/config` mount and `:z` to each media mount - the media ones are shared with your Sonarr/Radarr/Plex containers, so they must use the lowercase shared label. Both are harmless no-ops on non-SELinux hosts like stock Unraid. See this repo's own `docker-compose.yml` for a fully annotated example.

### Building from source

Only needed if you want to modify the code:

```bash
git clone https://github.com/thetvliam/remuxarr.git
cd remuxarr
cp .env.example .env      # required — the bundled compose file reads it
docker compose up -d --build
```

Every value in `.env.example` already matches the app's own built-in default, so an untouched copy is fine; edit it only to override something specific (the file's own comments explain each one). The first build takes a few minutes - it installs dependencies, builds the frontend, and fetches FFmpeg. Subsequent starts are fast.

The bundled `docker-compose.yml` builds the image locally rather than pulling it, and is also the fully annotated reference for the SELinux labels and `tmpfs` staging mentioned above.

## First-time configuration

Everything from here happens in the web UI, not in any config file:

1. Go to **Settings → Library & Processing → Library** and set your scan paths - this is empty on a fresh install, deliberately, so nothing happens until you point it at your actual library. Use the container-side paths (e.g. `/media/movies`, `/media/tv`), not your host paths.
2. If you keep audio/subtitles in a language other than English, set that in **Settings → Library & Processing → Audio / Subtitles** - both default to English.
3. Trigger a scan. **Dry run is on by default** - this first scan shows you exactly what would happen to every file, without touching anything.
4. Review the **Dry Run** tab. Once the planned actions look right, turn dry run off in **Settings → Worker** - real processing begins from here.
5. **Auto-start is on by default**, meaning the queue processes itself once dry run is off. If you'd rather review the queue manually before anything runs, turn this off in **Settings → Worker**.
6. Sonarr, Radarr, Plex, and email integrations are all off until you provide real connection details - nothing is assumed enabled.

## Development

The backend has a real test suite - 184 tests covering the decision engine (what happens to each file and why), library scanning, queue and job lifecycle, Sonarr/Radarr webhook path translation, FFmpeg command construction, AC3 Forge, and a sample-library regression suite that runs the real pipeline against a fixed set of probed media files:

```bash
pip install -r tests/requirements-test.txt
pytest tests/ -v
```

See [`tests/README.md`](tests/README.md) for more detail, including how to run this same suite inside a deployed container against production FFmpeg.

## License

MIT - see [`LICENSE`](LICENSE).
