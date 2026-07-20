"""
Parse `ffmpeg -i` / ffprobe human-readable dumps into ffprobe-JSON-shaped
file records, so the real extract_tracks()/_normalise_container() can
consume them. Strict: raises on any stream line it can't confidently parse.
"""
import json
import re
import sys

# Whole-word disposition tokens FFmpeg prints in trailing parens. Anything
# else in parens (codec profiles/tags like "(LC)", "(dca)", "(High 10)",
# "(srt)") is NOT a disposition and is ignored.
_DISPOSITIONS = {
    "default", "forced", "dub", "original", "comment", "lyrics", "karaoke",
    "hearing impaired", "visual impaired", "clean effects", "attached pic",
    "timed thumbnails", "captions", "descriptions", "metadata", "dependent",
    "still image",
}
_CHANNELS = {
    "mono": 1, "stereo": 2, "2.1": 3, "3.0": 3, "quad": 4, "4.0": 4,
    "5.0": 5, "4.1": 5, "5.1": 6, "6.1": 7, "7.1": 8, "downmix": 2,
}

def _dispositions(line: str) -> set:
    found = set()
    for tok in re.findall(r"\(([^)]+)\)", line):
        t = tok.strip().lower()
        if t in _DISPOSITIONS:
            found.add(t)
    return found

def _channels(detail: str):
    # detail is the codec-parameter section; find a known channel-layout token
    for tok, n in sorted(_CHANNELS.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"(?<![\w.])" + re.escape(tok) + r"(?![\w])", detail):
            return n, tok
    return None, ""

def parse(text: str) -> list:
    files = []
    cur = None
    pending_stream = None  # to attach a following "title :" metadata line

    def _flush_title(title):
        if pending_stream is not None and title:
            pending_stream["tags"]["title"] = title

    for raw in text.splitlines():
        mh = re.match(r"Input #0, (.+?), from '(.+?)':", raw)
        if mh:
            cur = {"path": mh.group(2), "format_name": mh.group(1), "streams": []}
            files.append(cur)
            pending_stream = None
            continue
        if cur is None:
            continue

        # Index may carry a hex stream-id in brackets (MP4 dumps: "#0:0[0x1]").
        ms = re.match(
            r"\s+Stream #\d+:(\d+)(?:\[0x[0-9a-fA-F]+\])?"
            r"(?:\(([A-Za-z]{2,3})\))?: (\w+): (.*)", raw)
        if ms:
            idx = int(ms.group(1))
            lang = ms.group(2)
            typ = ms.group(3).lower()   # Video/Audio/Subtitle/Attachment/Data
            rest = ms.group(4)
            _cm = re.match(r"([A-Za-z0-9_]+)", rest); codec = _cm.group(1) if _cm else "unknown"
            disp = _dispositions(rest)
            stream = {
                "index": idx,
                "codec_type": {"video": "video", "audio": "audio",
                               "subtitle": "subtitle"}.get(typ, typ),
                "codec_name": codec,
                "tags": {},
                "disposition": {d.replace(" ", "_"): 1 for d in disp},
            }
            if lang:
                stream["tags"]["language"] = lang
            if typ == "audio":
                ch, layout = _channels(rest)
                if ch is not None:
                    stream["channels"] = ch
                    stream["channel_layout"] = layout
            cur["streams"].append(stream)
            pending_stream = stream
            continue

        mt = re.match(r"\s+title\s*:\s*(.+)", raw)
        if mt:
            _flush_title(mt.group(1).strip())
            continue

        # Lines we deliberately skip: Metadata:, Duration:, Chapters:, other
        # metadata key/values, program lines. Only Stream/title/header matter.
        if re.match(r"\s+Stream #", raw):
            raise ValueError(f"Unparsed Stream line: {raw!r}")

    return files

if __name__ == "__main__":
    files = parse(open(sys.argv[1]).read())
    json.dump(files, open(sys.argv[2], "w"), indent=1)
    print(f"parsed {len(files)} files, "
          f"{sum(len(f['streams']) for f in files)} streams -> {sys.argv[2]}")
