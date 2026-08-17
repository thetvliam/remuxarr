# Remuxarr — Unraid Deployment Guide

## 1. Add the container

1. Go to the **Docker** tab.
2. Click **Add Container**.
3. Fill in these fields directly — this is the exact, confirmed-working
   configuration:

   | Field | Value |
   |---|---|
   | Name | `Remuxarr` |
   | Repository | `thetvliam/remuxarr:latest` |
   | Network Type | `Bridge` |

4. Add five path mappings (**Add another Path, Port, Variable, or
   Device**):

   | Container Path | Host Path |
   |---|---|
   | `/config` | `/mnt/user/appdata/remuxarr/config` |
   | `/media/movies` | your movies share, e.g. `/mnt/user/Media/Movies` |
   | `/media/tv` | your TV share, e.g. `/mnt/user/Media/TV` |
   | `/tmp/remuxarr` | `/tmp/remuxarr-temp` (RAM-backed on Unraid — avoids writing FFmpeg's intermediate output to your array) |
   | `/recycle` | `/mnt/user/appdata/remuxarr/recycle` — optional, see below |

   The last one is the recycle bin, which makes **revert to original**
   possible: it stores the audio and subtitle tracks a job removed, so a
   file can be put back the way it was. Only the removed tracks are kept,
   never the video, so it is normally a small fraction of each file —
   defaults are a 7 day / 20GB window, both changeable in Settings.

   Leave it unmapped and the recycle bin stays off and tells you why.
   Do **not** point it anywhere under `/tmp`: that is RAM-backed on Unraid,
   so the whole retention window would disappear on reboot.

5. Add one port mapping:

   | Container Port | Host Port |
   |---|---|
   | `9191` | `9191` (or whichever host port you prefer) |

6. Click **Apply**. Unraid pulls the image and starts the container — the
   first pull can take a minute or two.

There's also a template at [`templates/remuxarr.xml`](templates/remuxarr.xml)
in this repo, loadable via the **Select a template** dropdown if you place
the file in `/boot/config/plugins/dockerMan/templates-user/` first. Worth
trying — it does correctly pre-fill Name and Repository — but in practice
the path/port fields above may still need entering by hand regardless, so
the table above is the version to actually rely on.

## 2. First-time configuration

This part is identical no matter how you deployed Remuxarr — it all happens
in the web UI, not in Unraid itself.

1. Open `http://<your-unraid-ip>:9191` (or whichever port you set above).
2. Go to **Settings → Library & Processing → Library** and set your scan
   paths — this is empty by
   default, deliberately, so nothing happens until you point it somewhere.
   Use the *container-side* paths: `/media/movies` and `/media/tv` (these
   are what the app itself sees, regardless of what your actual Unraid
   share paths are called).
3. If you keep audio/subtitles in a language other than English, set that
   in **Settings → Library & Processing → Audio** / **→ Subtitles** —
   defaults to English.
4. Trigger a scan. **Dry run is on by default** — this first scan shows you
   exactly what would happen to every file, without touching anything.
5. Review the **Dry Run** tab. Once the planned actions look right, turn
   dry run off in **Settings → Worker** — from here on, real processing
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
Name:       Remuxarr
Image:      thetvliam/remuxarr:latest
Network:    Bridge
Port:       9191  →  9191/tcp   (host port is your choice)

Paths:
  /mnt/user/appdata/remuxarr/config  →  /config          (rw)
  <your movies share>                →  /media/movies    (rw)
  <your TV share>                    →  /media/tv        (rw)
  /tmp/remuxarr-temp                 →  /tmp/remuxarr    (rw)
  /mnt/user/appdata/remuxarr/recycle →  /recycle         (rw, optional)
```
