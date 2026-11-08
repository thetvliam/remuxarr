#!/usr/bin/env python3
"""
Plant corrupt AAC packets in an existing MKV/MP4 so a *stream copy* of its
audio fails inside the muxer, the way Remuxarr's corrupt-audio retry expects:

  [aost#0:1/copy @ ...] Error submitting a packet to the muxer:
                        Invalid data found when processing input

How: FFmpeg's MKV and MP4 muxers look at the FIRST packet of an AAC stream;
if it starts with an ADTS sync word they insert the aac_adtstoasc filter.
That filter then rejects any later packet that starts with a sync word but
has an invalid ADTS header. So: overwrite the start of the first audio
packet with a valid ADTS header, and the start of a later one with an
invalid header (sample-rate index 15). Packet sizes don't change, so the
container stays structurally valid and ffprobe still reads it normally;
only the bytes of those two audio frames are damaged, which a re-encode
(the retry path) decodes past with a warning.

Works on MP4/MOV/M4V input only: there ffprobe's packet "pos" is the exact
byte offset of the sample. (Matroska's is the start of the block element,
and its muxer refuses AAC without codec extradata anyway.) The output can
then be remuxed to MP4 or Matroska and the copy fails either way.

Usage: corrupt_aac.py INPUT.mp4 OUTPUT.mp4 [AUDIO_STREAM (default a:0)] [BAD_PACKET_NUMBER (default 10)]
"""
import json
import shutil
import subprocess
import sys


def packets(path: str, stream: int) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", str(stream),
         "-show_entries", "packet=pos,size", "-show_data_hash", "CRC32",
         "-of", "json", path],
        check=True, capture_output=True, text=True,
    ).stdout
    return [p for p in json.loads(out)["packets"] if p.get("pos") not in (None, "N/A")]


def adts_header(frame_len: int, sr_index: int) -> bytes:
    # 7-byte ADTS header, no CRC, AAC-LC, 1 channel config, 1 raw block
    b = bytearray(7)
    b[0] = 0xFF
    b[1] = 0xF1                                   # MPEG-4, layer 0, no CRC
    b[2] = (1 << 6) | (sr_index << 2) | 0         # profile LC(=1), sr index, chan hi bit
    b[3] = (2 << 6) | ((frame_len >> 11) & 0x03)  # chan config 2 (stereo) low bits
    b[4] = (frame_len >> 3) & 0xFF
    b[5] = ((frame_len & 0x07) << 5) | 0x1F
    b[6] = 0xFC
    return bytes(b)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    stream = sys.argv[3] if len(sys.argv) > 3 else "a:0"
    bad_n = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    head = open(src, "rb").read(12)
    if head[4:8] != b"ftyp":
        sys.exit("input must be an MP4/MOV (ISO-BMFF) file")
    pk = packets(src, stream)
    if len(pk) <= bad_n:
        sys.exit(f"only {len(pk)} audio packets; need more than {bad_n}")
    shutil.copyfile(src, dst)
    data = bytearray(open(dst, "rb").read())

    first, bad = pk[0], pk[bad_n]
    for p, sr in ((first, 4), (bad, 15)):          # 4 = 44.1 kHz (valid), 15 = invalid
        off = int(p["pos"])
        size = int(p["size"])
        if size < 9:
            sys.exit("packet too small to patch")
        data[off:off + 7] = adts_header(size, sr)
    open(dst, "wb").write(data)
    print(f"patched packet 0 @ {first['pos']} (valid ADTS) and packet {bad_n} @ {bad['pos']} (invalid ADTS)")


if __name__ == "__main__":
    main()
