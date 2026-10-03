"""Event window state machine, one per camera. Pure: time is passed in.

Triggers may be pulses (Tapo ONVIF: on and off within a second) or levels
(a door contact staying on). Both reduce to "last time any trigger was on".

    IDLE    --trigger on-------------> ACTIVE   start = t, end = t + post
    ACTIVE  --trigger on / still on--> ACTIVE   end = t + post
    ACTIVE  --t >= end---------------> IDLE     close "quiet"
    ACTIVE  --t >= start + max-------> CAPPED   close "cap"
    CAPPED  --quiet for post---------> IDLE     only then a new event can open
    any     --suppress on------------> SUPPRESSED, open event closes "suppressed"

CAPPED keeps a door left open or a flapping sensor from uploading for hours.
The upload window of an event is [start - pre, end].
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field


class State(enum.Enum):
    IDLE = "idle"
    ACTIVE = "active"
    CAPPED = "capped"
    SUPPRESSED = "suppressed"


@dataclass
class Event:
    cam: str
    start: float
    end: float  # provisional while open
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    reason: str | None = None  # set on close: quiet | cap | suppressed

    @property
    def closed(self) -> bool:
        return self.reason is not None


@dataclass(frozen=True)
class Opened:
    event: Event


@dataclass(frozen=True)
class Closed:
    event: Event


Action = Opened | Closed


class EventTracker:
    def __init__(self, cam: str, post_seconds: float, max_seconds: float):
        self.cam = cam
        self.post = post_seconds
        self.max = max_seconds
        self.state = State.IDLE
        self.event: Event | None = None
        self._on: set[str] = set()
        self._last_active: float | None = None

    @property
    def triggered(self) -> bool:
        return bool(self._on)

    def trigger(self, entity: str, on: bool, t: float) -> list[Action]:
        rising = on and entity not in self._on
        if on:
            self._on.add(entity)
        else:
            self._on.discard(entity)
        if self.state is State.SUPPRESSED:
            return []
        if on:
            self._last_active = t
        actions = self.tick(t)
        if rising and self.state is State.IDLE:
            self.event = Event(cam=self.cam, start=t, end=t + self.post)
            self.state = State.ACTIVE
            actions.append(Opened(self.event))
        return actions

    def suppress(self, on: bool, t: float) -> list[Action]:
        actions: list[Action] = []
        if on and self.state is not State.SUPPRESSED:
            if self.state is State.ACTIVE:
                actions.append(self._close(min(t, self.event.end), "suppressed"))
            self.state = State.SUPPRESSED
        elif not on and self.state is State.SUPPRESSED:
            # Triggers that stayed on through the privacy phase do not count
            # as a fresh event; the next rising edge does.
            self.state = State.CAPPED if self._on else State.IDLE
            self._last_active = t if self._on else None
        return actions

    def tick(self, t: float) -> list[Action]:
        if self.state is State.SUPPRESSED:
            return []
        if self._on:
            self._last_active = t
        if self.state is State.ACTIVE:
            ev = self.event
            if self._last_active is not None:
                ev.end = max(ev.end, self._last_active + self.post)
            cap_at = ev.start + self.max
            if t >= cap_at and ev.end > cap_at:
                self.state = State.CAPPED
                return [self._close(cap_at, "cap")]
            if t >= ev.end:
                self.state = State.IDLE
                return [self._close(ev.end, "quiet")]
        elif self.state is State.CAPPED:
            if self._last_active is None or t - self._last_active >= self.post:
                self.state = State.IDLE
        return []

    def _close(self, end: float, reason: str) -> Closed:
        ev = self.event
        ev.end = end
        ev.reason = reason
        self.event = None
        return Closed(ev)
