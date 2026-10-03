"""Which segments to delete. Pure functions; the caller reads and deletes.

Primary store: own usage above the limit -> delete down to 90 %, quiet
segments oldest first, event segments only when nothing quiet is left.
Fallback (tmpfs): keep only the last few minutes.
Never delete anything still waiting for upload.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .segments import Segment, overlaps

TARGET_RATIO = 0.9


def _in_event(seg: Segment, windows: Sequence[tuple[float, float | None]]) -> bool:
    return any(overlaps(seg.start, seg.end, t0, t1) for t0, t1 in windows)


def plan_primary(
    segments: Iterable[Segment],
    windows: Sequence[tuple[float, float | None]],
    pending: set[str],
    max_bytes: int,
) -> list[Segment]:
    segs = sorted(segments, key=lambda s: s.start)
    used = sum(s.bytes for s in segs)
    if used <= max_bytes:
        return []
    target = int(max_bytes * TARGET_RATIO)
    deletable = [s for s in segs if s.path not in pending]
    quiet = [s for s in deletable if not _in_event(s, windows)]
    event = [s for s in deletable if _in_event(s, windows)]
    out: list[Segment] = []
    for s in quiet + event:
        if used <= target:
            break
        out.append(s)
        used -= s.bytes
    return out


def plan_fallback(segments: Iterable[Segment], pending: set[str], now: float, keep_seconds: float) -> list[Segment]:
    return [s for s in segments if s.end < now - keep_seconds and s.path not in pending]
