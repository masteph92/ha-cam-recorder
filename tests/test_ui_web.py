import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from cam_recorder import web  # noqa: E402
from cam_recorder.config import parse  # noqa: E402
from cam_recorder.go2rtc import render_config, stream_names  # noqa: E402
from cam_recorder.ui import UiState  # noqa: E402

RAW = {
    "site": "garage",
    "cameras": [
        {"name": "g48", "rtsp": "rtsp://x/1", "triggers": ["binary_sensor.a"], "wired": True},
        {"name": "g65", "rtsp": "rtsp://x/2", "rtsp_sub": "rtsp://x/2s", "triggers": ["binary_sensor.b"],
         "door_entity": "binary_sensor.tor65", "signal_entity": "sensor.cpe65", "label": "Garage 65"},
    ],
    "ui": {"title": "Garagen", "kiosk_key": "geheim-geheim-123"},
}


def test_snapshot_and_changes():
    ui = UiState(parse(RAW))
    q = ui.subscribe()
    ui.motion("g65", 100.0)
    ui.door("g65", "on")
    ui.signal("g65", "-72")
    snap = ui.snapshot(200.0)
    g65 = snap["cams"][1]
    assert snap["title"] == "Garagen" and g65["label"] == "Garage 65"
    assert g65["motion_since"] == 100.0 and g65["door"] == "open" and g65["signal"] == -72.0
    assert snap["cams"][0]["label"] == "G48" and snap["cams"][0]["wired"] is True
    assert q.qsize() == 1  # coalesced
    ui.signal("g65", "unavailable")
    assert ui.snapshot(0)["cams"][1]["lost"] is True


def test_off_hides_motion():
    ui = UiState(parse(RAW))
    ui.motion("g48", 5.0)
    ui.pause("g48", True)
    c = ui.snapshot(0)["cams"][0]
    assert c["off"] and c["off_reason"] == "pause" and c["motion_since"] is None


def test_sub_streams_never_open_second_camera_session():
    conf = render_config(parse(RAW))
    assert conf["streams"]["g65_sub"] == ["rtsp://x/2s"]
    assert conf["streams"]["g48_sub"] == ["rtsp://127.0.0.1:8554/g48"]
    assert conf["api"]["listen"].startswith("127.0.0.1:")
    assert stream_names(parse(RAW)) == {"g48", "g48_sub", "g65", "g65_sub"}


def test_short_kiosk_key_rejected():
    from cam_recorder.config import ConfigError
    with pytest.raises(ConfigError, match="kiosk_key"):
        parse({**RAW, "ui": {"kiosk_key": "kurz"}})


def _app(key):
    ui = UiState(parse(RAW))
    offs = []

    async def set_off(cam, off):
        offs.append((cam, off))
        return cam in ("g48", "g65")

    async def set_all(off):
        offs.append(("*", off))

    app = web.make_app(snapshot=lambda: ui.snapshot(0), subscribe=ui.subscribe, unsubscribe=ui.unsubscribe,
                       set_off=set_off, set_all_off=set_all, stream_names={"g48"}, kiosk_key=key)
    return app, offs


def run(coro):
    return asyncio.run(coro)


def test_lan_needs_key_then_cookie():
    async def go():
        app, offs = _app("geheim-geheim-123")
        async with TestClient(TestServer(app)) as c:
            assert (await c.get("/api/state")).status == 403
            assert (await c.get("/api/state?key=falsch")).status == 403
            r = await c.get("/api/state?key=geheim-geheim-123")
            assert r.status == 200 and (await r.json())["title"] == "Garagen"
            assert (await c.get("/api/state")).status == 200  # cookie
            r = await c.post("/api/cams/g65/off", json={"off": True})
            assert r.status == 200 and offs == [("g65", True)]
            assert (await c.post("/api/cams/nope/off", json={"off": True})).status == 404
            assert (await c.get("/go2rtc/api.json")).status == 404  # go2rtc stays closed
    run(go())


def test_lan_closed_without_key():
    async def go():
        app, _ = _app("")
        async with TestClient(TestServer(app)) as c:
            assert (await c.get("/api/state?key=")).status == 403
            assert (await c.get("/")).status == 403
    run(go())


def test_ingress_peer_is_trusted(monkeypatch):
    monkeypatch.setattr(web, "is_ingress", lambda request: True)

    async def go():
        app, _ = _app("")
        async with TestClient(TestServer(app)) as c:
            assert (await c.get("/api/state")).status == 200
            r = await c.get("/")
            assert r.status == 200 and "ui/app.js" in await r.text()
            r = await c.get("/ui/app.js")
            assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
    run(go())


def test_live_websocket_pushes_snapshot_and_changes(monkeypatch):
    monkeypatch.setattr(web, "is_ingress", lambda request: True)
    ui = UiState(parse(RAW))

    async def noop(*a):
        return True

    app = web.make_app(snapshot=lambda: ui.snapshot(0), subscribe=ui.subscribe, unsubscribe=ui.unsubscribe,
                       set_off=noop, set_all_off=noop, stream_names=set(), kiosk_key="")

    async def go():
        async with TestClient(TestServer(app)) as c:
            ws = await c.ws_connect("/api/live")
            first = await ws.receive_json(timeout=2)
            assert first["cams"][1]["motion_since"] is None
            ui.motion("g65", 42.0)
            second = await ws.receive_json(timeout=2)
            assert second["cams"][1]["motion_since"] == 42.0
            await ws.close()
    run(go())
