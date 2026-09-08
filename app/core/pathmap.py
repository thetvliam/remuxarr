"""
Translating a path between Remuxarr's view of the filesystem and an *arr's.

Sonarr, Radarr and Remuxarr often run in separate containers with the same
physical directory mounted at different paths — Radarr sees /media where
Remuxarr sees /media/movies. The prefixes are configured per service as
"Path Prefix (Remote)" and "Path Prefix (Local)".

This runs in both directions, which is why the parameters are named for the
direction rather than for the service:

  inbound   a webhook gives a path in the *arr's terms, and the file has to
            be found on disk        — from_prefix=remote, to_prefix=local
  outbound  a file Remuxarr has just written has to be matched against the
            record the *arr holds   — from_prefix=local, to_prefix=remote

Both prefixes must be non-empty for translation to apply. If either is
blank the path is returned unchanged, so setups where the containers
already agree work without configuring anything.
"""


def translate_path(path: str, from_prefix: str, to_prefix: str) -> str:
    """
    Swap one leading path prefix for another.

    A trailing slash on either prefix is ignored, and the match is on a
    whole path component: /media matches /media/Show but not /mediaserver,
    which would otherwise translate a sibling directory that merely starts
    with the same characters.
    """
    src = from_prefix.rstrip("/")
    dst = to_prefix.rstrip("/")
    if not src or not dst:
        return path
    if path.startswith(src + "/") or path == src:
        return dst + path[len(src):]
    return path
