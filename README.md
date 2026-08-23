<!-- Single non-transparent banner, deliberately NOT a <picture> with
     prefers-color-scheme sources. That media query reads the viewer's
     OS/browser setting, not GitHub's own Appearance setting, so switching
     GitHub to light while the OS stays dark left the banner on the wrong
     variant. The GitHub-native mechanism that does follow the account theme
     (#gh-dark-mode-only / #gh-light-mode-only) is deprecated in GitHub's own
     docs and is GitHub-only — on Docker Hub, npm and VS Code it renders both
     images stacked, and this README is published to Docker Hub.

     banner_dark.png is fully opaque with square corners, so it carries its
     own background and reads as a deliberate card on any canvas, in any
     theme, in any renderer. Nothing to switch, nothing to get wrong. The
     transparent variants remain in images_and_demo/ for use on surfaces
     whose background is known. -->
![Remuxarr](https://raw.githubusercontent.com/thetvliam/remuxarr/main/images_and_demo/banner_dark.png)

# Remuxarr

[![Docker Pulls](https://img.shields.io/docker/pulls/thetvliam/remuxarr?logo=docker&label=Docker%20Hub%20pulls)](https://hub.docker.com/r/thetvliam/remuxarr)
[![GHCR](https://img.shields.io/badge/ghcr.io-thetvliam%2Fremuxarr-blue?logo=github)](https://github.com/thetvliam/remuxarr/pkgs/container/remuxarr)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Dockerized media-library tool for Unraid (and any Docker host) that cleans up your Sonarr/Radarr library for better playback compatibility - **without ever re-encoding video.**

If you've used [Unmanic](https://github.com/Unmanic/unmanic) or similar tools and found yourself wanting *less* transcoding, not more, this is built specifically for that: it remuxes containers, drops audio/subtitle tracks you don't want, and fixes broken metadata - all through lossless stream copying, never touching the actual video data.

---

## Screenshots

<table>
  <tr>
    <th width="33%">Screenshot</th>
    <th width="67%">Feature</th>
  </tr>
  <tr>
    <td width="33%">
      <a href="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_dashboard.png">
        <img src="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_dashboard.png" width="100%" alt="Dashboard showing a file mid-process with live progress, above the pending queue">
      </a>
    </td>
    <td width="67%">
      <strong>Dashboard</strong><br>
      Everything in flight and everything waiting, in one place. Files arrive
      from a library scan or straight from a Sonarr/Radarr webhook on import,
      and each job shows live progress while it runs. Dry run is on by default,
      so a fresh install shows you exactly what it <em>would</em> do to your
      library before it is allowed to do any of it.
    </td>
  </tr>
  <tr>
    <td width="33%">
      <a href="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_detail_panel.png">
        <img src="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_detail_panel.png" width="100%" alt="Detail panel listing every track in a file and the action planned for each">
      </a>
    </td>
    <td width="67%">
      <strong>Detail Panel</strong><br>
      Every track in the file - codec, language, channel layout, default and
      forced flags - next to exactly what is going to happen to it and why.
      Nothing is inferred silently: if a track is being dropped, retagged or
      left alone, the reason is on screen before the job runs.
    </td>
  </tr>
  <tr>
    <td width="33%">
      <a href="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_review.png">
        <img src="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_review.png" width="100%" alt="Review page asking whether to remove image-based subtitles from a file">
      </a>
    </td>
    <td width="67%">
      <strong>Review</strong><br>
      Genuinely ambiguous files are held for a person rather than guessed at -
      several undefined-language audio tracks, or image-based subtitles (PGS,
      VobSub) that cannot be converted to SRT. Decide once and apply it in bulk,
      or set a policy so the same case resolves itself from then on.
    </td>
  </tr>
  <tr>
    <td width="33%">
      <a href="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_forge.png">
        <img src="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_forge.png" width="100%" alt="AC3 Forge listing AAC 5.1 candidate files with convert and undo actions">
      </a>
    </td>
    <td width="67%">
      <strong>AC3 Forge</strong><br>
      The one deliberate exception to never re-encoding. Older AV receivers need
      AC3 to bitstream 5.1 over optical, so Forge finds AAC 5.1 files and
      converts <em>just</em> the audio, on files you pick. It is separate from
      the main pipeline, never automatic, and every conversion can be undone.
    </td>
  </tr>
  <tr>
    <td width="33%">
      <a href="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_settings.png">
        <img src="https://raw.githubusercontent.com/thetvliam/remuxarr/refs/heads/main/images_and_demo/Screenshot_settings.png" width="100%" alt="Settings page showing the language and subtitle rules for the library">
      </a>
    </td>
    <td width="67%">
      <strong>Settings</strong><br>
      Which languages to keep, what to do with subtitles, which containers to
      prefer, and how hard to work the machine. Integrations for Sonarr, Radarr
      and Plex live here, as do notifications, scheduled scans, and the recycle
      bin that lets a processed file be put back the way it was.
    </td>
  </tr>
</table>

<video src="https://github.com/user-attachments/assets/05affe2d-0b6d-4e00-9d7b-1ffb1b633bbe" aria-label="Demo video" title="Demo video">Demo video</video>

▶ [**Watch the demo video**](https://raw.githubusercontent.com/thetvliam/remuxarr/main/images_and_demo/demo_video.mp4) — plays inline on GitHub above; this link is for viewers that strip embedded video, such as Docker Hub.

---

## Why this exists

Most media coming out of Sonarr/Radarr carries far more than you actually need - five or six audio languages, subtitle tracks for every region, sometimes a container your TV or Plex client doesn't handle as cleanly as it could. Re-encoding to "fix" this wastes CPU, time, and quality for no benefit, since the video itself was already fine.

Remuxarr only ever touches what's cheap and lossless to touch:

- **Container remuxing** - MKV → MP4 when every track inside is already MP4-compatible, so no video re-encode is ever needed for this.
- **Track filtering** - drops audio/subtitle tracks outside your kept languages, using stream copy, never transcoding anything to do it.
- **Metadata correction** - fixes missing (`und`) language tags, and can correct *wrong* ones too (e.g. a track mistagged in a completely different language than what's actually spoken). Audio and subtitles are controlled separately, so you can tag undefined audio automatically while holding undefined subtitles for a decision, or the reverse (**Settings → Library & Processing → Metadata**).

Nothing is ever re-encoded - not video, not audio, under any setting, for any reason. That's not a preference here - it's the entire point of the project. (If you specifically need AAC 5.1 → AC3 for an older AV receiver's bitstream passthrough, that's a deliberate, reviewable, undoable operation in AC3 Forge - never something the main pipeline does automatically to every matching file.)

This application was generated entirely using Claude. I acted as the architect, prompt engineer, and primary tester to solve a specific problem for myself, and wanted to share the functional result with the open-source community!

## Features

- **Automatic library scanning** - full probe on first sight, fast delta (size/mtime) scans afterward.
- **Sonarr / Radarr webhook integration** - reacts to imports and upgrades directly, with automatic path translation between how each *arr sees files and how Remuxarr does.
- **Plex integration** - refreshes affected library sections after every job. A separate, opt-in backlog can additionally verify Plex's own metadata and force an explicit re-analyze on the rare files Plex's own maintenance misses - most installs won't need this turned on; see Settings → Integrations → Plex Analyze Backlog for why.
- **AC3 Forge** - AAC 5.1 → AC3 for older receivers, on files you pick, with an undo. Separate from the main pipeline and never automatic; see Screenshots above.
- **Manual review, with bulk resolution** - ambiguous files are held for a human decision rather than guessed at, resolvable in bulk or by a standing policy (Settings → Library & Processing → Subtitles); see Screenshots above.
- **Audio Language Review** - search-and-bulk-correct tool for tracks that have a *wrong* language tag rather than a missing one (common with some release groups) - confirm it's actually correct, or apply the right tag to every matching file at once.
- **Dry run mode** - see every planned action across your whole library before anything real is touched. **On by default** for a fresh install - see First-time configuration below.
- **Revert to original** (beta) - keeps the audio and subtitle tracks a job removed, so a processed file can be put back exactly as it was. Off by default and needs a volume mounted; see Reverting a processed file below.
- **Email notifications** - on job failure, with a circuit breaker so a bad setting doesn't flood your inbox.
- **Scheduled scans**, **manual and orphaned-file cleanup**, **abort/pause controls**, and a live log viewer, all from the web UI.

## Installation

Prebuilt images are published to **both** the GitHub Container Registry (GHCR) and Docker Hub - you do not need to clone this repository to run Remuxarr. The two are identical, built from the same commit in the same workflow; use whichever your setup prefers. Unraid's Community Apps and template ecosystem lean toward Docker Hub, so that's the one to use there unless you have a reason not to.

| Branch | Docker Hub | GHCR |
| --- | --- | --- |
| Main (stable) | `thetvliam/remuxarr:latest` | `ghcr.io/thetvliam/remuxarr:latest` |
| Testing (beta) | `thetvliam/remuxarr:testing` | `ghcr.io/thetvliam/remuxarr:testing` |

Every build also publishes an immutable `:<short-commit-sha>` tag (e.g. `:37a7265`) to both registries, so you can pin to an exact build and roll back if a `latest`/`testing` update ever misbehaves.

### Unraid

See [`UNRAID_DEPLOYMENT.md`](UNRAID_DEPLOYMENT.md) for a step-by-step GUI walkthrough. Use `thetvliam/remuxarr:latest` as the Repository value in the template.

### Docker Compose

Create a `docker-compose.yml` anywhere on your Docker host:

```yaml
services:
  remuxarr:
    image: thetvliam/remuxarr:latest   # :testing for the beta branch, or ghcr.io/thetvliam/remuxarr:latest
    container_name: remuxarr
    restart: unless-stopped

    ports:
      - "9191:9191"          # Web UI + API

    volumes:
      # Settings and database. Map this to a host path that survives
      # container updates - everything you configure lives here.
      - /path/to/appdata/remuxarr:/config

      # Your media library. Mount it at the same paths Sonarr/Radarr use
      # inside their own containers where you can - it makes the path
      # translation in Settings simpler (often unnecessary entirely).
      - /path/to/your/movies:/media/movies
      - /path/to/your/tv:/media/tv

      # Optional: recycle bin for "revert to original". Stores the audio
      # and subtitle tracks a job removed - never the video - so a file
      # can be put back the way it was. Leave it out and the feature
      # stays off. Must survive restarts, so not under /tmp.
      - /path/to/appdata/remuxarr/recycle:/recycle

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

3. Open `http://<your-host-ip>:9191`.

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

## Reverting a processed file (beta)

Remuxarr can keep whatever a job removed, so a file can be put back the way it
was. It is off by default and needs the `/recycle` volume mounted - see
Installation above.

**This feature is in beta.** It is newer than the rest of Remuxarr and has had
less time in front of real libraries. Nothing it does is destructive on its own -
it only ever adds a copy of what a job removed - but treat a revert point as a
convenience rather than a backup, and do not rely on it as your only route back.

It exists for the period while you are still working out what your language and
subtitle rules should be, which is exactly when a rule turns out to be wrong on
a few hundred files. It is not meant to be a permanent archive, and the
retention limits are set accordingly.

**What is kept.** Only the tracks a job actually removed - never the video. A
job that drops three foreign-language audio tracks and twenty subtitles stores
those and nothing else, so what it costs is a fraction of the file rather than a
second copy. A job that removes nothing stores nothing.

**What it costs.** Bounded twice, in **Settings → Recycle Bin**: 7 days and
20GB by default. Both apply - whichever is reached first. Set either to 0 to
disable that limit.

**Reverting.** Each entry in **Settings → Recycle Bin** restores the file to its
exact original state: every track back, in the original order, with the original
language tags, titles, default/forced flags and attachments, in the original
container. If the job converted MKV to MP4, reverting converts it back and
restores the original filename.

**When it will refuse.** A revert point records the file as the job left it. If
something else has written to that file since - Sonarr upgrading the episode is
the usual case - the stored tracks belong to a different release, and muxing
them in would produce a file that plays and is quietly wrong. Those entries stay
listed with the reason shown, so you can discard them, but they will not offer
to revert.

**If a file is renamed**, Remuxarr loses track of which file the entry belongs
to - a rename looks the same as a deletion from the outside. The entry moves to
**Unmatched** rather than being thrown away, and can be matched back: a renamed
file is byte-for-byte identical, so it is identified by its fingerprint rather
than guessed at.

**Extracted subtitle files** are removed again when you revert, since their
content goes back inside the file - but only the ones that job created. If you
already had a `.srt` there (from Bazarr, say) it is left alone, as is one you
have edited since.

**Two limitations worth knowing.** A job whose only casualty is an attachment -
a font, say - stores nothing, because Matroska cannot hold a file with no
tracks. And Matroska cover art comes back as a still-image video track rather
than an attachment, with its filename and mimetype intact; nothing is lost, but
a player may list it as a second video stream.

## Development

The backend has a real test suite - 1140 tests across 55 files, covering the decision engine (what happens to each file and why), library scanning and deletion cascades, queue and job lifecycle, job finalisation, Sonarr/Radarr webhook path translation and notification, FFmpeg command construction, AC3 Forge, the scheduler and Plex client, settings persistence, backup/restore, startup recovery, revert-to-original (including real-FFmpeg round trips that capture from a file and restore it, comparing stream by stream), and a sample-library regression suite that runs the real pipeline against a fixed set of probed media files:

```bash
pip install -r tests/requirements-test.txt
pytest
```

The frontend has its own suite - 175 tests covering the app's central state
hook (routing, toasts, history invalidation), every mutating user action,
paginated and history data fetching, the settings save path, and integer input
handling:

```bash
cd frontend && npm install && npm test
```

Both run in CI on every push, alongside pyflakes and eslint. A few backend
tests exercise a real FFmpeg/ffprobe and skip if the binaries are missing - CI
installs them so they always run.

See [`tests/README.md`](tests/README.md) for more detail, including how to run this same suite inside a deployed container against production FFmpeg.

## License

MIT - see [`LICENSE`](LICENSE).
