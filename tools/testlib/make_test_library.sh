#!/usr/bin/env bash
# =============================================================================
#  make_test_library.sh: build a small media library of unusual containers,
#  codecs and tracks for explore.py to run through Remuxarr.
#
#  26 media files plus a folder layout of copies for the extras / exclude /
#  existing-sidecar / name-collision rules (42 files, about 22 MB, at the
#  default 6 s length).
#  Every file shows its own name on screen and every track says what it is
#  ("ENG SDH", "GER 550 Hz"), so you can watch or listen to an output and
#  tell which track went where.
#
#  Usage:
#    make_test_library.sh [options] OUTPUT_DIR
#
#  Options:
#    --font FILE        TTF/OTF to embed as the font attachment, style the ASS
#                       tracks with, and draw the on-screen labels in. If left
#                       out, the system is searched (fc-match, then common
#                       paths). The Remuxarr container has no fonts, so pass
#                       one there.
#    --pgs FILE         use a real PGS (.sup) subtitle instead of the one
#                       make_pgs.py generates.
#    --vobsub FILE.idx  use a real VobSub (.idx with its .sub beside it)
#                       instead of converting the PGS to dvd_subtitle.
#    --duration N       seconds per file (default 6, minimum 4).
#    --force            delete OUTPUT_DIR first if it exists.
#
#  Environment: FFMPEG, FFPROBE, PYTHON override the binaries used.
#
#  Needs: FFmpeg with libx264, libx265, libvpx-vp9, libopus, libmp3lame
#  (the BtbN 8.1 build the Dockerfile pins has all of them), and Python 3
#  (stdlib only) for the two helpers next to this script.
#
#  The comment on each file below says what it is built to exercise. What
#  Remuxarr does with it depends on the settings, which is why explore.py
#  checks invariants rather than expected outcomes.
# =============================================================================
set -euo pipefail

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
PYTHON="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FONT=""; PGS_IN=""; VOBSUB_IN=""; DUR=6; FORCE=0; OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --font)     FONT="$2"; shift 2 ;;
    --pgs)      PGS_IN="$2"; shift 2 ;;
    --vobsub)   VOBSUB_IN="$2"; shift 2 ;;
    --duration) DUR="$2"; shift 2 ;;
    --force)    FORCE=1; shift ;;
    -h|--help)  sed -n '2,/^# ====/p' "$0"; exit 0 ;;
    -*)         echo "Unknown option: $1" >&2; exit 2 ;;
    *)          OUT="$1"; shift ;;
  esac
done
[[ -n "$OUT" ]] || { echo "Usage: $0 [options] OUTPUT_DIR   (see --help)" >&2; exit 2; }
(( DUR >= 4 )) || { echo "--duration must be at least 4" >&2; exit 2; }

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
command -v "$FFMPEG"  >/dev/null || die "ffmpeg not found (set FFMPEG=...)"
command -v "$FFPROBE" >/dev/null || die "ffprobe not found (set FFPROBE=...)"
command -v "$PYTHON"  >/dev/null || die "python3 not found (set PYTHON=...)"
for helper in make_pgs.py corrupt_aac.py; do
  [[ -f "$SCRIPT_DIR/$helper" ]] || die "$helper must sit next to this script"
done

ENCODERS="$("$FFMPEG" -hide_banner -encoders 2>/dev/null)"
need_encoder() { grep -qE "^ [A-Z.]{6} $1 " <<<"$ENCODERS" || die "this FFmpeg has no '$1' encoder"; }
for e in libx264 libx265 libvpx-vp9 libopus libmp3lame aac ac3 eac3 dca flac pcm_s16le wmv2 wmav2 \
         mpeg2video mpeg4 mjpeg srt ass webvtt mov_text dvdsub dvbsub; do
  need_encoder "$e"
done
FILTERS="$("$FFMPEG" -hide_banner -filters 2>/dev/null)"
grep -q ' drawtext ' <<<"$FILTERS" || die "this FFmpeg has no drawtext filter"

if [[ -z "$FONT" ]]; then
  if command -v fc-match >/dev/null; then FONT="$(fc-match -f '%{file}' 'sans' 2>/dev/null || true)"; fi
  if [[ -z "$FONT" || ! -f "$FONT" ]]; then
    FONT="$(find /usr/share/fonts /usr/local/share/fonts ~/.fonts -type f \( -iname '*.ttf' -o -iname '*.otf' \) 2>/dev/null | head -n1 || true)"
  fi
