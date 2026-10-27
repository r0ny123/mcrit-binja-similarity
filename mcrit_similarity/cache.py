"""Process-wide caches. Provider visits may overlap across threads."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from mcrit_similarity.errors import McritJobError

T = TypeVar("T")


class LockedCache:
    """Thread-safe cache that evicts least recently used entries beyond ``max_items`` or, when
    ``weigh`` is given, ``max_weight``. The newest entry is always kept, however heavy."""

    def __init__(
        self,
        max_items: int | None = None,
        *,
        max_weight: int | None = None,
        weigh: Callable[[Any], int] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[Any, Any] = OrderedDict()
        self._weights: dict[Any, int] = {}
        self._total_weight = 0
        self._inflight: dict[Any, threading.Event] = {}
        self._max_items = max_items
        self._max_weight = max_weight
        self._weigh = weigh

    def _over_limit(self) -> bool:
        too_many = self._max_items is not None and len(self._items) > self._max_items
        too_heavy = self._max_weight is not None and self._total_weight > self._max_weight
        return len(self._items) > 1 and (too_many or too_heavy)

    def _store(self, key: Any, value: Any, weight: int) -> None:
        self._total_weight -= self._weights.pop(key, 0)
        self._items[key] = value
        self._items.move_to_end(key)
        if self._weigh is not None:
            self._weights[key] = weight
            self._total_weight += weight
        while self._over_limit():
            evicted, _ = self._items.popitem(last=False)
            self._total_weight -= self._weights.pop(evicted, 0)

    def get(self, key: Any) -> Any:
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._weights.clear()
            self._total_weight = 0

    def _weight_of(self, value: Any) -> int:
        # Measured before taking the lock: weighing an SMDA report walks all of its functions.
        return self._weigh(value) if self._weigh is not None else 0

    def set(self, key: Any, value: Any) -> Any:
        weight = self._weight_of(value)
        with self._lock:
            self._store(key, value, weight)
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
                    if should_stop is not None and should_stop():
                        raise McritJobError("MCRIT visit cancelled")
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
            should_cache = cache_empty or (
                value is not None and (not isinstance(value, (dict, list, set)) or len(value) > 0)
            )
            weight = self._weight_of(value) if should_cache else 0
            with self._lock:
                if should_cache:
                    self._store(key, value, weight)
            return value
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()


# An SMDA report holds every instruction as Python objects, about 165 bytes each, so the cache is
# bounded by instruction count (5 million is roughly 800 MB) as well as by number of reports.
REPORTS = LockedCache(
    max_items=16, max_weight=5_000_000, weigh=lambda report: report.num_instructions
)
# Per-open-file entries are small, but headless runs over many files would otherwise keep all.
SESSION_CACHE_ITEMS = 1024
# Open file (FileMetadata.session_id) -> REPORTS key of the report exported for it.
REPORT_KEYS = LockedCache(max_items=SESSION_CACHE_ITEMS)
# (server, function_id) -> (offset, corpus label)
FUNCTION_OFFSETS = LockedCache(max_items=200_000)
