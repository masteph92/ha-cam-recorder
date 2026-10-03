"""Add-on options (/data/options.json) into typed config."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
# go2rtc sources; ffmpeg:virtual is the test pattern used for smoke tests
SOURCE_PREFIXES = ("rtsp://", "rtsps://", "ffmpeg:")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Camera:
    name: str
    rtsp: str
    triggers: tuple[str, ...]
    suppress: str | None = None
    snapshot_url: str | None = None
    label: str | None = None
    rtsp_sub: str | None = None  # substream for tablets; recording always uses rtsp
    door_entity: str | None = None
    signal_entity: str | None = None
    wired: bool = False


@dataclass(frozen=True)
class Config:
    site: str
    cameras: tuple[Camera, ...]
    # ffmpeg cuts only on keyframes; below the camera GOP (Tapo ~8 s) every
    # keyframe starts a segment, above it segments become 2 GOPs long.
    segment_seconds: int = 5
    # False: nur Livebild – keine Aufnahme, go2rtc holt Streams nur, solange
    # jemand zuschaut (spart Funkstrecke und 5G-Volumen)
    recording: bool = True
    audio: bool = False
    pre_seconds: int = 30
    post_seconds: int = 60
    max_seconds: int = 600
    primary: str = "/share/cam-recorder"
    primary_max_gb: float = 450
    fallback: str = "/tmp/cam-recorder"
    fallback_keep_seconds: int = 120
    create_marker: bool = False
    upload_enabled: bool = False
    remote: str = ""
    b2_account: str = ""
    b2_key: str = field(default="", repr=False)
    daily_budget_gb: float = 5
    title: str = ""
    kiosk_key: str = field(default="", repr=False)
    ui_rotate_seconds: int = 12
    ui_pin_minutes: int = 5

    @property
    def primary_max_bytes(self) -> int:
        return int(self.primary_max_gb * 1024**3)

    @property
    def daily_budget_bytes(self) -> int:
        return int(self.daily_budget_gb * 1024**3)


def _opt(d: dict, key: str, default):
    v = d.get(key, default)
    return default if v is None or v == "" else v


def parse(raw: dict) -> Config:
    site = _opt(raw, "site", "")
    if not NAME_RE.match(site):
        raise ConfigError(f"site '{site}': only a-z, 0-9, _")

    cams: list[Camera] = []
    for c in raw.get("cameras") or []:
        name = c.get("name", "")
        if not NAME_RE.match(name):
            raise ConfigError(f"camera name '{name}': only a-z, 0-9, _")
        if not str(c.get("rtsp", "")).startswith(SOURCE_PREFIXES):
            raise ConfigError(f"camera {name}: rtsp must start with one of {', '.join(SOURCE_PREFIXES)}")
        triggers = tuple(t for t in (c.get("triggers") or []) if t)
        cams.append(Camera(
            name=name,
            rtsp=c["rtsp"],
            triggers=triggers,
            suppress=c.get("suppress") or None,
            snapshot_url=c.get("snapshot_url") or None,
            label=c.get("label") or None,
            rtsp_sub=c.get("rtsp_sub") or None,
            door_entity=c.get("door_entity") or None,
            signal_entity=c.get("signal_entity") or None,
            wired=bool(c.get("wired", False)),
        ))
        if cams[-1].rtsp_sub and not str(cams[-1].rtsp_sub).startswith(SOURCE_PREFIXES):
            raise ConfigError(f"camera {name}: rtsp_sub must start with one of {', '.join(SOURCE_PREFIXES)}")
    if len({c.name for c in cams}) != len(cams):
        raise ConfigError("camera names must be unique")

    ev = raw.get("event") or {}
    st = raw.get("storage") or {}
    up = raw.get("upload") or {}
    ui = raw.get("ui") or {}
    cfg = Config(
        site=site,
        cameras=tuple(cams),
        segment_seconds=int(_opt(raw, "segment_seconds", 5)),
        recording=bool(raw.get("recording", True)),
        audio=bool(_opt(raw, "audio", False)),
        pre_seconds=int(_opt(ev, "pre_seconds", 30)),
        post_seconds=int(_opt(ev, "post_seconds", 60)),
        max_seconds=int(_opt(ev, "max_seconds", 600)),
        primary=str(_opt(st, "primary", "/share/cam-recorder")),
        primary_max_gb=float(_opt(st, "primary_max_gb", 450)),
        fallback=str(_opt(st, "fallback", "/tmp/cam-recorder")),
        fallback_keep_seconds=int(_opt(st, "fallback_keep_seconds", 120)),
        create_marker=bool(_opt(st, "create_marker", False)),
        upload_enabled=bool(_opt(up, "enabled", False)),
        remote=str(_opt(up, "remote", "")),
        b2_account=str(_opt(up, "b2_account", "")),
        b2_key=str(_opt(up, "b2_key", "")),
        daily_budget_gb=float(_opt(up, "daily_budget_gb", 5)),
        title=str(_opt(ui, "title", "")),
        kiosk_key=str(_opt(ui, "kiosk_key", "")),
        ui_rotate_seconds=int(_opt(ui, "rotate_seconds", 12)),
        ui_pin_minutes=int(_opt(ui, "pin_minutes", 5)),
    )
    if cfg.kiosk_key and len(cfg.kiosk_key) < 12:
        raise ConfigError("ui.kiosk_key: at least 12 characters (it is the only lock on the LAN port)")
    if cfg.upload_enabled and not (cfg.remote and cfg.b2_account and cfg.b2_key):
        raise ConfigError("upload enabled but remote/b2_account/b2_key missing")
    if cfg.max_seconds <= cfg.post_seconds:
        raise ConfigError("event.max_seconds must be larger than post_seconds")
    return cfg


def load(path: str | Path = "/data/options.json") -> Config:
    return parse(json.loads(Path(path).read_text()))
