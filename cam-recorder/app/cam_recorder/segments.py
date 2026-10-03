"""Segment naming, ffmpeg segment-list parsing and on-disk layout.

ffmpeg writes into <root>/<cam>/incoming/ with UTC names like
20261003T201530Z.ts. A segment is complete once ffmpeg lists it in the CSV
segment list; only then it is moved to <root>/<cam>/<YYYY-MM-DD>/.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

NAME_FMT = "%Y%m%dT%H%M%SZ"
FFMPEG_PATTERN = NAME_FMT + ".ts"  # ffmpeg -strftime 1 pattern
INCOMING = "incoming"


@dataclass(frozen=True)
class Segment:
    cam: str
    path: str
    start: float  # epoch seconds, UTC
    end: float
    bytes: int
    store: str = "primary"  # primary | fallback

    @property
    def duration(self) -> float:
        return self.end - self.start


def parse_name(name: str) -> float:
    """'20261003T201530Z.ts' -> epoch seconds."""
    stem = Path(name).name.removesuffix(".ts").split("-")[0]  # "-1" collision suffix
    return datetime.strptime(stem, NAME_FMT).replace(tzinfo=timezone.utc).timestamp()


def day_dir(root: str | Path, cam: str, start: float) -> Path:
    day = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d")
    return Path(root) / cam / day


def incoming_dir(root: str | Path, cam: str) -> Path:
    return Path(root) / cam / INCOMING


def parse_segment_list(text: str) -> list[tuple[str, float, float]]:
    """ffmpeg -segment_list_type csv: 'name,start,end' per completed segment."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        try:
            out.append((row[0], float(row[1]), float(row[2])))
        except ValueError:
            continue
    return out


def overlaps(seg_start: float, seg_end: float, t0: float, t1: float | None) -> bool:
    """Half-open overlap; t1=None means open-ended."""
    return seg_end > t0 and (t1 is None or seg_start < t1)


def window(event_start: float, event_end: float | None, pre: float) -> tuple[float, float | None]:
    return event_start - pre, event_end
