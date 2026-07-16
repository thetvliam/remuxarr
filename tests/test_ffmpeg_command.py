"""
Regression tests for FFmpeg/forge command building — specifically the
container→mux-format mapping.

Split from test_decision.py deliberately: that suite is scoped to
decision.py's pure logic only, and these tests exercise the command
builders in ffmpeg.py / forge.py (still pure — no disk, no DB — they
just build argv lists).
"""
import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import build_ffmpeg_command, determine_output_path
from app.core.forge import build_add_ac3_command, build_undo_command
from tests.conftest import make_track, make_file_info


def _flv_settings():
    return {
        "keep_audio_languages": ["eng"], "keep_subtitle_languages": ["eng"],
        "und_audio_threshold": 2, "fix_undefined_language": "always_leave",
        "prefer_mp4_container": True,
    }


def test_unknown_container_raises_instead_of_silently_muxing_matroska():
    """
    Regression for a real P1 file-corruption bug found by independent
    review: MEDIA_EXTENSIONS accepts containers (e.g. .flv) that
    _normalise_container passes through unchanged and _CONTAINER_FORMAT
    has no entry for. The old `.get(..., "matroska")` default meant such
    a file — processed for ANY reason, e.g. a simple track drop — got
    Matroska bytes written into its original path in place, with the
    original deleted after "success". analyze_file's own container guard
    can't catch it (the container string is present and non-empty; it
    just isn't muxable-as-itself), so the command builder must hard-fail
    rather than guess. The ValueError is caught by the worker's
    job-level handler and becomes a visible failed job, raised before
    FFmpeg ever starts — the source file is never touched.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="flv1"),
        make_track(stream_index=1, track_type="audio", codec="mp3",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="mp3",
                   language="fre"),
    ]
    file_info = make_file_info(path="/media/x/clip.flv", container="flv",
                                video_codec="flv1")
    decision = analyze_file(file_info, tracks, _flv_settings())

    # The decision itself legitimately wants to process (drop the French
    # track) — the failure must come from the command builder, which is
    # the last line of defence before bytes hit disk.
    assert decision.should_process is True
    assert decision.target_container == "flv"

    out = determine_output_path("/media/x/clip.flv", decision)
    with pytest.raises(ValueError, match="Unsupported output container 'flv'"):
        build_ffmpeg_command("/media/x/clip.flv", out, decision, tracks)


def test_all_supported_containers_still_build():
    """Every container _CONTAINER_FORMAT actually knows must keep working."""
    expected = {
        "mkv": "matroska", "mp4": "mp4", "avi": "avi", "ts": "mpegts",
        "wmv": "asf", "webm": "webm", "mov": "mov",
    }
    for container, fmt in expected.items():
        tracks = [
            make_track(stream_index=0, track_type="video", codec="h264"),
            make_track(stream_index=1, track_type="audio", codec="aac",
                       language="eng", is_default=True),
            make_track(stream_index=2, track_type="audio", codec="aac",
                       language="fre"),
        ]
        file_info = make_file_info(path=f"/media/x/f.{container}",
                                    container=container, video_codec="h264")
        decision = analyze_file(
            file_info, tracks,
            {**_flv_settings(), "prefer_mp4_container": False},
        )
        assert decision.should_process is True
        out = determine_output_path(file_info["path"], decision)
        cmd = build_ffmpeg_command(file_info["path"], out, decision, tracks)
        assert cmd[cmd.index("-f") + 1] == fmt, (
            f"{container}: expected -f {fmt}, got {cmd[cmd.index('-f') + 1]}"
        )


def test_forge_builders_hard_fail_on_unknown_containers():
    """
    Both forge command builders had the identical silent-matroska
    default — and forge's own map is even narrower than ffmpeg.py's
    (no "mov" entry), so a forge job on a .mov would have hit it too.
    """
    with pytest.raises(ValueError, match="Unsupported container 'flv'"):
        build_add_ac3_command("/media/x/a.flv", "/tmp/t", 1, 1, container="flv")
    with pytest.raises(ValueError, match="Unsupported container 'mov'"):
        build_undo_command("/media/x/a.mov", "/tmp/t", 0, container="mov")
    # Known containers keep working
    cmd = build_add_ac3_command("/media/x/a.mkv", "/tmp/t", 1, 1, container="mkv")
    assert cmd[cmd.index("-f") + 1] == "matroska"
