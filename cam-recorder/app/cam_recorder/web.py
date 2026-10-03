"""Web UI: start view (tablet / monitor), live state, camera on/off, and a
narrow proxy to go2rtc for the live video.

Two ways in, one port:
- HA ingress (the Supervisor proxy at 172.30.32.2): HA already checked the
  login, everything is allowed.
- the LAN port for wall tablets: only with the kiosk key (?key=… once, then a
  cookie). No key configured -> the LAN port answers 403.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
from aiohttp import web

from .go2rtc import API_PORT

log = logging.getLogger(__name__)

PORT = 8099
INGRESS_PEER = "172.30.32.2"
COOKIE = "cam_recorder_key"
STATIC = Path(__file__).parent / "web"
GO2RTC = f"http://127.0.0.1:{API_PORT}"
GO2RTC_FILES = {"video-rtc.js", "video-stream.js"}


def is_ingress(request: web.Request) -> bool:
    return request.remote == INGRESS_PEER


def key_ok(request: web.Request, key: str) -> bool:
    if not key:
        return False
    given = request.query.get("key") or request.cookies.get(COOKIE) or ""
    return hmac.compare_digest(given.encode(), key.encode())


def make_app(*, snapshot: Callable[[], dict], subscribe, unsubscribe,
             set_off: Callable[[str, bool], Awaitable[bool]], set_all_off: Callable[[bool], Awaitable[None]],
             stream_names: set[str], kiosk_key: str) -> web.Application:

    @web.middleware
    async def auth(request: web.Request, handler):
        if is_ingress(request):
            return await handler(request)
        if not key_ok(request, kiosk_key):
            return web.Response(status=403, text="Kiosk-Schlüssel fehlt oder ist falsch.\n")
        resp = await handler(request)
        if request.query.get("key") and isinstance(resp, web.StreamResponse) and not resp.prepared:
            resp.set_cookie(COOKIE, kiosk_key, httponly=True, samesite="Strict", max_age=400 * 86400)
        return resp

    async def index(request: web.Request) -> web.StreamResponse:
        resp = web.FileResponse(STATIC / "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def state(request: web.Request) -> web.Response:
        return web.json_response(snapshot())

    async def events(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        q = subscribe()
        try:
            await resp.write(f"data: {json.dumps(snapshot())}\n\n".encode())
            while True:
                try:
                    await asyncio.wait_for(q.get(), 25)
                    await resp.write(f"data: {json.dumps(snapshot())}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            unsubscribe(q)
        return resp

    async def cam_off(request: web.Request) -> web.Response:
        body = await request.json()
        ok = await set_off(request.match_info["cam"], bool(body.get("off")))
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    async def all_off(request: web.Request) -> web.Response:
        body = await request.json()
        await set_all_off(bool(body.get("off")))
        return web.json_response({"ok": True})

    async def go2rtc_file(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        if name not in GO2RTC_FILES:
            raise web.HTTPNotFound()
        async with aiohttp.ClientSession() as s, s.get(f"{GO2RTC}/{name}") as r:
            body = await r.read()
        return web.Response(body=body, content_type="application/javascript",
                            headers={"Cache-Control": "max-age=3600"})

    async def go2rtc_ws(request: web.Request) -> web.StreamResponse:
        src = request.query.get("src", "")
        if src not in stream_names:
            raise web.HTTPNotFound()
        client = web.WebSocketResponse(max_msg_size=0)
        await client.prepare(request)
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{GO2RTC.replace('http', 'ws')}/api/ws?src={src}", max_msg_size=0) as up:
                async def pump(a, b):
                    async for msg in a:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await b.send_str(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await b.send_bytes(msg.data)
                        else:
                            break
                tasks = [asyncio.create_task(pump(client, up)), asyncio.create_task(pump(up, client))]
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        await client.close()
        return client

    app = web.Application(middlewares=[auth])
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/cams/{cam}/off", cam_off)
    app.router.add_post("/api/all/off", all_off)
    app.router.add_get("/go2rtc/api/ws", go2rtc_ws)
    app.router.add_get("/go2rtc/{name}", go2rtc_file)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


async def serve(app: web.Application, port: int = PORT) -> None:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("web UI on :%d", port)
    while True:
        await asyncio.sleep(3600)


def now() -> float:
    return time.time()
