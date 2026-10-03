"""Primary/fallback storage selection.

The primary root must contain a marker file. Without it the network share is
not mounted and the path is just an empty directory on the HA disk - writing
there would fill up Home Assistant. Then recording goes to the fallback
(tmpfs) until the marker is back.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MARKER = ".cam-recorder-root"


def primary_ok(root: str | Path) -> bool:
    p = Path(root)
    return (p / MARKER).is_file() and os.access(p, os.W_OK)


@dataclass(frozen=True)
class Store:
    kind: str  # primary | fallback
    root: Path


def select(primary: str | Path, fallback: str | Path) -> Store:
    if primary_ok(primary):
        return Store("primary", Path(primary))
    Path(fallback).mkdir(parents=True, exist_ok=True)
    return Store("fallback", Path(fallback))