fi
[[ -n "$FONT" && -f "$FONT" ]] || die "no font found — pass one with --font FILE.ttf"
FONT_EXT="${FONT##*.}"; FONT_EXT="${FONT_EXT,,}"
# The family name the ASS styles ask for. libass matches by family name, so
# a wrong guess only costs the styling, never a path.
FONT_FAMILY="$(fc-scan --format '%{family[0]}' "$FONT" 2>/dev/null || true)"
[[ -n "$FONT_FAMILY" ]] || FONT_FAMILY="$(basename "${FONT%.*}")"

if [[ -e "$OUT" ]]; then
  (( FORCE )) || die "$OUT already exists (use --force to replace it)"
  rm -rf -- "$OUT"
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
A="$OUT/.assets"       # build inputs; deleted at the end
mkdir -p "$A"

log "FFmpeg:  $("$FFMPEG" -version | head -n1)"
FF_MAJOR="$("$FFMPEG" -version | head -n1 | sed -nE 's/^ffmpeg version n?([0-9]+)\..*/\1/p')"
if [[ -n "$FF_MAJOR" && "$FF_MAJOR" -lt 8 ]]; then
  warn "FFmpeg $FF_MAJOR.x: the files will build, but Remuxarr ships 8.1 and reads some of them differently"
  warn "(before 8.1 ffprobe does not report an MP4 track's title, so file 17's 'Forced' title is invisible)."
fi
log "Font:    $FONT ($FONT_FAMILY)"
log "Output:  $OUT"

# ── Building blocks ──────────────────────────────────────────────────────────
FF=("$FFMPEG" -hide_banner -nostdin -loglevel error -y)

# A labelled test-card video input. $1 = label shown on screen.
video_in() {
  local label="$1" size="${2:-320x180}"
  printf -- '-f\nlavfi\n-i\ntestsrc2=s=%s:r=24:d=%s,drawtext=fontfile=%s:text=%s:fontsize=16:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=4:x=6:y=6\n' \
    "$size" "$DUR" "${FONT//:/\\:}" "'${label}'"
}
# A tone input. Each language gets its own pitch so tracks can be told apart
# by ear: eng 440, ger 550, spa 660, fre 330, ita 770, jpn 880, und 220,
# dan 990, commentary 300.
tone_in() { printf -- '-f\nlavfi\n-i\nsine=f=%s:d=%s,aformat=channel_layouts=stereo\n' "$1" "$DUR"; }

# Collect the lines of a helper's output into an array.
add() { local -n _arr=$1; shift; while IFS= read -r l; do _arr+=("$l"); done < <("$@"); }

