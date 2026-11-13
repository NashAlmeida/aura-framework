"""Simple in-memory short-term conversation memory (per session)."""

from __future__ import annotations

from threading import Lock
from typing import Any


class ShortTermMemory:
    """Stores recent turns for a session id. Not persisted across process restarts."""

    def __init__(self, max_turns: int = 20) -> None:
        self.max_turns = max_turns
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._lock = Lock()

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            history = self._sessions.setdefault(session_id, [])
            history.append({"role": role, "content": content})
            # Keep the window small for Phase 2.
            if len(history) > self.max_turns:
                self._sessions[session_id] = history[-self.max_turns :]

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sessions.get(session_id, []))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


# Process-wide default memory store for /agent/run.
memory_store = ShortTermMemory()
