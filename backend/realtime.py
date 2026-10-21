"""In-process WebSocket event fanout for the single-instance local server."""
import asyncio
from datetime import datetime, timezone

from fastapi import WebSocket


def event_envelope(event: str, data: dict) -> dict:
    return {
        "event": event,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


class EventManager:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._clients.discard(websocket)

    async def publish(self, event: str, data: dict):
        message = event_envelope(event, data)
        async with self._lock:
            clients = tuple(self._clients)
        failed = []
        for client in clients:
            try:
                await client.send_json(message)
            except Exception:
                failed.append(client)
        if failed:
            async with self._lock:
                self._clients.difference_update(failed)


event_manager = EventManager()
