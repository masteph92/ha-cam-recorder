"""App logic end to end, without HA, ffmpeg or rclone: fake clock, temp DB."""

from pathlib import Path

import pytest

from cam_recorder import storage
from cam_recorder.app import App
from cam_recorder.config import parse
from cam_recorder.segments import Segment, parse_name
from cam_recorder.state import StateDB

T0 = parse_name("20261003T200000Z.ts")
MOTION = "binary_sensor.kamera_vorzimmer_motion_alarm"
PRIVACY = "switch.kamera_vorzimmer_privacy"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    raw = {
        "site": "mg",
        "segment_seconds": 10,
        "cameras": [{"name": "vorzimmer", "rtsp": "rtsp://x", "triggers": [MOTION], "suppress": PRIVACY}],
        "event": {"pre_seconds": 30, "post_seconds": 60, "max_seconds": 600},
        "storage": {"primary": str(tmp_path / "share"), "primary_max_gb": 0.000001,
                    "fallback": str(tmp_path / "tmp"), "fallback_keep_seconds": 120},
        "upload": {"enabled": True, "remote": "b2:cam-clips/mg", "b2_account": "a", "b2_key": "k",
                   "daily_budget_gb": 0.00001},
    }
    clock = Clock(T0)
    db = StateDB(tmp_path / "state.sqlite")
    app = App(parse(raw), db, tmp_path / "data", clock=clock)
    events = []
    app.notify = lambda kind, data: events.append((kind, data))
    return app, db, clock, events, tmp_path


def add_segments(app, tmp_path, start_i, end_i, size=1000):
    d = tmp_path / "share" / "vorzimmer"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(start_i, end_i):
        p = d / f"{i}.ts"
        p.write_bytes(b"x" * size)
        app.on_segment(Segment("vorzimmer", str(p), T0 + i * 10, T0 + (i + 1) * 10, size))


def queued(db):
    return [Path(r[0]).name for r in db.db.execute("SELECT src FROM queue ORDER BY id")]


def test_event_uploads_pre_roll_live_segments_and_meta(env):
    app, db, clock, events, tmp = env
    app.cfg = app.cfg.__class__(**{**app.cfg.__dict__, "daily_budget_gb": 1})
    add_segments(app, tmp, 0, 10)  # 0..100 s quiet
    clock.t = T0 + 100
    app.on_ha_state(MOTION, "on")
    app.on_ha_state(MOTION, "off")
    # pre-roll 30 s: segments 7, 8, 9
    assert queued(db) == ["7.ts", "8.ts", "9.ts"]
    assert events[0][0] == "cam_recorder_event_start"
    # real time: each segment finishes 10 s after its start
    for i in range(10, 20):
        clock.t = T0 + (i + 1) * 10
        add_segments(app, tmp, i, i + 1)
        app.tick()
    # closed at 160: segments 10..15 overlap [70, 160], 16.. start at 160
    ends = [e for e in events if e[0] == "cam_recorder_event_end"]
    assert ends and ends[0][1]["end"] == T0 + 160 and ends[0][1]["reason"] == "quiet"
    q = queued(db)
    assert q[:9] == ["7.ts", "8.ts", "9.ts", "10.ts", "11.ts", "12.ts", "13.ts", "14.ts", "15.ts"]
    assert "16.ts" not in q
    # event.json comes 2 segments after the end and lists the closing segment
    last_dst = db.db.execute("SELECT dst FROM queue ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert last_dst.startswith("b2:cam-clips/mg/vorzimmer/2026-10-03/") and last_dst.endswith("/event.json")
    meta = (tmp / "data" / "events").glob("*.json").__next__().read_text()
    assert '"15.ts"' in meta and '"7.ts"' in meta


def test_budget_stops_queueing(env):
    app, db, clock, events, tmp = env  # budget ~10.7 kB
    add_segments(app, tmp, 0, 3, size=6000)
    clock.t = T0 + 30
    app.on_ha_state(MOTION, "on")
    assert len(queued(db)) == 1 and "vorzimmer" in app.over_budget


def test_privacy_closes_event_and_stops_triggers(env):
    app, db, clock, events, tmp = env
    clock.t = T0 + 10
    app.on_ha_state(MOTION, "on")
    clock.t = T0 + 20
    app.on_ha_state(PRIVACY, "on")
    assert events[-1][1]["reason"] == "suppressed"
    clock.t = T0 + 30
    app.on_ha_state(MOTION, "off")
    app.on_ha_state(MOTION, "on")
    assert [e for e in events if e[0] == "cam_recorder_event_start"].__len__() == 1


def test_cleanup_respects_pending_uploads(env):
    app, db, clock, events, tmp = env  # primary limit ~1 kB
    add_segments(app, tmp, 0, 10)
    clock.t = T0 + 100
    app.on_ha_state(MOTION, "on")  # queues 7, 8, 9
    n = app.clean()
    left = {Path(s.path).name for s in db.segments("primary")}
    assert {"7.ts", "8.ts", "9.ts"} <= left
    assert n == 7


def test_storage_switches_to_fallback_without_marker(env):
    app, db, clock, events, tmp = env
    (tmp / "share").mkdir()
    assert app.check_storage().kind == "fallback"
    (tmp / "share" / storage.MARKER).touch()
    assert app.check_storage().kind == "primary"


def test_restart_closes_dangling_events(env):
    app, db, clock, events, tmp = env
    app.on_ha_state(MOTION, "on")
    assert db.close_dangling_events(T0 + 5) == 1
