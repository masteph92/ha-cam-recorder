import json
import os
from pathlib import Path

import pytest

from cam_recorder.config import ConfigError, parse
from cam_recorder.go2rtc import render_config
from cam_recorder.recorder import _redact, collect_completed, ffmpeg_cmd
from cam_recorder.segments import parse_name
from cam_recorder.uploader import rclone_cmd, remote_dst

RAW = {
    "site": "mg",
    "cameras": [{"name": "vorzimmer", "rtsp": "rtsp://cam:x@192.168.1.201:554/stream1",
                 "triggers": ["binary_sensor.kamera_vorzimmer_motion_alarm"],
                 "suppress": "switch.kamera_vorzimmer_privacy"}],
    "event": {"pre_seconds": 30, "post_seconds": 60, "max_seconds": 600},
    "storage": {"primary": "/share/cam-recorder", "primary_max_gb": 450},
    "upload": {"enabled": False},
}


def test_config_parses_and_defaults():
    cfg = parse(RAW)
    assert cfg.site == "mg" and cfg.cameras[0].triggers == ("binary_sensor.kamera_vorzimmer_motion_alarm",)
    assert cfg.fallback == "/tmp/cam-recorder" and cfg.audio is False
    assert cfg.primary_max_bytes == 450 * 1024**3


@pytest.mark.parametrize("patch,msg", [
    ({"site": "M G"}, "site"),
    ({"cameras": [{"name": "Wohn Zimmer", "rtsp": "rtsp://x"}]}, "camera name"),
    ({"cameras": [{"name": "a", "rtsp": "http://x"}]}, "rtsp"),
    ({"upload": {"enabled": True, "remote": "b2:x"}}, "upload"),
    ({"event": {"post_seconds": 600, "max_seconds": 600}}, "max_seconds"),
])
def test_config_rejects(patch, msg):
    with pytest.raises(ConfigError, match=msg):
        parse({**RAW, **patch})


def test_b2_key_not_in_repr():
    cfg = parse({**RAW, "upload": {"enabled": True, "remote": "b2:b/mg", "b2_account": "a", "b2_key": "SECRET"}})
    assert "SECRET" not in repr(cfg)


def test_ffmpeg_cmd_copies_video_only_by_default(tmp_path):
    cmd = ffmpeg_cmd("rtsp://127.0.0.1:8554/vorzimmer", tmp_path, tmp_path / "l.csv", 10, audio=False)
    assert cmd[cmd.index("-map") + 1] == "0:v:0" and "-c:a" not in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[-1].endswith("%Y%m%dT%H%M%SZ.ts")
    with_audio = ffmpeg_cmd("rtsp://x", tmp_path, tmp_path / "l.csv", 10, audio=True)
    assert with_audio[with_audio.index("-c:a") + 1] == "aac"


def _ts(d: Path, name: str, size=1000, mtime=None):
    p = d / name
    p.write_bytes(b"x" * size)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_collect_moves_only_listed_while_running(tmp_path):
    inc = tmp_path / "vorzimmer" / "incoming"
    inc.mkdir(parents=True)
    _ts(inc, "20261003T200000Z.ts")
    _ts(inc, "20261003T200008Z.ts")
    _ts(inc, "20261003T200016Z.ts")  # still being written
    (inc / "segments-1.csv").write_text("20261003T200000Z.ts,0,8\n20261003T200008Z.ts,8,16.5\n")
    done = collect_completed(inc, "vorzimmer", tmp_path, "primary", running=True)
    assert [Path(s.path).name for s in done] == ["20261003T200000Z.ts", "20261003T200008Z.ts"]
    assert done[1].duration == pytest.approx(8.5)
    assert Path(done[0].path).parent == tmp_path / "vorzimmer" / "2026-10-03"
    assert (inc / "20261003T200016Z.ts").exists()


def test_collect_leftovers_after_crash(tmp_path):
    inc = tmp_path / "c" / "incoming"
    inc.mkdir(parents=True)
    start = parse_name("20261003T200016Z.ts")
    _ts(inc, "20261003T200016Z.ts", mtime=start + 7)
    done = collect_completed(inc, "c", tmp_path, "fallback", running=False)
    assert len(done) == 1 and done[0].duration == pytest.approx(7) and done[0].store == "fallback"


def test_redact_hides_credentials():
    assert _redact("rtsp://cam:p%40ss@192.168.1.201:554/stream1: 401") == "rtsp://***@192.168.1.201:554/stream1: 401"


def test_go2rtc_single_source_per_camera():
    conf = render_config(parse(RAW))
    assert conf["streams"] == {"vorzimmer": ["rtsp://cam:x@192.168.1.201:554/stream1"]}
    json.dumps(conf)


def test_remote_paths():
    assert remote_dst("b2:cam-clips/mg/", "vorzimmer", "2026-10-03", "abc", "x.ts") == "b2:cam-clips/mg/vorzimmer/2026-10-03/abc/x.ts"
    assert rclone_cmd("/a", "b2:x")[:3] == ["rclone", "copyto", "--no-check-dest"]


def test_collect_never_overwrites_existing_segment(tmp_path):
    inc = tmp_path / "c" / "incoming"
    inc.mkdir(parents=True)
    day = tmp_path / "c" / "2026-10-03"
    day.mkdir(parents=True)
    (day / "20261003T200000Z.ts").write_bytes(b"old")
    _ts(inc, "20261003T200000Z.ts", mtime=parse_name("20261003T200000Z.ts") + 5)
    done = collect_completed(inc, "c", tmp_path, "primary", running=False)
    assert Path(done[0].path).name == "20261003T200000Z-1.ts"
    assert (day / "20261003T200000Z.ts").read_bytes() == b"old"
    assert parse_name("20261003T200000Z-1.ts") == parse_name("20261003T200000Z.ts")
