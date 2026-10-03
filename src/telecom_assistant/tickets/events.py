"""In-process pub/sub feeding Server-Sent Events: customers get their own tickets' updates, agents get
the queue. (Multi-instance deployments would swap this for Redis pub/sub; the UI also polls as a fallback.)"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict
from datetime import UTC, datetime


class EventBus:
    def __init__(self) -> None:
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers[channel].add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self.subscribers[channel].discard(queue)

    def publish(self, channels: list[str], kind: str, data: dict) -> None:
        message = json.dumps({"kind": kind, "at": datetime.now(UTC).isoformat(), **data}, default=str)
        for channel in channels:
            for queue in list(self.subscribers.get(channel, ())):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(message)

    def ticket(self, ticket_id: str, owner_id: str, kind: str, **data) -> None:
        self.publish([f"user:{owner_id}", "agents"], kind, {"ticket_id": ticket_id, **data})
