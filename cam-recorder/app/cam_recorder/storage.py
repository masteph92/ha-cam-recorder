"""Primary/fallback storage selection.

The primary root must contain a marker file. Without it the network share is
not mounted and the path is just an empty directory on the HA disk - writing
there would fill up Home Assistant. Then recording goes to the fallback
(tmpfs) until the marker is back.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MARKER = ".cam-recorder-root"
NETWORK_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4"}


def mount_fstype(path: str | Path, mounts: str | None = None) -> str | None:
    """Filesystem type of the mount that contains path (longest mount-point
    prefix in /proc/mounts). Stacked mounts on the same point - systemd
    automount lists autofs first, the real cifs mount after it - resolve to
    the last entry."""
    if mounts is None:
        try:
            mounts = Path("/proc/mounts").read_text()
        except OSError:
            return None
    p = os.path.normpath(str(path))
    best, fstype = "", None
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mp = parts[1].replace("\\040", " ")
        if (p == mp or p.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best):
            best, fstype = mp, parts[2]
    return fstype


def ensure_marker(root: str | Path, mounts: str | None = None) -> bool:
    """Create the marker, but only on a network mount - never on the HA disk."""
    p = Path(root)
    if mounts is None:
        try:
            os.listdir(p)  # triggers a systemd automount before we look
        except OSError:
            pass
    if (p / MARKER).is_file():
        return True
    fstype = mount_fstype(p, mounts)
    if fstype not in NETWORK_FS:
        log.warning("create_marker: %s is on %s, not a network mount - no marker", p, fstype)
        return False
    p.mkdir(parents=True, exist_ok=True)
    (p / MARKER).touch()
    log.info("created %s on %s mount", p / MARKER, fstype)
    return True


def primary_ok(root: str | Path) -> bool:
    p = Path(root)
    return (p / MARKER).is_file() and os.access(p, os.W_OK)


@dataclass(frozen=True)
class Store:
    kind: str  # primary | fallback
    root: Path


def select(primary: str | Path, fallback: str | Path, create_marker: bool = False) -> Store:
    if create_marker and not primary_ok(primary):
        try:
            ensure_marker(primary)
        except OSError as e:
            log.warning("create_marker: %s", e)
    if primary_ok(primary):
        return Store("primary", Path(primary))
    Path(fallback).mkdir(parents=True, exist_ok=True)
    return Store("fallback", Path(fallback))
