"""One ffmpeg per camera, reading the go2rtc restream, writing segments.

The camera itself only ever sees go2rtc as consumer (Tapo C100: max. two RTSP
sessions). ffmpeg only copies (-c copy), no transcoding.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from .segments import FFMPEG_PATTERN, Segment, day_dir, incoming_dir, parse_name, parse_segment_list

log = logging.getLogger(__name__)

BACKOFF_START = 2.0
BACKOFF_MAX = 60.0
POLL = 2.0
STARTUP_DELAY = 3.0
LIST_PREFIX = "segments-"


def ffmpeg_cmd(src: str, incoming: Path, list_path: Path, segment_seconds: int, audio: bool) -> list[str]:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-rtsp_transport", "tcp", "-timeout", "10000000",
        "-i", src,
        "-map", "0:v:0",
    ]
    if audio:
        # G.711 does not go into MPEG-TS; AAC at 8 kHz costs next to nothing.
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "32k"]
    cmd += [
        "-c:v", "copy",
        "-f", "segment", "-segment_time", str(segment_seconds),
        "-segment_format", "mpegts", "-reset_timestamps", "1", "-strftime", "1",
        "-segment_list", str(list_path), "-segment_list_type", "csv",
        "-segment_list_flags", "+live",
        str(incoming / FFMPEG_PATTERN),
    ]
    return cmd


def collect_completed(incoming: Path, cam: str, root: Path, store: str, running: bool) -> list[Segment]:
    """Move every segment ffmpeg has finished into its day directory.

    Finished = listed in a segments-*.csv. Files nobody listed (ffmpeg was
    killed) count as finished once no ffmpeg runs; their duration comes from
    the next segment's name or, for the last one, from the file mtime.
    """
    listed: dict[str, float] = {}
    lists = sorted(incoming.glob(LIST_PREFIX + "*.csv"))
    for lp in lists:
        try:
            for name, start, end in parse_segment_list(lp.read_text()):
                listed[name] = end - start
        except FileNotFoundError:
            continue

    files = sorted(p for p in incoming.glob("*.ts"))
    done: list[Segment] = []
    for i, f in enumerate(files):
        dur = listed.get(f.name)
        if dur is None:
            if running:
                continue
            nxt = files[i + 1] if i + 1 < len(files) else None
            try:
                dur = (parse_name(nxt.name) - parse_name(f.name)) if nxt else f.stat().st_mtime - parse_name(f.name)
            except ValueError:
                continue
            dur = max(dur, 0.1)
        try:
            start = parse_name(f.name)
        except ValueError:
            log.warning("unexpected file %s, ignored", f)
            continue
        target_dir = day_dir(root, cam, start)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f.name
        n = 1
        while target.exists():  # same-second restart: never overwrite older footage
            target = target_dir / f"{f.stem}-{n}.ts"
            n += 1
        size = f.stat().st_size
        os.replace(f, target)
        done.append(Segment(cam=cam, path=str(target), start=start, end=start + dur, bytes=size, store=store))

    if not running:
        for lp in lists:
            lp.unlink(missing_ok=True)
    return done


class Recorder:
    """Keeps one ffmpeg alive for a camera; reports finished segments."""

    def __init__(self, cam: str, src: str, segment_seconds: int, audio: bool,
                 on_segment: Callable[[Segment], None], on_state: Callable[[str, str], None]):
        self.cam = cam
        self.src = src
        self.segment_seconds = segment_seconds
        self.audio = audio
        self.on_segment = on_segment
        self.on_state = on_state
        self.root: Path | None = None
        self.store = "primary"
        self.suppressed = False
        self.proc: asyncio.subprocess.Process | None = None
        self._wake = asyncio.Event()
        self.last_segment_at: float | None = None

    def set_root(self, root: Path, store: str) -> None:
        if root != self.root:
            self.root, self.store = root, store
            self._restart()

    def set_suppressed(self, on: bool) -> None:
        if on != self.suppressed:
            self.suppressed = on
            self._restart()

    def _restart(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
        self._wake.set()

    async def run(self) -> None:
        backoff = BACKOFF_START
        await asyncio.sleep(STARTUP_DELAY)  # go2rtc needs a moment to listen
        while True:
            self._wake.clear()
            if self.suppressed or self.root is None:
                self.on_state(self.cam, "suppressed" if self.suppressed else "waiting")
                await self._wake.wait()
                continue
            root, store = self.root, self.store
            inc = incoming_dir(root, self.cam)
            inc.mkdir(parents=True, exist_ok=True)
            self._collect(inc, root, store, running=False)  # leftovers from last run
            list_path = inc / f"{LIST_PREFIX}{int(time.time())}.csv"
            cmd = ffmpeg_cmd(self.src, inc, list_path, self.segment_seconds, self.audio)
            started = time.monotonic()
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TZ": "UTC"})
            self.on_state(self.cam, "recording")
            stderr_task = asyncio.create_task(self._log_stderr(self.proc))
            while self.proc.returncode is None:
                try:
                    await asyncio.wait_for(self.proc.wait(), POLL)
                except asyncio.TimeoutError:
                    pass
                self._collect(inc, root, store, running=self.proc.returncode is None)
            await stderr_task
            self._collect(inc, root, store, running=False)
            if self.suppressed or self._wake.is_set():
                backoff = BACKOFF_START
                continue
            # Unexpected exit: back off. A run that lasted a while resets it.
            if time.monotonic() - started > 60:
                backoff = BACKOFF_START
            self.on_state(self.cam, "reconnecting")
            log.warning("%s: ffmpeg exit %s, retry in %.0fs", self.cam, self.proc.returncode, backoff)
            try:
                await asyncio.wait_for(self._wake.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _collect(self, inc: Path, root: Path, store: str, running: bool) -> None:
        for seg in collect_completed(inc, self.cam, root, store, running):
            self.last_segment_at = time.time()
            self.on_segment(seg)

    async def _log_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr
        async for line in proc.stderr:
            msg = line.decode(errors="replace").rstrip()
            if msg:
                log.info("%s ffmpeg: %s", self.cam, _redact(msg))

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()


def _redact(msg: str) -> str:
    """ffmpeg prints URLs incl. credentials on errors."""
    import re
    return re.sub(r"(rtsps?://)[^@/\s]+@", r"\1***@", msg)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None
