"""Persistent state in SQLite on /data (local HA disk, never on the share:
SQLite locking on NFS/SMB is unreliable).

segments  every completed segment and where it lives
events    every event; end is NULL while open
queue     pending/finished uploads, survives restarts
budget    bytes queued for upload per camera and UTC day
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .events import Event
from .segments import Segment

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
  path TEXT PRIMARY KEY, cam TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
  bytes INTEGER NOT NULL, store TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS segments_cam_start ON segments(cam, start);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, cam TEXT NOT NULL, start REAL NOT NULL, end REAL, reason TEXT);
CREATE INDEX IF NOT EXISTS events_cam_start ON events(cam, start);
CREATE TABLE IF NOT EXISTS queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cam TEXT NOT NULL, event_id TEXT NOT NULL,
  src TEXT NOT NULL, dst TEXT NOT NULL, bytes INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0, error TEXT,
  UNIQUE(event_id, src));
CREATE TABLE IF NOT EXISTS budget (
  cam TEXT NOT NULL, day TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(cam, day));
"""


@dataclass(frozen=True)
class Job:
    id: int
    cam: str
    event_id: str
    src: str
    dst: str
    bytes: int
    attempts: int


def utc_day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


class StateDB:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # --- segments
    def add_segment(self, s: Segment) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO segments VALUES (?,?,?,?,?,?)",
            (s.path, s.cam, s.start, s.end, s.bytes, s.store))

    def remove_segment(self, path: str) -> None:
        self.db.execute("DELETE FROM segments WHERE path=?", (path,))

    def segments(self, store: str | None = None, cam: str | None = None) -> list[Segment]:
        q, args = "SELECT cam, path, start, end, bytes, store FROM segments WHERE 1=1", []
        if store:
            q += " AND store=?"
            args.append(store)
        if cam:
            q += " AND cam=?"
            args.append(cam)
        return [Segment(*r) for r in self.db.execute(q + " ORDER BY start", args)]

    def segments_overlapping(self, cam: str, t0: float, t1: float | None) -> list[Segment]:
        t1 = float("inf") if t1 is None else t1
        rows = self.db.execute(
            "SELECT cam, path, start, end, bytes, store FROM segments "
            "WHERE cam=? AND end>? AND start<? ORDER BY start", (cam, t0, t1))
        return [Segment(*r) for r in rows]

    def latest_segment(self, cam: str) -> Segment | None:
        r = self.db.execute(
            "SELECT cam, path, start, end, bytes, store FROM segments WHERE cam=? "
            "ORDER BY start DESC LIMIT 1", (cam,)).fetchone()
        return Segment(*r) if r else None

    def used_bytes(self, store: str) -> int:
        return self.db.execute(
            "SELECT COALESCE(SUM(bytes),0) FROM segments WHERE store=?", (store,)).fetchone()[0]

    # --- events
    def save_event(self, ev: Event) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?)",
            (ev.id, ev.cam, ev.start, ev.end if ev.closed else None, ev.reason))

    def close_dangling_events(self, now: float) -> int:
        """After a restart nothing is open any more."""
        return self.db.execute(
            "UPDATE events SET end=?, reason='restart' WHERE end IS NULL", (now,)).rowcount

    def event_windows(self, pre: float) -> list[tuple[float, float | None]]:
        return [(s - pre, e) for s, e in self.db.execute("SELECT start, end FROM events")]

    def events_overlapping(self, cam: str, seg_start: float, seg_end: float, pre: float) -> list[str]:
        rows = self.db.execute(
            "SELECT id FROM events WHERE cam=? AND start-?<? AND (end IS NULL OR end>?)",
            (cam, pre, seg_end, seg_start))
        return [r[0] for r in rows]

    # --- upload queue
    def enqueue(self, cam: str, event_id: str, src: str, dst: str, size: int) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO queue (cam, event_id, src, dst, bytes) VALUES (?,?,?,?,?)",
            (cam, event_id, src, dst, size))
        return cur.rowcount == 1

    def due_jobs(self, now: float, limit: int = 10) -> list[Job]:
        rows = self.db.execute(
            "SELECT id, cam, event_id, src, dst, bytes, attempts FROM queue "
            "WHERE done=0 AND next_try<=? ORDER BY id LIMIT ?", (now, limit))
        return [Job(*r) for r in rows]

    def job_done(self, job_id: int) -> None:
        self.db.execute("UPDATE queue SET done=1, error=NULL WHERE id=?", (job_id,))

    def job_failed(self, job_id: int, now: float, error: str) -> None:
        attempts = self.db.execute("SELECT attempts FROM queue WHERE id=?", (job_id,)).fetchone()[0] + 1
        delay = min(10 * 2 ** (attempts - 1), 1800)
        self.db.execute(
            "UPDATE queue SET attempts=?, next_try=?, error=? WHERE id=?",
            (attempts, now + delay, error[-500:], job_id))

    def job_gone(self, job_id: int, reason: str) -> None:
        """Source vanished; nothing left to retry."""
        self.db.execute("UPDATE queue SET done=2, error=? WHERE id=?", (reason, job_id))

    def pending_paths(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT src FROM queue WHERE done=0")}

    def pending_count(self, cam: str | None = None) -> int:
        if cam:
            return self.db.execute("SELECT COUNT(*) FROM queue WHERE done=0 AND cam=?", (cam,)).fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM queue WHERE done=0").fetchone()[0]

    def prune_queue(self, older_than_id_keep: int = 5000) -> None:
        self.db.execute(
            "DELETE FROM queue WHERE done>0 AND id < (SELECT COALESCE(MAX(id),0) FROM queue) - ?",
            (older_than_id_keep,))

    # --- budget
    def budget_used(self, cam: str, t: float) -> int:
        r = self.db.execute("SELECT bytes FROM budget WHERE cam=? AND day=?", (cam, utc_day(t))).fetchone()
        return r[0] if r else 0

    def budget_add(self, cam: str, t: float, size: int) -> None:
        self.db.execute(
            "INSERT INTO budget VALUES (?,?,?) ON CONFLICT(cam, day) DO UPDATE SET bytes=bytes+excluded.bytes",
            (cam, utc_day(t), size))

    def close(self) -> None:
        self.db.close()


def now() -> float:
    return time.time()


def iter_chunks(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]
