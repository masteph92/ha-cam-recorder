"""Read side for the details view: events, recorded coverage, HLS playlists
and clips built from the segments on disk. No transcoding anywhere: the
segments already are MPEG-TS, which is what HLS plays."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .state import StateDB

GAP = 15.0  # seconds; closer segments count as one continuous recording
SEG_NAME = re.compile(r"^\d{8}T\d{6}Z(-\d+)?\.ts$")


@dataclass(frozen=True)
class Span:
    start: float
    end: float


def merge(spans: list[tuple[float, float]], gap: float = GAP) -> list[Span]:
    out: list[Span] = []
    for s, e in sorted(spans):
        if out and s - out[-1].end <= gap:
            out[-1] = Span(out[-1].start, max(out[-1].end, e))
        else:
            out.append(Span(s, e))
    return out


class Archive:
    def __init__(self, db: StateDB, pre_seconds: float, snapshots: Path):
        self.db = db
        self.pre = pre_seconds
        self.snapshots = snapshots

    def events(self, start: float, end: float, cam: str | None = None) -> list[dict]:
        q = "SELECT id, cam, start, end, reason FROM events WHERE start < ? AND (end IS NULL OR end > ?)"
        args: list = [end, start]
        if cam:
            q += " AND cam=?"
            args.append(cam)
        rows = self.db.db.execute(q + " ORDER BY start DESC", args).fetchall()
        return [{"id": i, "cam": c, "start": s, "end": e, "reason": r,
                 "snapshot": (self.snapshots / f"{i}.jpg").is_file()} for i, c, s, e, r in rows]

    def coverage(self, cam: str, start: float, end: float) -> list[Span]:
        segs = self.db.segments_overlapping(cam, start, end)
        return merge([(max(s.start, start), min(s.end, end)) for s in segs])

    def segments(self, cam: str, start: float, end: float):
        return self.db.segments_overlapping(cam, start, end)

    def segment_path(self, cam: str, name: str) -> Path | None:
        """Only files the DB knows - never a path taken from the request."""
        if not SEG_NAME.match(name):
            return None
        row = self.db.db.execute(
            "SELECT path FROM segments WHERE cam=? AND path LIKE ?", (cam, f"%/{name}")).fetchone()
        if not row:
            return None
        p = Path(row[0])
        return p if p.is_file() else None

    def event(self, event_id: str) -> dict | None:
        r = self.db.db.execute("SELECT id, cam, start, end, reason FROM events WHERE id=?", (event_id,)).fetchone()
        return None if not r else {"id": r[0], "cam": r[1], "start": r[2], "end": r[3], "reason": r[4]}

    def event_window(self, ev: dict, now: float) -> tuple[float, float]:
        end = ev["end"] if ev["end"] is not None else now
        return ev["start"] - self.pre, end

    def snapshot_path(self, event_id: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{6,32}", event_id):
            return None
        p = self.snapshots / f"{event_id}.jpg"
        return p if p.is_file() else None


def hls_playlist(segs, url_for) -> str:
    """VOD playlist over existing segments. Every segment restarts its
    timestamps (ffmpeg -reset_timestamps 1), hence a discontinuity before each."""
    if not segs:
        return "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-ENDLIST\n"
    target = max(1, int(max(s.duration for s in segs) + 0.999))
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    for i, s in enumerate(segs):
        if i:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{s.duration:.3f},")
        lines.append(url_for(s))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"