srt() {   # srt FILE "TEXT"
  local t="$2"
  printf '1\n00:00:00,500 --> 00:00:01,800\n%s (1/3)\n\n2\n00:00:02,000 --> 00:00:03,300\n%s (2/3)\n\n3\n00:00:03,500 --> 00:00:04,000\n%s (3/3)\n\n' \
    "$t" "$t" "$t" > "$1"
}
vtt() {   # vtt FILE "TEXT"
  printf 'WEBVTT\n\n00:00:00.500 --> 00:00:01.800\n%s (1/3)\n\n00:00:02.000 --> 00:00:03.300\n%s (2/3)\n\n00:00:03.500 --> 00:00:04.000\n%s (3/3)\n\n' \
    "$2" "$2" "$2" > "$1"
}
ass() {   # ass FILE "TEXT" signs(0|1)
  local pos=""; [[ "$3" == 1 ]] && pos='{\pos(160,40)\c&H00FFFF&}'
  cat > "$1" <<EOF
[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,${FONT_FAMILY},16,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:01.80,Default,,0,0,0,,${pos}$2 (1/3)
Dialogue: 0,0:00:02.00,0:00:03.30,Default,,0,0,0,,${pos}$2 (2/3)
Dialogue: 0,0:00:03.50,0:00:04.00,Default,,0,0,0,,${pos}$2 (3/3)
EOF
}

# ── Shared assets ────────────────────────────────────────────────────────────
log "Preparing subtitle, chapter and image assets"
srt "$A/eng.srt"        "ENG"
srt "$A/eng2.srt"       "ENG second plain track"
srt "$A/eng_forced.srt" "ENG FORCED (disposition)"
srt "$A/eng_sdh.srt"    "ENG SDH"
srt "$A/eng_cc.srt"     "ENG CC (title)"
srt "$A/eng_dub.srt"    "ENG DUBTITLE (disposition)"
srt "$A/eng_fdub.srt"   "ENG FORCED+DUB"
srt "$A/fre_forced.srt" "FRE FORCED (title only)"
srt "$A/fre.srt"        "FRE"
srt "$A/spa.srt"        "SPA"
srt "$A/jpn_dub.srt"    "JPN Dubtitle (title)"
srt "$A/und1.srt"       "UND first"
srt "$A/und2.srt"       "UND second"
vtt "$A/eng.vtt"        "ENG WebVTT"
vtt "$A/spa.vtt"        "SPA WebVTT"
vtt "$A/ger.vtt"        "GER WebVTT"
ass "$A/eng_signs.ass"  "ENG Signs and Songs" 1
ass "$A/eng_full.ass"   "ENG Full Subtitles"  0
ass "$A/ger_full.ass"   "GER Volle Untertitel" 0
ass "$A/eng_plain.ass"  "ENG ASS without fonts" 0
# Windows-1252 bytes FFmpeg cannot decode as UTF-8: é è à ç ï
printf '1\r\n00:00:00,500 --> 00:00:01,800\r\nCaf\xe9 cr\xe8me (latin-1, 1/2)\r\n\r\n2\r\n00:00:02,000 --> 00:00:03,300\r\nNa\xefve d\xe9j\xe0 vu (latin-1, 2/2)\r\n\r\n' > "$A/latin1.srt"
printf 'Release notes: this is a non-font attachment.\n' > "$A/release-notes.txt"
printf ';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=%d\ntitle=Opening\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=%d\nEND=%d\ntitle=Main\n' \
  $((DUR*500)) $((DUR*500)) $((DUR*1000)) > "$A/chapters.txt"
"${FF[@]}" -f lavfi -i "color=c=0x8B0000:s=300x300:d=1,drawtext=fontfile=${FONT//:/\\:}:text='COVER':fontsize=48:fontcolor=white:x=(w-tw)/2:y=(h-th)/2" -frames:v 1 "$A/cover.jpg"

# Bitmap subtitles: PGS from make_pgs.py (or --pgs), VobSub and DVB decoded
# from it and re-encoded by FFmpeg (or --vobsub for a real one).
if [[ -n "$PGS_IN" ]]; then cp -- "$PGS_IN" "$A/pgs.sup"; else "$PYTHON" "$SCRIPT_DIR/make_pgs.py" "$A/pgs.sup" 320 180 3; fi
"${FF[@]}" -fix_sub_duration -i "$A/pgs.sup" -c:s dvbsub -f matroska "$A/dvb.mks"
if [[ -n "$VOBSUB_IN" ]]; then
  "${FF[@]}" -i "$VOBSUB_IN" -map 0:s:0 -c:s copy -f matroska "$A/dvd.mks"
else
  "${FF[@]}" -fix_sub_duration -i "$A/pgs.sup" -c:s dvdsub -f matroska "$A/dvd.mks"
fi

# Common encoder settings
H264=(-c:v libx264 -preset ultrafast -crf 32 -pix_fmt yuv420p -g 48)
MKV=(-default_mode passthrough)     # only the default flags set below; none inferred

# ── The files ────────────────────────────────────────────────────────────────
# Each block: inputs, maps, codecs, tags, dispositions, output. Where a
# comment names what Remuxarr does, that is at the app's default settings.

f01() {  # Already compliant MP4: the "nothing to do" baseline. Its audio is AAC 5.1, for trying AC3 Forge by hand.
  local a=(); add a video_in "01 clean faststart mp4"; add a tone_in 440
  "${FF[@]}" "${a[@]}" -map 0 -map 1 "${H264[@]}" -c:a aac -ac 6 \
    -metadata:s:a:0 language=eng -disposition:a:0 default -movflags +faststart \
    "$OUT/01 - clean faststart.mp4"
}
f02() {  # MP4 with the moov atom at the end -> add fast start.
  local a=(); add a video_in "02 mp4 without faststart"; add a tone_in 440
  "${FF[@]}" "${a[@]}" -map 0 -map 1 "${H264[@]}" -c:a aac -metadata:s:a:0 language=eng \
    "$OUT/02 - no faststart.mp4"
}
f03() {  # Typical WEB-DL MKV: combined pass (drop + extract + convert). Non-MP4 DTS is dropped, so MP4 is allowed.
  local a=(); add a video_in "03 webdl typical mkv"
  add a tone_in 440; add a tone_in 550; add a tone_in 300
  a+=(-i "$A/eng.srt" -i "$A/eng_sdh.srt" -i "$A/spa.srt" -i "$A/chapters.txt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 -map 5 -map 6 -map_chapters 7 \
    "${H264[@]}" -strict -2 -c:a:0 eac3 -ac:a:0 6 -c:a:1 dca -ac:a:1 6 -c:a:2 aac -ac:a:2 2 -c:s srt \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger -metadata:s:a:2 title="Director's Commentary" \
    -metadata:s:s:0 language=eng -metadata:s:s:1 language=eng -metadata:s:s:1 title="English SDH" \
    -metadata:s:s:2 language=spa \
    -disposition:a:0 default -disposition:a:1 0 -disposition:a:2 0 \
    -disposition:s:0 0 -disposition:s:1 hearing_impaired -disposition:s:2 0 "${MKV[@]}" \
    "$OUT/03 - webdl typical.mkv"
}
f04() {  # External-SRT naming: .forced .sdh .dub, numbering, flags given only by title, ASS without fonts.
  local a=(); add a video_in "04 subtitle naming mkv"; add a tone_in 440
  local subs=(eng eng2 eng_forced eng_sdh eng_cc eng_dub eng_fdub fre_forced spa jpn_dub)
  for s in "${subs[@]}"; do a+=(-i "$A/$s.srt"); done
  a+=(-i "$A/eng_plain.ass")
  local maps=(-map 0 -map 1); for i in $(seq 2 12); do maps+=(-map "$i"); done
  "${FF[@]}" "${a[@]}" "${maps[@]}" "${H264[@]}" -c:a aac -c:s srt -c:s:10 ass \
    -metadata:s:a:0 language=eng \
    -metadata:s:s:0 language=eng -metadata:s:s:1 language=eng \
    -metadata:s:s:2 language=eng \
    -metadata:s:s:3 language=eng -metadata:s:s:3 title="English (SDH)" \
    -metadata:s:s:4 language=eng -metadata:s:s:4 title="English [CC]" \
    -metadata:s:s:5 language=eng \
    -metadata:s:s:6 language=eng \
    -metadata:s:s:7 language=fre -metadata:s:s:7 title="Forced" \
    -metadata:s:s:8 language=spa \
    -metadata:s:s:9 language=jpn -metadata:s:s:9 title="Dubtitle" \
    -metadata:s:s:10 language=eng -metadata:s:s:10 title="Styled, no fonts" \
    -disposition:a:0 default \
    -disposition:s:0 0 -disposition:s:1 0 -disposition:s:2 forced -disposition:s:3 0 -disposition:s:4 0 \
    -disposition:s:5 dub -disposition:s:6 forced+dub -disposition:s:7 0 -disposition:s:8 0 \
    -disposition:s:9 0 -disposition:s:10 0 "${MKV[@]}" \
    "$OUT/04 - subtitle naming.mkv"
}
f05() {  # DVD rip: MPEG-2 + VobSub. Image-subtitle review with two kept tracks (one kept only because forced).
  local a=(); add a video_in "05 dvd vobsub mkv" 320x180; add a tone_in 440
  a+=(-i "$A/dvd.mks" -i "$A/dvd.mks" -i "$A/dvd.mks")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2:s:0 -map 3:s:0 -map 4:s:0 \
    -c:v mpeg2video -q:v 6 -c:a ac3 -c:s copy \
    -metadata:s:a:0 language=eng -metadata:s:s:0 language=eng -metadata:s:s:1 language=fre \
    -metadata:s:s:2 language=spa -disposition:a:0 default \
    -disposition:s:0 0 -disposition:s:1 forced -disposition:s:2 0 "${MKV[@]}" \
    "$OUT/05 - dvd vobsub.mkv"
}
f06() {  # Anime BD: HEVC 10-bit, PGS + styled ASS + fonts -> one review giving both reasons (image subtitle, fonts).
  local a=(); add a video_in "06 anime bd mkv"; add a tone_in 880; add a tone_in 440
  a+=(-i "$A/pgs.sup" -i "$A/eng_signs.ass" -i "$A/eng_full.ass" -i "$A/ger_full.ass")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 -map 5 -map 6 \
    -c:v libx265 -preset ultrafast -pix_fmt yuv420p10le -x265-params log-level=error \
    -c:a:0 flac -c:a:1 aac -c:s:0 copy -c:s:1 ass -c:s:2 ass -c:s:3 ass \
    -metadata:s:a:0 language=jpn -metadata:s:a:1 language=eng \
    -metadata:s:s:0 language=eng -metadata:s:s:0 title="PGS (Blu-ray)" \
    -metadata:s:s:1 language=eng -metadata:s:s:1 title="Signs & Songs" \
    -metadata:s:s:2 language=eng -metadata:s:s:2 title="Full Subtitles" \
    -metadata:s:s:3 language=ger -metadata:s:s:3 title="Deutsch" \
    -disposition:a:0 default -disposition:a:1 0 \
    -disposition:s:0 0 -disposition:s:1 forced -disposition:s:2 default -disposition:s:3 0 \
    -attach "$FONT" -metadata:s:t:0 mimetype="font/$FONT_EXT" -metadata:s:t:0 filename="Modern.$FONT_EXT" \
    -attach "$FONT" -metadata:s:t:1 mimetype=application/x-truetype-font -metadata:s:t:1 filename="Legacy.$FONT_EXT" \
    "${MKV[@]}" "$OUT/06 - anime bd pgs and fonts.mkv"
}
f07() {  # Fonts that serve nothing kept: filename-only font + non-font attachment, styled track not in keep list.
  local a=(); add a video_in "07 fonts unused mkv"; add a tone_in 440
  a+=(-i "$A/eng.srt" -i "$A/ger_full.ass")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 "${H264[@]}" -c:a aac -c:s:0 srt -c:s:1 ass \
    -metadata:s:a:0 language=eng -metadata:s:s:0 language=eng -metadata:s:s:1 language=ger \
    -disposition:a:0 default -disposition:s:0 0 -disposition:s:1 0 \
    -attach "$FONT" -metadata:s:t:0 mimetype=application/octet-stream -metadata:s:t:0 filename="NoMimetype.$FONT_EXT" \
    -attach "$A/release-notes.txt" -metadata:s:t:1 mimetype=text/plain -metadata:s:t:1 filename=release-notes.txt \
    "${MKV[@]}" "$OUT/07 - fonts unused.mkv"
}
f08() {  # Good + non-UTF-8 subtitle in a file with audio -> combined pass fails -> BOTH tracks flagged (subtitle_encoding).
  local a=(); add a video_in "08 bad utf8 combined"; add a tone_in 440; add a tone_in 550
  a+=(-i "$A/eng.srt" -i "$A/latin1.srt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 "${H264[@]}" -c:a aac -c:s copy \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger -metadata:s:s:0 language=eng \
    -metadata:s:s:1 language=eng -metadata:s:s:1 title="SDH (latin-1)" \
    -disposition:a:0 default -disposition:a:1 0 -disposition:s:0 0 -disposition:s:1 hearing_impaired "${MKV[@]}" \
    "$OUT/08 - bad utf8 subtitle with remux.mkv"
}
f09() {  # Same subtitles, NO audio and VP9 video (no MP4, so extraction is the only work) -> the two-pass loop.
  # Every audio track gets a copy or drop action, and any action besides an
  # extraction sends the job down the combined pass, so this file has no
  # audio and nothing else to change.
  local a=(); add a video_in "09 bad utf8 two pass"
  a+=(-i "$A/eng.srt" -i "$A/latin1.srt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 \
    -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -b:v 150k -c:s copy \
    -metadata:s:s:0 language=eng -metadata:s:s:1 language=eng -metadata:s:s:1 title="SDH (latin-1)" \
    -disposition:s:0 0 -disposition:s:1 hearing_impaired "${MKV[@]}" \
    "$OUT/09 - bad utf8 subtitle two pass.mkv"
}
f10() {  # Corrupt AAC packets: copy fails in the muxer -> retry with AAC re-encode (remux-only path).
  local a=(); add a video_in "10 corrupt audio remux"; add a tone_in 440; add a tone_in 550
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 "${H264[@]}" -c:a aac \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger -movflags +faststart \
    "$A/clean10.mp4"
  "$PYTHON" "$SCRIPT_DIR/corrupt_aac.py" "$A/clean10.mp4" "$OUT/10 - corrupt audio remux.mp4" a:0 60 >/dev/null
}
f11() {  # Corrupt AAC + a mov_text subtitle to extract -> retry on the combined path.
  local a=(); add a video_in "11 corrupt audio combined"; add a tone_in 440; add a tone_in 550
  a+=(-i "$A/eng.srt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 "${H264[@]}" -c:a aac -c:s mov_text \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger -metadata:s:s:0 language=eng \
    -movflags +faststart "$A/clean11.mp4"
  "$PYTHON" "$SCRIPT_DIR/corrupt_aac.py" "$A/clean11.mp4" "$OUT/11 - corrupt audio combined.mp4" a:0 60 >/dev/null
}
f12() {  # One untagged AAC 5.1 track + two untagged subtitles (tagged und and undefined).
  local a=(); add a video_in "12 und single audio"; add a tone_in 220
  a+=(-i "$A/und1.srt" -i "$A/und2.srt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 "${H264[@]}" -c:a aac -ac 6 -c:s srt \
    -metadata:s:a:0 language=und -metadata:s:s:0 language=und -metadata:s:s:1 language=undefined \
    -disposition:a:0 default -disposition:s:0 0 -disposition:s:1 0 "${MKV[@]}" \
    "$OUT/12 - und single audio.mkv"
}
f13() {  # Two untagged audio tracks + one und and one eng subtitle.
  local a=(); add a video_in "13 und multi audio"; add a tone_in 220; add a tone_in 260
  a+=(-i "$A/und1.srt" -i "$A/eng.srt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 "${H264[@]}" \
    -c:a:0 aac -c:a:1 ac3 -ac:a:1 6 -c:s srt \
    -metadata:s:a:0 language=und -metadata:s:a:1 language=und \
    -metadata:s:s:0 language=und -metadata:s:s:1 language=eng \
    -disposition:a:0 default -disposition:a:1 0 -disposition:s:0 0 -disposition:s:1 0 "${MKV[@]}" \
    "$OUT/13 - und multi audio.mkv"
}
f14() {  # No preferred-language audio (the default-flag safety net), kept WebVTT holds MKV, MKV cover art.
  local a=(); add a video_in "14 no preferred audio"; add a tone_in 770; add a tone_in 660
  a+=(-i "$A/eng.vtt" -i "$A/spa.vtt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 "${H264[@]}" \
    -c:a:0 eac3 -c:a:1 aac -c:s webvtt \
    -metadata:s:a:0 language=ita -metadata:s:a:1 language=spa \
    -metadata:s:s:0 language=eng -metadata:s:s:1 language=spa \
    -disposition:a:0 default -disposition:a:1 0 -disposition:s:0 0 -disposition:s:1 0 \
    -attach "$A/cover.jpg" -metadata:s:t:0 mimetype=image/jpeg -metadata:s:t:0 filename=cover.jpg \
    "${MKV[@]}" "$OUT/14 - no preferred audio.mkv"
}
f15() {  # Two-letter and ISO 639-2/T tags: en / deu / fra.
  local a=(); add a video_in "15 two letter tags"; add a tone_in 440; add a tone_in 550
  a+=(-i "$A/eng.srt" -i "$A/fre.srt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 "${H264[@]}" -c:a aac -c:s srt \
    -metadata:s:a:0 language=en -metadata:s:a:1 language=deu \
    -metadata:s:s:0 language=en -metadata:s:s:1 language=fra \
    -disposition:a:0 default -disposition:a:1 0 -disposition:s:0 0 -disposition:s:1 0 "${MKV[@]}" \
    "$OUT/15 - two letter tags.mkv"
}
f16() {  # Camera-style QuickTime .mov: PCM audio, moov at the end behind a 64-bit mdat header (as any >4 GB file has).
  local a=(); add a video_in "16 camera quicktime mov"; add a tone_in 440
  "${FF[@]}" "${a[@]}" -map 0 -map 1 "${H264[@]}" -c:a pcm_s16le -metadata:s:a:0 language=eng -f mov \
    "$OUT/16 - camera quicktime.mov"
  # FFmpeg writes an 8-byte 'wide' placeholder right before 'mdat' for exactly
  # this upgrade. Rewriting those 16 bytes as size=1 + 'mdat' + 64-bit size
  # changes no sample offset, so the file plays exactly as before.
  "$PYTHON" - "$OUT/16 - camera quicktime.mov" <<'PY'
import struct, sys
p = sys.argv[1]; d = bytearray(open(p, "rb").read()); i = 0
while i < len(d):
    size, kind = struct.unpack(">I4s", d[i:i + 8])
    if kind == b"wide" and size == 8 and d[i + 12:i + 16] == b"mdat":
        mdat = struct.unpack(">I", d[i + 8:i + 12])[0]
        d[i:i + 16] = struct.pack(">I4sQ", 1, b"mdat", mdat + 8)
        open(p, "wb").write(d); sys.exit(0)
    i += size
sys.exit("no wide+mdat pair found")
PY
}
f17() {  # iTunes-style .m4v: cover art, chapters, mov_text forced by title only, a language to drop.
  local a=(); add a video_in "17 itunes m4v"; add a tone_in 440; add a tone_in 550
  a+=(-i "$A/eng_forced.srt" -i "$A/cover.jpg" -i "$A/chapters.txt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 -map 4 -map_chapters 5 \
    "${H264[@]}" -c:v:1 copy -disposition:v:1 attached_pic -c:a aac -c:s mov_text \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger \
    -metadata:s:s:0 language=eng -metadata:s:s:0 title="English (Forced)" \
    -movflags +faststart -f mp4 "$OUT/17 - itunes.m4v"
}
f18() {  # Old Xvid AVI: MPEG-4 ASP with B-frames + MP3, no language tags -> converts to MP4.
  local a=(); add a video_in "18 xvid avi"; add a tone_in 440
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -c:v mpeg4 -vtag XVID -bf 2 -q:v 5 -c:a libmp3lame -b:a 96k \
    "$OUT/18 - xvid.avi"
}
f19() {  # DVB broadcast recording: audio description track + DVB bitmap subtitles -> image review.
  local a=(); add a video_in "19 dvb recording ts"; add a tone_in 440; add a tone_in 445
  a+=(-i "$A/dvb.mks")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3:s:0 "${H264[@]}" -c:a aac -c:s copy \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=eng -metadata:s:s:0 language=eng \
    -disposition:a:1 visual_impaired -f mpegts "$OUT/19 - dvb recording.ts"
}
f20() {  # Blu-ray .m2ts: AC3 5.1 in two languages -> drop one, convert to .mp4.
  local a=(); add a video_in "20 bluray m2ts"; add a tone_in 440; add a tone_in 330
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 "${H264[@]}" -c:a ac3 -ac 6 \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=fre \
    -f mpegts -mpegts_m2ts_mode 1 "$OUT/20 - bluray.m2ts"
}
f21() {  # Windows Media: wmv2 + wmav2 cannot go to MP4 -> remuxed as ASF, keeps .wmv.
  local a=(); add a video_in "21 windows media wmv"; add a tone_in 440; add a tone_in 550
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -c:v wmv2 -b:v 300k -c:a wmav2 -b:a 64k \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger -f asf "$OUT/21 - windows media.wmv"
}
f22() {  # Flash video: H.264 + AAC with no language -> converts to MP4. With MP4 off its job is refused: no muxer entry for flv.
  local a=(); add a video_in "22 flash flv"; add a tone_in 440
  "${FF[@]}" "${a[@]}" -map 0 -map 1 "${H264[@]}" -c:a aac -f flv "$OUT/22 - flash.flv"
}
f23() {  # YouTube-style WebM: VP9 + Opus + WebVTT. Reads as mkv, keeps .webm.
  local a=(); add a video_in "23 youtube webm"; add a tone_in 440
  a+=(-i "$A/eng.vtt" -i "$A/ger.vtt")
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 -map 3 \
    -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -b:v 150k -c:a libopus -b:a 48k -c:s webvtt \
    -metadata:s:a:0 language=eng -metadata:s:s:0 language=eng -metadata:s:s:1 language=ger \
    -f webm "$OUT/23 - youtube.webm"
}
f24() {  # Matroska bytes behind an .mp4 name: converts to MP4 at the SAME path.
  local a=(); add a video_in "24 mkv named mp4"; add a tone_in 440; add a tone_in 550
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -map 2 "${H264[@]}" -c:a aac \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger \
    -disposition:a:0 default -disposition:a:1 0 "${MKV[@]}" -f matroska "$OUT/24 - matroska named mp4.mp4"
}
f25() {  # No video stream at all: two FLAC tracks, one to drop.
  local a=(); add a tone_in 440; add a tone_in 550
  "${FF[@]}" "${a[@]}" -map 0 -map 1 -c:a flac \
    -metadata:s:a:0 language=eng -metadata:s:a:1 language=ger \
    -disposition:a:0 default -disposition:a:1 0 "${MKV[@]}" "$OUT/25 - audio only.mkv"
}

f26() {  # A zero-byte .mkv (an interrupted copy): ffprobe fails, the scan records an error.
  : > "$OUT/26 - unreadable empty file.mkv"
}

for n in $(seq -w 1 26); do
  log "Building file $n"
  "f$n" || die "file $n failed"
done

# ── Folder layout: extras, excludes, existing sidecars ───────────────────────
# Copies (not links: Remuxarr rewrites files in place) of small files above.
# None of these are new media — they exercise exclude.py and the sidecar
# rules, which look at names and folders, never at the content.
log "Building the folder layout"
L="$OUT/layout"
mv_copy() { mkdir -p "$(dirname "$2")"; cp -- "$OUT/$1" "$2"; }
M="$L/Movies/Test Movie (2024)"
mv_copy "03 - webdl typical.mkv"  "$M/Test Movie (2024).mkv"
srt "$M/Test Movie (2024).en.srt" "Existing sidecar (as if Bazarr downloaded it)"
mv_copy "02 - no faststart.mp4"   "$M/Test Movie (2024)-trailer.mp4"           # extras suffix -> skipped
mv_copy "02 - no faststart.mp4"   "$M/sample.mp4"                                # extras name -> skipped
mv_copy "02 - no faststart.mp4"   "$M/Featurettes/Making Of.mp4"                 # extras folder -> skipped
mv_copy "02 - no faststart.mp4"   "$M/Plex Versions/Optimized for TV/Test Movie (2024).mp4"  # -> skipped
mv_copy "02 - no faststart.mp4"   "$L/Movies/Shorts/A Short Film (2019).mp4"    # collection named Shorts -> processed
mv_copy "02 - no faststart.mp4"   "$L/Movies/Shorts/Another Short (2021).mp4"
mv_copy "02 - no faststart.mp4"   "$L/TV/Extras/Season 01/Extras - S01E01 - Andy Millman.mp4"  # sitcom called Extras -> processed
mv_copy "04 - subtitle naming.mkv" "$L/TV/Show.dub/Season 01/Show.dub.S01E01.mkv"  # .dub. in the stem must not tag sidecars
mv_copy "02 - no faststart.mp4"   "$L/TV/Anime/Season 01/Anime - S01E01 - trailer.mp4"  # ends ' trailer' with a space -> processed
mv_copy "02 - no faststart.mp4"   "$L/TV/Extras/Extras - S00E01 - Christmas Special.mp4"  # folder named Extras holds seasons -> a show, processed
mv_copy "02 - no faststart.mp4"   "$L/TV/Some Show/Season 01/Some Show - S01E01.mp4"
mv_copy "02 - no faststart.mp4"   "$L/TV/Some Show/Featurettes/Behind the Scenes.mp4"  # beside season folders -> skipped
C="$L/Movies/Collision (2023)"                                    # Collision.mkv converts to Collision.mp4,
mv_copy "24 - matroska named mp4.mp4" "$C/Collision (2023).mkv"  # which is already taken by a different
mv_copy "01 - clean faststart.mp4"    "$C/Collision (2023).mp4"  # file -> the job is refused, nothing overwritten

rm -rf -- "$A"
log "Done: $(find "$OUT" -type f | wc -l) files, $(du -sh "$OUT" | cut -f1)"
