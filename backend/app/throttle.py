import time
from collections import deque


class SlidingWindowLimiter:
    """In-memory sliding-window counter.

    ponytail: process-local -- resets on restart / not shared across machines. Fine for the
    single Fly machine; the persisted daily gate stays the durable spend control.
    """

    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: deque = deque()

    def allow(self) -> bool:
        now = self._clock()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True
