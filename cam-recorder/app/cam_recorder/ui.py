"""Live state for the web UI: what each camera is doing right now.

Pure and synchronous; the web layer turns change notifications into
Server-Sent Events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Camera, Config


def default_label(name: str) -> str:
    return name.replace("_", " ").title()


@dataclass
class CamUi:
    cam: Camera
    motion_since: float | None = None
    privacy_on: bool = False  # from the camera's suppress entity in HA
    paused: bool = False  # add-on internal pause (cameras without suppress entity)
    door: str | None = None  # open | closed
    signal: float | None = None
    lost: bool = False  # signal entity unavailable
    recording: str = "starting"

    @property
    def off(self) -> bool:
        return self.privacy_on or self.paused

    def as_dict(self) -> dict:
        c = self.cam
        return {
            "id": c.name,
            "label": c.label or default_label(c.name),
            "privacy_capable": bool(c.suppress),
            "off": self.off,
            "off_reason": "privacy" if self.privacy_on else ("pause" if self.paused else None),
            "motion_since": None if self.off else self.motion_since,
            "door": self.door,
            "signal": self.signal,
            "lost": self.lost,
            "wired": c.wired,
            "recording": self.recording,
            "has_sub": bool(c.rtsp_sub),
        }


@dataclass
class UiState:
    cfg: Config
    cams: dict[str, CamUi] = field(init=False)
    _listeners: set[asyncio.Queue] = field(default_factory=set, init=False)
    on_change: Callable[[], None] = lambda: None

    def __post_init__(self) -> None:
        self.cams = {c.name: CamUi(c) for c in self.cfg.cameras}

    def snapshot(self, now: float) -> dict:
        return {
            "title": self.cfg.title or self.cfg.site,
            "site": self.cfg.site,
            "now": now,
            "rotate_seconds": self.cfg.ui_rotate_seconds,
            "pin_minutes": self.cfg.ui_pin_minutes,
            "history": self.cfg.recording,
            "cams": [c.as_dict() for c in self.cams.values()],
        }

    # ---- updates (each notifies listeners only on a real change)
    def _set(self, cam: str, attr: str, value) -> None:
        cu = self.cams.get(cam)
        if cu is None or getattr(cu, attr) == value:
            return
        setattr(cu, attr, value)
        self._changed()

    def motion(self, cam: str, since: float | None) -> None:
        self._set(cam, "motion_since", since)

    def privacy(self, cam: str, on: bool) -> None:
        self._set(cam, "privacy_on", on)

    def pause(self, cam: str, on: bool) -> None:
        self._set(cam, "paused", on)

    def door(self, cam: str, state: str) -> None:
        self._set(cam, "door", "open" if state.lower() in {"on", "open"} else "closed")

    def signal(self, cam: str, state: str) -> None:
        try:
            value, lost = float(state), False
        except ValueError:
            value, lost = None, state.lower() in {"unavailable", "unknown"}
        cu = self.cams.get(cam)
        if cu is None or (cu.signal == value and cu.lost == lost):
            return
        cu.signal, cu.lost = value, lost
        self._changed()

    def recording(self, cam: str, state: str) -> None:
        self._set(cam, "recording", state)

    # ---- listeners
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _changed(self) -> None:
        for q in list(self._listeners):
            if q.empty():
                q.put_nowait(True)  # coalesce: one pending wake-up is enough
        self.on_change()
