import asyncio
from pathlib import Path

import pytest

from cam_recorder.archive import Archive, hls_playlist, merge
from cam_recorder.events import Event
from cam_recorder.segments import Segment, parse_name
from cam_recorder.state import StateDB

T0 = parse_name("20261003T200000Z.ts")


@pytest.fixture
def arch(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    d = tmp_path / "share" / "vorzimmer" / "2026-10-03"
    d.mkdir(parents=True)
    for i in range(6):
        name = f"20261003T2000{i * 8:02d}Z.ts"
        p = d / name
        p.write_bytes(b"x" * 100)
        db.add_segment(Segment("vorzimmer", str(p), T0 + i * 8, T0 + (i + 1) * 8, 100))
    # Lücke, dann noch eines
    p = d / "20261003T200200Z.ts"
    p.write_bytes(b"x")
    db.add_segment(Segment("vorzimmer", str(p), T0 + 120, T0 + 128, 1))
    ev = Event(cam="vorzimmer", start=T0 + 20, end=T0 + 40, id="abc123def456", reason="quiet")
    db.save_event(ev)
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    (snaps / "abc123def456.jpg").write_bytes(b"jpg")
    return Archive(db, 30, snaps), tmp_path


def test_merge_joins_close_spans():
    assert [(s.start, s.end) for s in merge([(0, 8), (8, 16), (40, 48)])] == [(0, 16), (40, 48)]


def test_coverage_and_events(arch):
    a, _ = arch
    cov = a.coverage("vorzimmer", T0, T0 + 3600)
    assert [(s.start - T0, s.end - T0) for s in cov] == [(0, 48), (120, 128)]
    evs = a.events(T0, T0 + 3600)
    assert evs[0]["id"] == "abc123def456" and evs[0]["snapshot"] is True


def test_segment_path_only_known_files(arch):
    a, _ = arch
    assert a.segment_path("vorzimmer", "20261003T200008Z.ts") is not None
    assert a.segment_path("vorzimmer", "../../etc/passwd") is None
    assert a.segment_path("vorzimmer", "20991231T000000Z.ts") is None
    assert a.segment_path("wohnzimmer", "20261003T200008Z.ts") is None
    assert a.snapshot_path("../x") is None and a.snapshot_path("abc123def456") is not None


def test_playlist_has_discontinuities_and_relative_urls(arch):
    a, _ = arch
    segs = a.segments("vorzimmer", T0, T0 + 24)
    pl = hls_playlist(segs, lambda s: "../seg/vorzimmer/" + Path(s.path).name)
    assert pl.startswith("#EXTM3U") and pl.rstrip().endswith("#EXT-X-ENDLIST")
    assert pl.count("#EXT-X-DISCONTINUITY") == len(segs) - 1
    assert "#EXT-X-TARGETDURATION:8" in pl and "../seg/vorzimmer/20261003T200000Z.ts" in pl


def test_history_and_vod_endpoints(arch, monkeypatch):
    aiohttp = pytest.importorskip("aiohttp")  # noqa: F841
    from aiohttp.test_utils import TestClient, TestServer
    from cam_recorder import web
    monkeypatch.setattr(web, "is_ingress", lambda request: True)
    a, _ = arch
    snap = {"title": "T", "cams": [{"id": "vorzimmer"}]}

    async def noop(*_):
        return True

    app = web.make_app(snapshot=lambda: snap, subscribe=lambda: asyncio.Queue(), unsubscribe=lambda q: None,
                       set_off=noop, set_all_off=noop, stream_names=set(), kiosk_key="",
                       archive=a, storage_stats=lambda: {"store": "primary", "used_bytes": 601})

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await c.get(f"/api/history?start={T0}&end={T0 + 3600}")
            j = await r.json()
            assert j["coverage"]["vorzimmer"][0] == [T0, T0 + 48] and j["storage"]["used_bytes"] == 601
            assert (await c.get("/api/history?start=5&end=1")).status == 400
            r = await c.get(f"/api/vod/vorzimmer.m3u8?start={T0}&end={T0 + 24}")
            assert r.status == 200 and "mpegurl" in r.headers["Content-Type"]
            r = await c.get("/api/seg/vorzimmer/20261003T200008Z.ts")
            assert r.status == 200 and r.headers["Content-Type"] == "video/mp2t"
            assert (await c.get("/api/seg/vorzimmer/..%2F..%2Fetc%2Fpasswd")).status == 404
            assert (await c.get("/api/snapshot/abc123def456.jpg")).status == 200
    asyncio.run(go())
