---
name: Bug report
about: Something isn't working as expected
title: ""
labels: bug
assignees: ""
---

**Describe the bug**
A clear description of what's wrong.

**To reproduce**
Steps to reproduce, including relevant settings (Settings → Library/Audio/Subtitles/etc.).

**Expected behavior**
What you expected to happen instead.

**Logs**
Relevant output from the in-app Log Viewer (**Settings → Maintenance & Logs**) or `docker logs remuxarr`.

**Environment**
- Remuxarr build: the **Build** line at the bottom of **Settings → Maintenance & Logs**, below the log viewer. The COPY button there gives the full value. `curl http://<host>:9191/api/health` reports the same thing.
- Image tag you pull (`latest`, `testing`, or a commit sha):
- Sonarr/Radarr/Plex versions (if relevant):

**Media file details (if relevant)**
Output of `ffprobe` on the affected file, or the file's track list from the UI.
