#!/usr/bin/env python3
"""
Write a tiny, valid HDMV PGS (.sup) subtitle file with no dependencies.

ffmpeg can decode PGS but has no PGS encoder, so this is the one piece the
generator cannot get from ffmpeg itself. From this .sup ffmpeg can produce
the other two bitmap formats (dvd_subtitle / VobSub and dvb_subtitle) by
decoding the bitmaps and re-encoding them.

Usage: make_pgs.py OUTPUT.sup [WIDTH HEIGHT] [CUES]
  CUES is how many subtitle events to write (default 2), one per second.
"""
import struct
import sys


def _segment(seg_type: int, pts: int, data: bytes) -> bytes:
    return b"PG" + struct.pack(">IIBH", pts, 0, seg_type, len(data)) + data


def _rle_line(pixels: list[int]) -> bytes:
    """Encode one line with PGS run-length coding, then end-of-line."""
    out = bytearray()
    i = 0
    while i < len(pixels):
        c = pixels[i]
        run = 1
        while i + run < len(pixels) and pixels[i + run] == c and run < 16383:
            run += 1
        if c == 0:
            if run < 64:
                out += bytes([0x00, run])
            else:
                out += bytes([0x00, 0x40 | (run >> 8), run & 0xFF])
        else:
            if run < 3:
                out += bytes([c]) * run
            elif run < 64:
                out += bytes([0x00, 0x80 | run, c])
            else:
                out += bytes([0x00, 0xC0 | (run >> 8), run & 0xFF, c])
        i += run
    out += b"\x00\x00"
    return bytes(out)


def _bitmap(w: int, h: int) -> list[list[int]]:
    """A bordered box with three block 'letters' — visible and easy to spot."""
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            if y < 3 or y >= h - 3 or x < 3 or x >= w - 3:
                row.append(1)            # white border
            elif 8 <= y < h - 8 and any(a <= x < a + 20 for a in (20, 70, 120)):
                row.append(2)            # yellow blocks
            else:
                row.append(3)            # translucent black fill
        rows.append(row)
    return rows


def make_sup(path: str, width: int = 320, height: int = 180, cues: int = 2) -> None:
    bw, bh = min(180, width - 20), min(40, height - 20)
    x, y = (width - bw) // 2, height - bh - 10
    rle = b"".join(_rle_line(r) for r in _bitmap(bw, bh))
    palette = bytes([0, 0])                      # palette id, version
    palette += bytes([1, 235, 128, 128, 255])    # white
    palette += bytes([2, 210, 146, 16, 255])     # yellow
    palette += bytes([3, 16, 128, 128, 160])     # translucent black

    out = bytearray()
    for n in range(cues):
        show = 90000 * (n + 0) + 9000            # +0.1 s
        hide = show + 90000 * 0.7
        hide = int(hide)
        comp = n * 2
        # Display set: show
        pcs = struct.pack(">HHBHBBBB", width, height, 0x10, comp, 0x80, 0, 0, 1)
        pcs += struct.pack(">HBBHH", 0, 0, 0, x, y)
        wds = struct.pack(">BBHHHH", 1, 0, x, y, bw, bh)
        ods_body = struct.pack(">HH", bw, bh) + rle
        ods = struct.pack(">HBB", 0, 0, 0xC0) + (len(ods_body)).to_bytes(3, "big") + ods_body
        out += _segment(0x16, show, pcs)
        out += _segment(0x17, show, wds)
        out += _segment(0x14, show, palette)
        out += _segment(0x15, show, ods)
        out += _segment(0x80, show, b"")
        # Display set: clear
        pcs = struct.pack(">HHBHBBBB", width, height, 0x10, comp + 1, 0x00, 0, 0, 0)
        out += _segment(0x16, hide, pcs)
        out += _segment(0x17, hide, wds)
        out += _segment(0x80, hide, b"")
    with open(path, "wb") as fh:
        fh.write(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 320
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    c = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    make_sup(sys.argv[1], w, h, c)
