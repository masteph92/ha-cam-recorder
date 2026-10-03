"""Home Assistant via the Supervisor proxy: WebSocket for state changes and
events, REST for health sensors."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Iterable

import aiohttp

log = logging.getLogger(__name__)

WS_URL = "ws://supervisor/core/websocket"
API_URL = "http://supervisor/core/api"


class HAClient:
    def __init__(self, entities: Iterable[str], on_state: Callable[[str, str], None]):
        self.token = os.environ["SUPERVISOR_TOKEN"]
        self.entities = set(entities)
        self.on_state = on_state
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._id = 0
        self.connected = asyncio.Event()

    def _next(self) -> int:
        self._id += 1
        return self._id

    async def run(self) -> None:
        backoff = 2.0
        self.session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"})
        while True:
            try:
                await self._session()
                backoff = 2.0
            except Exception as e:  # noqa: BLE001 - reconnect on anything
                log.warning("HA websocket: %s, reconnect in %.0fs", e, backoff)
            self.connected.clear()
            self.ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _session(self) -> None:
        async with self.session.ws_connect(WS_URL, heartbeat=30) as ws:
            msg = await ws.receive_json()
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"unexpected hello {msg}")
            await ws.send_json({"type": "auth", "access_token": self.token})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                raise RuntimeError("auth failed")
            self.ws = ws
            sub_id = self._next()
            await ws.send_json({"id": sub_id, "type": "subscribe_events", "event_type": "state_changed"})
            states_id = self._next()
            await ws.send_json({"id": states_id, "type": "get_states"})
            self.connected.set()
            log.info("HA websocket connected")
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                msg = raw.json()
                if msg.get("id") == states_id and msg.get("type") == "result":
                    for st in msg.get("result") or []:
                        if st["entity_id"] in self.entities:
                            self.on_state(st["entity_id"], st["state"])
                elif msg.get("type") == "event":
                    data = msg["event"].get("data") or {}
                    eid = data.get("entity_id")
                    new = data.get("new_state")
                    if eid in self.entities and new:
                        self.on_state(eid, new["state"])

    async def fire(self, event_type: str, data: dict) -> None:
        if not self.ws:
            return
        try:
            await self.ws.send_json({"id": self._next(), "type": "fire_event",
                                     "event_type": event_type, "event_data": data})
        except Exception as e:  # noqa: BLE001
            log.warning("fire_event %s: %s", event_type, e)

    async def call_service(self, entity_id: str, on: bool) -> bool:
        """turn_on / turn_off for switch, input_boolean, light ..."""
        if not self.session:
            return False
        domain = entity_id.split(".", 1)[0]
        service = "turn_on" if on else "turn_off"
        try:
            async with self.session.post(f"{API_URL}/services/{domain}/{service}",
                                         json={"entity_id": entity_id}) as r:
                if r.status >= 300:
                    log.warning("%s.%s %s: HTTP %s", domain, service, entity_id, r.status)
                    return False
                return True
        except Exception as e:  # noqa: BLE001
            log.warning("%s.%s %s: %s", domain, service, entity_id, e)
            return False

    async def set_sensor(self, entity_id: str, state: str, attributes: dict) -> None:
        if not self.session:
            return
        try:
            async with self.session.post(f"{API_URL}/states/{entity_id}",
                                         json={"state": state, "attributes": attributes}) as r:
                if r.status >= 300:
                    log.warning("set %s: HTTP %s", entity_id, r.status)
        except Exception as e:  # noqa: BLE001
            log.warning("set %s: %s", entity_id, e)
