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


def render_config(cfg: Config) -> dict:
    return {
        "api": {"listen": f":{API_PORT}"},
        "rtsp": {"listen": f":{RTSP_PORT}"},
        "webrtc": {"listen": ""},
        "log": {"level": "info", "format": "text"},
        "streams": {c.name: [c.rtsp] for c in cfg.cameras},
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
