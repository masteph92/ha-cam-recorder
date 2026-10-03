"""Uploads to B2 via rclone. Queue lives in SQLite, so a restart loses nothing.

The B2 application key has no deleteFiles: whoever takes the box cannot wipe
what is already offsite. Retention is the bucket lifecycle rule, not us.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from .config import Config
from .state import Job, StateDB

log = logging.getLogger(__name__)

WORKERS = 2
IDLE_POLL = 3.0


def remote_dst(remote: str, cam: str, day: str, event_id: str, name: str) -> str:
    return f"{remote.rstrip('/')}/{cam}/{day}/{event_id}/{name}"


def rclone_env(cfg: Config) -> dict[str, str]:
    """rclone remote 'b2:' from env, no config file with the key on disk."""
    return {
        **os.environ,
        "RCLONE_CONFIG_B2_TYPE": "b2",
        "RCLONE_CONFIG_B2_ACCOUNT": cfg.b2_account,
        "RCLONE_CONFIG_B2_KEY": cfg.b2_key,
        "RCLONE_CONFIG_B2_HARD_DELETE": "false",
    }


def rclone_cmd(src: str, dst: str) -> list[str]:
    # --no-check-dest: no listing/HEAD needed, the key may lack listFiles.
    return ["rclone", "copyto", "--no-check-dest", "--retries", "1", "--low-level-retries", "3", src, dst]


class Uploader:
    def __init__(self, cfg: Config, db: StateDB):
        self.cfg = cfg
        self.db = db
        self.env = rclone_env(cfg)
        self._wake = asyncio.Event()
        self._busy: set[int] = set()
        self.last_ok: float | None = None
        self.last_error: str | None = None

    def kick(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        if not self.cfg.upload_enabled:
            log.info("upload disabled")
            return
        await asyncio.gather(*(self._worker(i) for i in range(WORKERS)))

    async def _worker(self, n: int) -> None:
        while True:
            jobs = [j for j in self.db.due_jobs(time.time(), limit=WORKERS * 2) if j.id not in self._busy]
            if not jobs:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), IDLE_POLL)
                except asyncio.TimeoutError:
                    pass
                continue
            job = jobs[0]
            self._busy.add(job.id)
            try:
                await self._upload(job)
            finally:
                self._busy.discard(job.id)

    async def _upload(self, job: Job) -> None:
        if not Path(job.src).exists():
            self.db.job_gone(job.id, "source missing")
            log.warning("upload source gone: %s", job.src)
            return
        proc = await asyncio.create_subprocess_exec(
            *rclone_cmd(job.src, job.dst), env=self.env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode == 0:
            self.db.job_done(job.id)
            self.last_ok = time.time()
        else:
            msg = err.decode(errors="replace").strip().splitlines()[-1:] or ["?"]
            self.last_error = msg[0]
            self.db.job_failed(job.id, time.time(), msg[0])
            log.warning("upload failed (%s): %s", job.src, msg[0])
