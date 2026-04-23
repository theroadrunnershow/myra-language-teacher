"""Test helpers for the kids-teacher realtime layer.

This module is intentionally import-free of any external SDK. It exists so
both the Intern 1 handler tests and the Intern 3 flow tests can import a
ready-made scripted backend:

    from kids_teacher_fakes import FakeRealtimeBackend

Kept out of ``kids_teacher_realtime.py`` to keep the production handler
module under 400 lines and to make it obvious that importing this file is
a test-only convenience.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional


class FakeRealtimeBackend:
    """Scripted :class:`RealtimeBackend` for tests.

    Pass a list of events to emit in order. Tests can also push additional
    events mid-run via :meth:`push_event`. All side-effect calls
    (send_audio, send_text, cancel_response, close) are recorded.
    """

    def __init__(
        self,
        scripted_events: Optional[List[dict]] = None,
        *,
        connect_error: Optional[Exception] = None,
    ) -> None:
        self._scripted = list(scripted_events or [])
        self._connect_error = connect_error
        self._queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
        self._connected = False
        self._closed = False

        # Call recorders for assertions.
        self.connect_calls: List[dict] = []
        self.audio_chunks: List[bytes] = []
        self.text_messages: List[str] = []
        self.cancel_calls: int = 0
        self.close_calls: int = 0

    async def connect(self, session_payload: dict) -> None:
        self.connect_calls.append(session_payload)
        if self._connect_error is not None:
            raise self._connect_error
        self._connected = True
        # Seed scripted events into the queue so events() can yield them.
        for event in self._scripted:
            await self._queue.put(event)

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)

    async def send_text(self, text: str) -> None:
        self.text_messages.append(text)

    async def cancel_response(self) -> None:
        self.cancel_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
        self._closed = True
        # Unblock any active events() consumer.
        await self._queue.put(None)

    async def push_event(self, event: dict) -> None:
        """Inject an event into the live stream after connect()."""
        await self._queue.put(event)

    async def end_stream(self) -> None:
        """Signal that no more events will arrive (stream complete)."""
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[dict]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
            # Yield control so the handler can process each event before
            # the next one arrives — matches real streaming pacing.
            await asyncio.sleep(0)
