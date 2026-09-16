"""Process-wide caches. Provider visits may overlap across threads."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from mcrit_similarity.errors import McritJobError

T = TypeVar("T")


class LockedCache:
    """Thread-safe cache; with ``max_items`` it evicts least recently used entries."""

    def __init__(self, max_items: int | None = None) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[Any, Any] = OrderedDict()
        self._inflight: dict[Any, threading.Event] = {}
        self._max_items = max_items

    def _store(self, key: Any, value: Any) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        if self._max_items is not None:
            while len(self._items) > self._max_items:
                self._items.popitem(last=False)

    def get(self, key: Any) -> Any:
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def set(self, key: Any, value: Any) -> Any:
        with self._lock:
            self._store(key, value)
            return value

    def get_or_set(
        self,
        key: Any,
        factory: Callable[[], T],
        *,
        cache_empty: bool = False,
        should_stop: Callable[[], bool] | None = None,
    ) -> T:
        """Build ``key`` once; concurrent callers wait, and ``should_stop`` lets a waiter give up."""
        while True:
            with self._lock:
                if key in self._items:
                    self._items.move_to_end(key)
                    return self._items[key]
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break
            if should_stop is None:
                event.wait()
                continue
            while not event.wait(0.25):
                if should_stop():
                    raise McritJobError("MCRIT visit cancelled")

        try:
            value = factory()
            with self._lock:
                should_cache = cache_empty or (
                    value is not None
                    and (not isinstance(value, (dict, list, set)) or len(value) > 0)
                )
                if should_cache:
                    self._store(key, value)
            return value
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()


# SMDA reports hold every instruction of a binary, so only the most recent ones are kept.
REPORTS = LockedCache(max_items=16)
# Open file (FileMetadata.session_id) -> REPORTS key of the report exported for it.
REPORT_KEYS = LockedCache()
# (server, sha256) -> SampleRef
SAMPLES = LockedCache()
# (server, function_id) -> (offset, corpus label)
FUNCTION_OFFSETS = LockedCache()
