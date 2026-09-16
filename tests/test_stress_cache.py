"""LockedCache under concurrent callers, and the disk cache under adversarial files."""

import gc
import gzip
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from smda.Disassembler import Disassembler

from mcrit_similarity import diskcache
from mcrit_similarity.cache import LockedCache
from mcrit_similarity.errors import McritJobError

PROPERTY = settings(
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

_CODE = bytes.fromhex("554889e5b8010000005dc3") + b"\x90" * 5 + bytes.fromhex("e8ebffffffc3")


@pytest.fixture(scope="module")
def report():
    return Disassembler().disassembleBuffer(_CODE, 0x1000, bitness=64)


def hammer(cache, key, builds, threads=16, **kwargs):
    start = threading.Barrier(threads)

    def worker(index):
        start.wait(timeout=10)
        return cache.get_or_set(key, lambda: builds(index), **kwargs)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(worker, range(threads)))


def test_concurrent_callers_share_one_build():
    cache = LockedCache()
    calls = []

    def build(index):
        calls.append(index)
        return f"value-{index}"

    results = hammer(cache, "k", build)
    assert len(calls) == 1
    assert set(results) == {f"value-{calls[0]}"}
    assert cache.get("k") == results[0]


def test_a_failing_build_does_not_wedge_the_key():
    cache = LockedCache()
    attempts = []

    def build(index):
        attempts.append(index)
        if len(attempts) < 3:
            raise RuntimeError("transient")
        return "ok"

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cache.get_or_set("k", lambda: build(0))
    assert cache.get_or_set("k", lambda: build(0)) == "ok"
    assert cache.get("k") == "ok"


def test_waiters_give_up_when_should_stop_turns_true():
    cache = LockedCache()
    release = threading.Event()
    waiting = threading.Barrier(2)

    def slow():
        waiting.wait(timeout=10)
        release.wait(5)
        return "built"

    with ThreadPoolExecutor(max_workers=2) as pool:
        builder = pool.submit(cache.get_or_set, "k", slow)
        waiting.wait(timeout=10)
        waiter = pool.submit(cache.get_or_set, "k", lambda: "never", should_stop=lambda: True)
        with pytest.raises(McritJobError, match="cancelled"):
            waiter.result(timeout=5)
        release.set()
        assert builder.result(timeout=5) == "built"


@given(
    st.integers(min_value=1, max_value=8),
    st.lists(st.integers(min_value=0, max_value=12), min_size=1, max_size=40),
)
@PROPERTY
def test_lru_never_exceeds_its_cap_and_keeps_the_newest(max_items, keys):
    cache = LockedCache(max_items=max_items)
    for key in keys:
        cache.get_or_set(key, lambda key=key: key)
        assert len(cache._items) <= max_items
    for key in list(dict.fromkeys(reversed(keys)))[:max_items]:
        assert cache.get(key) == key


def test_reading_an_entry_protects_it_from_eviction():
    cache = LockedCache(max_items=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") is None


@pytest.mark.parametrize("value", [None, {}, [], set(), "", 0])
def test_empty_results_are_only_cached_on_request(value):
    cache = LockedCache()
    assert cache.get_or_set("k", lambda: value) == value
    stored_by_default = cache.get("k") is not None
    assert stored_by_default == (value not in (None, {}, [], set()))
    cache = LockedCache()
    cache.get_or_set("k", lambda: value, cache_empty=True)
    assert cache._items["k"] == value


def test_concurrent_writers_leave_one_readable_report(tmp_path, report):
    key = report.sha256
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: diskcache.store(tmp_path, key, report), range(6)))
    assert not list(tmp_path.glob("*.tmp"))
    assert len(list(tmp_path.glob(f"*{diskcache.SUFFIX}"))) == 1
    assert diskcache.load(tmp_path, key) is not None


@pytest.mark.parametrize(
    "corrupt",
    [
        b"",
        b"not gzip at all",
        gzip.compress(b"{"),
        gzip.compress(b'{"format": 1}'),
        gzip.compress(json.dumps({"format": 99, "smda": "x", "sha256": "z"}).encode()),
        gzip.compress(json.dumps({"report": {"nonsense": True}}).encode()),
    ],
)
def test_unreadable_files_are_discarded_not_raised(tmp_path, corrupt):
    key = "a" * 64
    path = diskcache.report_path(tmp_path, key)
    path.write_bytes(corrupt)
    gc.enable()
    assert diskcache.load(tmp_path, key) is None
    assert not path.exists()
    assert gc.isenabled(), "the cyclic collector must be restored on every exit path"


def test_a_truncated_file_is_dropped(tmp_path, report):
    key = report.sha256
    diskcache.store(tmp_path, key, report)
    path = diskcache.report_path(tmp_path, key)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    assert diskcache.load(tmp_path, key) is None
    assert not path.exists()


def test_a_header_for_another_file_is_rejected(tmp_path, report):
    key = report.sha256
    diskcache.store(tmp_path, key, report)
    other = diskcache.report_path(tmp_path, "b" * 64)
    other.write_bytes(diskcache.report_path(tmp_path, key).read_bytes())
    assert diskcache.load(tmp_path, "b" * 64) is None
    assert not other.exists()
    assert diskcache.load(tmp_path, key) is not None


def write_stub(path, size, mtime):
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def test_pruning_respects_both_caps_and_the_kept_file(tmp_path):
    paths = [tmp_path / f"{index:02d}-v1{diskcache.SUFFIX}" for index in range(6)]
    for index, path in enumerate(paths):
        write_stub(path, 100, index)
    keep = paths[0]
    diskcache.prune(tmp_path, keep=keep, max_bytes=250, max_files=100)
    # keep survives and still counts against the budget, so deletion continues past it.
    survivors = sorted(p.name for p in tmp_path.glob(f"*{diskcache.SUFFIX}"))
    assert survivors == [keep.name, paths[5].name]

    for index, path in enumerate(paths):
        write_stub(path, 10, index)
    diskcache.prune(tmp_path, max_bytes=10**9, max_files=2)
    assert sorted(p.name for p in tmp_path.glob(f"*{diskcache.SUFFIX}")) == [
        paths[4].name,
        paths[5].name,
    ]


def test_stale_temp_files_are_collected_and_fresh_ones_kept(tmp_path):
    stale = tmp_path / ".old.tmp"
    fresh = tmp_path / ".new.tmp"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    aged = time.time() - 2 * diskcache._STALE_TEMP_SECONDS
    os.utime(stale, (aged, aged))
    diskcache.prune(tmp_path)
    assert not stale.exists()
    assert fresh.exists()


def test_a_read_only_folder_is_reported_not_raised(tmp_path, report, caplog):
    blocked = tmp_path / "reports"
    blocked.write_bytes(b"this is a file, not a folder")
    diskcache.store(blocked, report.sha256, report)
    assert diskcache.load(blocked, report.sha256) is None
