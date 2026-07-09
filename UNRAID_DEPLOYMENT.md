# Remuxarr — Unraid Deployment Guide

## Before you start

Remuxarr's Unraid template pulls its image from GitHub Container Registry.
If that image hasn't been published yet, the steps below will get all the
way through adding and configuring the container, then fail at the final
step when Unraid tries to actually pull it. If you hit that, the image
isn't published yet — this isn't something wrong with your setup.

## 1. Add the template repository

This is a one-time step — it tells Unraid where to find Remuxarr's template,
so it shows up as an option the normal way, instead of needing to be
configured by hand field by field.

1. Go to the **Docker** tab.
2. Click **Docker Repositories** (near the bottom of the page).
3. Under **Template repositories**, paste:
   ```
   https://github.com/thetvliam/remuxarr
   ```
4. Click **Save**.

## 2. Add the container

1. Back on the **Docker** tab, click **Add Container**.
2. Click the **Template** dropdown and select **Remuxarr**.
3. Every field is pre-filled with sensible defaults. Two are worth checking
   before you click Apply:
   - **Movies** / **TV Shows** — these default to `/mnt/user/media/movies`
     and `/mnt/user/media/tv`. Adjust to match your own share names if
     they're different.
   - **WebUI** port — defaults to `8000`. Change the host-side port if
     you're already running something else on it.
4. Click **Apply**. Unraid pulls the image and starts the container — the
   first pull can take a minute or two.

## 3. First-time configuration

This part is identical no matter how you deployed Remuxarr — it all happens
in the web UI, not in Unraid itself.

1. Open `http://<your-unraid-ip>:8000` (or whichever port you set above).
2. Go to **Settings → Library** and set your scan paths — this is empty by
   default, deliberately, so nothing happens until you point it somewhere.
   Use the *container-side* paths: `/media/movies` and `/media/tv` (these
   are what the app itself sees, regardless of what your actual Unraid
   share paths are called).
3. If you keep audio/subtitles in a language other than English, set that
   in **Settings → Audio** / **Settings → Subtitles** — defaults to
   English.
4. Trigger a scan. **Dry run is on by default** — this first scan shows you
   exactly what would happen to every file, without touching anything.
5. Review the **Dry Run** tab. Once the planned actions look right, turn
   dry run off in **Settings → Library** — from here on, real processing
   begins.
6. Everything else — Sonarr/Radarr webhooks, Plex integration, email
   notifications — is off until you provide real connection details in
   Settings. Nothing is assumed enabled.

## Updating

Standard Unraid update flow — no manual steps needed. When a new image is
published, Unraid's Docker tab shows an update available for the container
the same way it does for any other app; click it to pull and restart. Your
config and database live in the persistent `/config` volume, untouched by
this process.

## Quick reference

```
Image:    ghcr.io/thetvliam/remuxarr:latest
Port:     8000  →  8000/tcp

Paths:
  /mnt/user/appdata/remuxarr/config  →  /config          (rw)
  /mnt/user/media/movies             →  /media/movies    (rw)
  /mnt/user/media/tv                 →  /media/tv        (rw)
  /tmp/remuxarr-temp                 →  /tmp/remuxarr    (rw)

The only setting exposed directly in the template is webhook debounce
(REMUXARR_WEBHOOK_DEBOUNCE_SECONDS, default 10s) — everything else that's
actually worth adjusting lives in the web UI's own Settings, not as
environment variables.
```
