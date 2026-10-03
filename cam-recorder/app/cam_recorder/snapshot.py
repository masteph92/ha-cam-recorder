"""Event snapshot: from the camera's snapshot URL (Hikvision ISAPI, digest
auth via curl) or as last frame of the newest finished segment."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def frame_cmd(segment: str, out: Path) -> list[str]:
    # First frame: every segment starts on a keyframe. "-sseof" seeks past the
    # last keyframe in MPEG-TS and silently writes nothing.
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", segment, "-frames:v", "1", "-update", "1", "-q:v", "3", str(out)]


def url_cmd(url: str, out: Path) -> list[str]:
    return ["curl", "-sf", "--digest", "--max-time", "8", "-o", str(out), url]


async def take(out: Path, segment: str | None, url: str | None) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    for cmd in ([url_cmd(url, out)] if url else []) + ([frame_cmd(segment, out)] if segment else []):
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        if await proc.wait() == 0 and out.exists() and out.stat().st_size > 0:
            return True
    log.warning("snapshot failed: %s", out.name)
    return False
