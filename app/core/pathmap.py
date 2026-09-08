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


def swap_prefix(path: str, src: str, dst: str) -> str | None:
    """
    Replace one leading path prefix with another, or return None if the
    path does not sit under it.

    The rule this exists to hold in one place: the match is on a whole path
    component, so /media matches /media/Show and /media itself but not
    /mediaserver, which would otherwise translate a sibling directory that
    merely starts with the same characters. A trailing slash on either
    prefix is ignored.

    None rather than the unchanged path, because the callers want different
    things on a miss and only one of them can be the default. Plex skips
    the notification entirely; the *arr translation passes the path
    through. Returning the input would silently give both the second
    behaviour, and for Plex that means sending a local path the server
    cannot resolve — a notification that fails quietly.
    """
    src_norm = src.rstrip("/")
    dst_norm = dst.rstrip("/")

    if path == src_norm or path.startswith(src_norm + "/"):
        return dst_norm + path[len(src_norm):]
    return None


def translate_path(path: str, from_prefix: str, to_prefix: str) -> str:
    """
    Swap one leading path prefix for another, leaving the path alone if it
    does not match.

    Both prefixes must be non-empty for translation to apply. A single
    blank half is a half-configured setting, and treating it as a prefix
    of "" would match every absolute path and rewrite the lot.
    """
    if not from_prefix.rstrip("/") or not to_prefix.rstrip("/"):
        return path

    swapped = swap_prefix(path, from_prefix, to_prefix)
    return path if swapped is None else swapped
