"""Wiring: HA states -> trackers -> events -> upload queue; storage, cleanup,
health. The sync methods are the logic and are tested without a running
loop; run() adds the long-running tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from . import cleaner, go2rtc, snapshot, storage, web
from .config import Camera, Config
from .events import Closed, Event, EventTracker, Opened
from .recorder import Recorder
from .segments import Segment, window
from .state import StateDB, utc_day
from .archive import Archive
from .ui import UiState
from .uploader import Uploader, remote_dst

log = logging.getLogger(__name__)

ON_STATES = {"on", "open", "detected", "true"}
TICK = 1.0
STORAGE_CHECK = 30.0
CLEAN_EVERY = 300.0
HEALTH_EVERY = 60.0
SNAPSHOT_KEEP_DAYS = 14


class App:
    def __init__(self, cfg: Config, db: StateDB, data_dir: Path, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.db = db
        self.data = data_dir
        self.clock = clock
        self.cams: dict[str, Camera] = {c.name: c for c in cfg.cameras}
        self.trackers = {c.name: EventTracker(c.name, cfg.post_seconds, cfg.max_seconds) for c in cfg.cameras}
        self.routes: dict[str, list[tuple[str, str]]] = {}
        for c in cfg.cameras:
            for t in c.triggers:
                self.routes.setdefault(t, []).append((c.name, "trigger"))
            if c.suppress:
                self.routes.setdefault(c.suppress, []).append((c.name, "suppress"))
            if c.door_entity:
                self.routes.setdefault(c.door_entity, []).append((c.name, "door"))
            if c.signal_entity:
                self.routes.setdefault(c.signal_entity, []).append((c.name, "signal"))
        self.recorders: dict[str, Recorder] = {}
        self.cam_state: dict[str, str] = {c.name: "starting" for c in cfg.cameras}
        self.over_budget: set[str] = set()
        self.store = storage.Store("primary", Path(cfg.primary))
        self.notify: Callable[[str, dict], None] = lambda kind, data: None
        self.on_opened: Callable[[Event], None] = lambda ev: None
        self.uploader: Uploader | None = None
        # event.json waits until the segment containing the end is finished
        self.meta_due: list[tuple[float, Event]] = []
        self.ui = UiState(cfg)
        self.ha = None

    # ------------------------------------------------------------ HA input
    def on_ha_state(self, entity: str, state: str) -> None:
        on = state.lower() in ON_STATES
        t = self.clock()
        for cam, kind in self.routes.get(entity, []):
            tr = self.trackers[cam]
            if kind == "trigger":
                self._apply(tr.trigger(entity, on, t))
            elif kind == "suppress":
                self._apply(tr.suppress(on, t))
                self.ui.privacy(cam, on)
                rec = self.recorders.get(cam)
                if rec:
                    rec.set_suppressed(on)
            elif kind == "door":
                self.ui.door(cam, state)
            elif kind == "signal":
                self.ui.signal(cam, state)

    def tick(self) -> None:
        t = self.clock()
        for tr in self.trackers.values():
            self._apply(tr.tick(t))
        due = [ev for at, ev in self.meta_due if at <= t]
        if due:
            self.meta_due = [(at, ev) for at, ev in self.meta_due if at > t]
            for ev in due:
                self._enqueue_meta(ev)

    # ------------------------------------------------------------ events
    def _apply(self, actions) -> None:
        for a in actions:
            ev = a.event
            self.db.save_event(ev)
            if isinstance(a, Opened):
                log.info("%s: event %s opened", ev.cam, ev.id)
                t0, _ = window(ev.start, None, self.cfg.pre_seconds)
                for seg in self.db.segments_overlapping(ev.cam, t0, None):
                    self._enqueue_segment(ev, seg)
                self.ui.motion(ev.cam, ev.start)
                self.on_opened(ev)
                self.notify("cam_recorder_event_start", {"camera": ev.cam, "event_id": ev.id, "start": ev.start})
            elif isinstance(a, Closed):
                log.info("%s: event %s closed (%s, %.0fs)", ev.cam, ev.id, ev.reason, ev.end - ev.start)
                self.ui.motion(ev.cam, None)
                self.meta_due.append((ev.end + 2 * self.cfg.segment_seconds, ev))
                self.notify("cam_recorder_event_end", {
                    "camera": ev.cam, "event_id": ev.id, "start": ev.start, "end": ev.end, "reason": ev.reason})

    def on_segment(self, seg: Segment) -> None:
        self.db.add_segment(seg)
        for event_id in self.db.events_overlapping(seg.cam, seg.start, seg.end, self.cfg.pre_seconds):
            ev = self._event(event_id)
            if ev:
                self._enqueue_segment(ev, seg)

    def _event(self, event_id: str) -> Event | None:
        r = self.db.db.execute("SELECT cam, start, end, id, reason FROM events WHERE id=?", (event_id,)).fetchone()
        if not r:
            return None
        cam, start, end, eid, reason = r
        return Event(cam=cam, start=start, end=end if end is not None else start, id=eid, reason=reason)

    # ------------------------------------------------------------ on / off
    def pause(self, cam: str, on: bool) -> None:
        """Add-on internal pause for cameras without a privacy entity in HA:
        no recording, no events. Survives restarts."""
        if cam not in self.cams:
            return
        self._apply(self.trackers[cam].suppress(on, self.clock()))
        rec = self.recorders.get(cam)
        if rec:
            rec.set_suppressed(on)
        self.db.set_setting(f"paused:{cam}", "1" if on else "0")
        self.ui.pause(cam, on)

    async def set_off(self, cam: str, off: bool) -> bool:
        c = self.cams.get(cam)
        if c is None:
            return False
        if c.suppress:
            # privacy switch in HA; its new state comes back over the websocket
            return bool(self.ha) and await self.ha.call_service(c.suppress, off)
        self.pause(cam, off)
        return True

    async def set_all_off(self, off: bool) -> None:
        for cam in self.cams:
            await self.set_off(cam, off)

    def restore_pauses(self) -> None:
        for cam, c in self.cams.items():
            if not c.suppress and self.db.get_setting(f"paused:{cam}") == "1":
                self.pause(cam, True)

    # ------------------------------------------------------------ upload queue
    def _dst(self, ev: Event, name: str) -> str:
        return remote_dst(self.cfg.remote, ev.cam, utc_day(ev.start), ev.id, name)

    def enqueue(self, ev: Event, src: str, name: str, size: int, *, budgeted: bool = True) -> bool:
        if not self.cfg.upload_enabled:
            return False
        t = self.clock()
        if budgeted and self.db.budget_used(ev.cam, t) + size > self.cfg.daily_budget_bytes:
            if ev.cam not in self.over_budget:
                log.warning("%s: daily upload budget reached, segments stay local", ev.cam)
                self.over_budget.add(ev.cam)
            return False
        if self.db.enqueue(ev.cam, ev.id, src, self._dst(ev, name), size):
            if budgeted:
                self.db.budget_add(ev.cam, t, size)
            if self.uploader:
                self.uploader.kick()
            return True
        return False

    def _enqueue_segment(self, ev: Event, seg: Segment) -> None:
        self.enqueue(ev, seg.path, Path(seg.path).name, seg.bytes)

    def _enqueue_meta(self, ev: Event) -> None:
        p = self.data / "events" / f"{ev.id}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        segs = self.db.segments_overlapping(ev.cam, ev.start - self.cfg.pre_seconds, ev.end)
        p.write_text(json.dumps({
            "site": self.cfg.site, "camera": ev.cam, "event_id": ev.id,
            "start": ev.start, "end": ev.end, "reason": ev.reason,
            "segments": [Path(s.path).name for s in segs],
        }, indent=2))
        self.enqueue(ev, str(p), "event.json", p.stat().st_size, budgeted=False)

    async def take_snapshot(self, ev: Event) -> None:
        out = self.data / "snapshots" / f"{ev.id}.jpg"
        latest = self.db.latest_segment(ev.cam)
        if await snapshot.take(out, latest.path if latest else None, self.cams[ev.cam].snapshot_url):
            self.enqueue(ev, str(out), "snapshot.jpg", out.stat().st_size, budgeted=False)
            self.notify("cam_recorder_snapshot", {"camera": ev.cam, "event_id": ev.id, "path": str(out)})

    # ------------------------------------------------------------ storage
    def check_storage(self) -> storage.Store:
        st = storage.select(self.cfg.primary, self.cfg.fallback, self.cfg.create_marker)
        if st.kind != self.store.kind:
            log.warning("storage: %s -> %s (%s)", self.store.kind, st.kind, st.root)
        self.store = st
        for rec in self.recorders.values():
            rec.set_root(st.root, st.kind)
        return st

    def clean(self) -> int:
        pending = self.db.pending_paths()
        doomed = cleaner.plan_primary(
            self.db.segments("primary"), self.db.event_windows(self.cfg.pre_seconds),
            pending, self.cfg.primary_max_bytes)
        doomed += cleaner.plan_fallback(
            self.db.segments("fallback"), pending, self.clock(), self.cfg.fallback_keep_seconds)
        for s in doomed:
            Path(s.path).unlink(missing_ok=True)
            self.db.remove_segment(s.path)
        self._prune_dir(self.data / "snapshots", SNAPSHOT_KEEP_DAYS)
        self._prune_dir(self.data / "events", SNAPSHOT_KEEP_DAYS)
        self.db.prune_queue()
        if doomed:
            log.info("cleanup: %d segments, %.1f MB", len(doomed), sum(s.bytes for s in doomed) / 1e6)
        return len(doomed)

    def _prune_dir(self, d: Path, days: int) -> None:
        if not d.is_dir():
            return
        limit = self.clock() - days * 86400
        pending = self.db.pending_paths()
        for f in d.iterdir():
            if f.stat().st_mtime < limit and str(f) not in pending:
                f.unlink(missing_ok=True)

    def storage_stats(self) -> dict:
        return {
            "store": self.store.kind,
            "used_bytes": self.db.used_bytes(self.store.kind),
            "max_bytes": self.cfg.primary_max_bytes if self.store.kind == "primary" else None,
            "queue": self.db.pending_count(),
            "upload": self.cfg.upload_enabled,
        }

    # ------------------------------------------------------------ health
    def health(self) -> dict[str, dict]:
        t = self.clock()
        out = {}
        for name in self.cams:
            rec = self.recorders.get(name)
            last = self.db.latest_segment(name)
            age = round(t - last.end) if last else None
            state = self.cam_state.get(name, "unknown")
            if state == "recording" and self.store.kind == "fallback":
                state = "degraded"
            out[name] = {
                "state": state,
                "store": self.store.kind,
                "last_segment_age_s": age,
                "queue": self.db.pending_count(name),
                "uploaded_today_mb": round(self.db.budget_used(name, t) / 1e6, 1),
                "over_budget": name in self.over_budget,
                "event_open": self.trackers[name].event is not None,
                "suppressed": bool(rec and rec.suppressed),
            }
        return out

    # ------------------------------------------------------------ main
    async def run(self) -> None:
        from .ha import HAClient  # aiohttp only needed at runtime

        loop = asyncio.get_running_loop()
        ha = None
        if os.environ.get("SUPERVISOR_TOKEN"):
            ha = HAClient(self.routes.keys(), lambda e, s: loop.call_soon(self.on_ha_state, e, s))
            self.ha = ha
            self.notify = lambda kind, data: loop.create_task(ha.fire(kind, data))
        else:
            # Recording must not depend on HA; without it there are just no events.
            log.error("SUPERVISOR_TOKEN missing: recording without Home Assistant (no triggers)")
        self.on_opened = lambda ev: loop.create_task(self.take_snapshot(ev))
        self.uploader = Uploader(self.cfg, self.db)

        closed = self.db.close_dangling_events(self.clock())
        if closed:
            log.info("closed %d events left open by a restart", closed)

        for c in self.cfg.cameras if self.cfg.recording else ():
            self.recorders[c.name] = Recorder(
                c.name, go2rtc.local_url(c.name), self.cfg.segment_seconds, self.cfg.audio,
                on_segment=self.on_segment, on_state=self._set_cam_state)
        if self.cfg.recording:
            self.check_storage()
        else:
            log.info("recording: false – live view only, nothing is stored")
            for cam in self.cams:
                self.ui.recording(cam, "live")
        self.restore_pauses()
        if not self.cfg.kiosk_key:
            log.info("no ui.kiosk_key: LAN port locked, UI only via HA sidebar")
        ui_app = web.make_app(
            snapshot=lambda: self.ui.snapshot(self.clock()),
            subscribe=self.ui.subscribe, unsubscribe=self.ui.unsubscribe,
            set_off=self.set_off, set_all_off=self.set_all_off,
            stream_names=go2rtc.stream_names(self.cfg), kiosk_key=self.cfg.kiosk_key,
            archive=Archive(self.db, self.cfg.pre_seconds, self.data / "snapshots") if self.cfg.recording else None,
            storage_stats=self.storage_stats)

        tasks = [
            web.serve(ui_app),
            go2rtc.run(self.cfg),
            *([ha.run()] if ha else []),
            self.uploader.run(),
            *(r.run() for r in self.recorders.values()),
            self._every(TICK, self.tick),
            *([self._every(STORAGE_CHECK, self.check_storage),
               self._every(CLEAN_EVERY, self.clean)] if self.cfg.recording else []),
            self._every(HEALTH_EVERY, lambda: loop.create_task(self._publish_health(ha)) if ha else None),
        ]
        await asyncio.gather(*tasks)

    def _set_cam_state(self, cam: str, state: str) -> None:
        self.cam_state[cam] = state
        self.ui.recording(cam, state)

    async def _publish_health(self, ha) -> None:
        for cam, h in self.health().items():
            await ha.set_sensor(f"sensor.cam_recorder_{cam}", h["state"],
                                {**h, "friendly_name": f"Aufnahme {cam}", "icon": "mdi:cctv"})

    @staticmethod
    async def _every(seconds: float, fn: Callable[[], object]) -> None:
        while True:
            try:
                fn()
            except Exception:  # noqa: BLE001 - one failed pass must not stop the loop
                log.exception("periodic task %s failed", getattr(fn, "__name__", fn))
            await asyncio.sleep(seconds)
