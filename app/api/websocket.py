"""WebSocket endpoint for streaming tool-run status.

Frontend (later): open `ws://127.0.0.1:8000/ws`, listen for
`tool_started` / `tool_finished` events, and update a live status panel
while REST `POST /tools/run` drives the actual call. No auth in Phase 1.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Simple in-memory broadcaster for all connected WebSocket clients."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WebSocket client disconnected (%d total)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send JSON to every connected client; drop dead sockets cleanly."""
        stale: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


# Shared singleton used by the executor to push status events.
manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_status(websocket: WebSocket) -> None:
    """Stream bridge status updates to the frontend."""
    await manager.connect(websocket)
    await websocket.send_json(
        {"type": "connected", "message": "Bridge WebSocket ready"}
    )
    try:
        # Keep the socket open. Clients may send pings/text; we ignore content for now.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
        raise
