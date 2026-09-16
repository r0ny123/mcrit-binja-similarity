import threading
import time
from unittest.mock import Mock

from mcrit_similarity.cache import LockedCache


def test_locked_cache_basic_get_set():
    cache = LockedCache()
    assert cache.get("k") is None
    assert cache.set("k", "v") == "v"
    assert cache.get("k") == "v"


def test_locked_cache_get_or_set_hit_and_miss():
    cache = LockedCache()
    factory = Mock(return_value="created")
    val1 = cache.get_or_set("k1", factory)
    assert val1 == "created"
    assert factory.call_count == 1

    val2 = cache.get_or_set("k1", factory)
    assert val2 == "created"
    assert factory.call_count == 1


def test_locked_cache_does_not_cache_empty_by_default():
    cache = LockedCache()
    factory = Mock(return_value={})
    val1 = cache.get_or_set("empty_map", factory, cache_empty=False)
    assert val1 == {}
    assert factory.call_count == 1
    # Because it was empty, it should not be in cache
    assert cache.get("empty_map") is None

    # Calling again should invoke factory again
    factory.return_value = {1: "offset"}
    val2 = cache.get_or_set("empty_map", factory, cache_empty=False)
    assert val2 == {1: "offset"}
    assert factory.call_count == 2
    assert cache.get("empty_map") == {1: "offset"}


def test_locked_cache_concurrent_single_flight():
    cache = LockedCache()
    invocations = 0
    lock = threading.Lock()

    def slow_factory():
        nonlocal invocations
        with lock:
            invocations += 1
        time.sleep(0.05)
        return "expensive_result"

    results = []

    def worker():
        res = cache.get_or_set("shared_key", slow_factory)
        results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert invocations == 1
    assert len(results) == 5
    assert all(r == "expensive_result" for r in results)


def test_bounded_cache_evicts_least_recently_used():
    cache = LockedCache(max_items=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get_or_set("d", lambda: 4) == 4
    assert cache.get("c") is None


def test_waiter_can_stop_while_another_caller_builds():
    import threading

    import pytest

    from mcrit_similarity.errors import McritJobError

    cache = LockedCache()
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "built"

    builder = threading.Thread(target=lambda: cache.get_or_set("k", slow))
    builder.start()
    started.wait(5)
    with pytest.raises(McritJobError, match="cancelled"):
        cache.get_or_set("k", lambda: "never", should_stop=lambda: True)
    release.set()
    builder.join(5)
    assert cache.get("k") == "built"
