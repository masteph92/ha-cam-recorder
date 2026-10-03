"""go2rtc as the single RTSP consumer per camera.

Recorder (and later the UI / HA live view) read rtsp://127.0.0.1:8554/<cam>;
go2rtc holds exactly one session to the camera.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .config import Config
from .recorder import _redact

log = logging.getLogger(__name__)

RTSP_PORT = 8554
API_PORT = 1984


SUB_SUFFIX = "_sub"


def stream_names(cfg: Config) -> set[str]:
    return {c.name for c in cfg.cameras} | {c.name + SUB_SUFFIX for c in cfg.cameras}


def render_config(cfg: Config) -> dict:
    streams: dict[str, list[str]] = {}
    for c in cfg.cameras:
        streams[c.name] = [c.rtsp]
        # without a substream the tablet name loops back to go2rtc's own
        # restream of the main stream - no second session to the camera
        streams[c.name + SUB_SUFFIX] = [c.rtsp_sub] if c.rtsp_sub else [local_url(c.name)]
    return {
        # API only on localhost: the add-on's web server proxies the few
        # paths the UI needs and nothing else (go2rtc's config editor stays shut)
        "api": {"listen": f"127.0.0.1:{API_PORT}"},
        "rtsp": {"listen": f":{RTSP_PORT}"},
        "webrtc": {"listen": ""},
        "log": {"level": "info", "format": "text"},
        "streams": streams,
    }


def local_url(cam: str) -> str:
    return f"rtsp://127.0.0.1:{RTSP_PORT}/{cam}"


async def run(cfg: Config, workdir: Path = Path("/tmp")) -> None:
    path = workdir / "go2rtc.yaml"
    path.write_text(json.dumps(render_config(cfg), indent=2))  # JSON is valid YAML
    path.chmod(0o600)
    backoff = 2.0
    while True:
        proc = await asyncio.create_subprocess_exec(
            "go2rtc", "-config", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdout
        async for line in proc.stdout:
            msg = line.decode(errors="replace").rstrip()
            if msg:
                log.info("go2rtc: %s", _redact(msg))
        rc = await proc.wait()
        log.error("go2rtc exited %s, restart in %.0fs", rc, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
