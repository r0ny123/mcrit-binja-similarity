import gc
import gzip
import json
import os

import pytest
from smda.Disassembler import Disassembler

from mcrit_similarity import diskcache
from mcrit_similarity.picblocks import hash_smda_function

_CODE = bytes.fromhex("554889e5b8010000005dc3") + b"\x90" * 5 + bytes.fromhex("e8ebffffffc3")


def _report():
    return Disassembler().disassembleBuffer(_CODE, 0x1000, bitness=64)


def _hashes(report):
    return {
        address: hash_smda_function(
            function, report.architecture, report.base_addr, report.binary_size
        )
        for address, function in report.xcfg.items()
    }


@pytest.fixture
def folder(tmp_path):
    return tmp_path


def test_roundtrip_matches_exported_report(folder):
    report = _report()
    key = report.sha256
    diskcache.store(folder, key, report)
    path = diskcache.report_path(folder, key)
    assert path.parent == folder and path.exists()
    assert not list(folder.glob("*.tmp"))
    loaded = diskcache.load(folder, key)
    assert loaded is not None
    assert json.dumps(loaded.toDict()["xcfg"], sort_keys=True) == json.dumps(
        report.toDict()["xcfg"], sort_keys=True
    )
    assert _hashes(loaded) == _hashes(report)


def test_missing_file_is_a_miss(folder):
    assert diskcache.load(folder, "0" * 64) is None


def test_other_versions_are_never_read(folder, monkeypatch):
    report = _report()
    key = report.sha256
    diskcache.store(folder, key, report)
    monkeypatch.setattr(diskcache, "_smda_version", lambda: "0.0.1")
    assert diskcache.load(folder, key) is None
    monkeypatch.setattr(diskcache, "FORMAT_VERSION", diskcache.FORMAT_VERSION + 1)
    assert diskcache.load(folder, key) is None
    assert len(list(folder.iterdir())) == 1


def test_header_must_name_the_requested_key(folder):
    report = _report()
    diskcache.store(folder, report.sha256, report)
    other = "b" * 64
    os.replace(diskcache.report_path(folder, report.sha256), diskcache.report_path(folder, other))
    assert diskcache.load(folder, other) is None
    assert not diskcache.report_path(folder, other).exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not gzip",
        gzip.compress(b"{not json"),
        gzip.compress(json.dumps({"format": 1}).encode()),
    ],
)
def test_unreadable_file_is_a_miss_and_removed(folder, content):
    key = "a" * 64
    path = diskcache.report_path(folder, key)
    path.write_bytes(content)
    assert diskcache.load(folder, key) is None
    assert not path.exists()


def test_truncated_file_is_a_miss_and_removed(folder):
    report = _report()
    key = report.sha256
    diskcache.store(folder, key, report)
    path = diskcache.report_path(folder, key)
    path.write_bytes(path.read_bytes()[:-8])
    assert diskcache.load(folder, key) is None
    assert not path.exists()


def test_prune_removes_oldest_beyond_caps(folder):
    paths = []
    for index in range(5):
        path = folder / f"{index}{diskcache.SUFFIX}"
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + index, 1000 + index))
        paths.append(path)
    stale = folder / ".old.tmp"
    stale.write_bytes(b"")
    os.utime(stale, (0, 0))
    fresh = folder / ".new.tmp"
    fresh.write_bytes(b"")

    diskcache.prune(folder, max_bytes=10_000, max_files=3)
    assert [p.exists() for p in paths] == [False, False, True, True, True]
    assert not stale.exists() and fresh.exists()

    diskcache.prune(folder, keep=paths[2], max_bytes=150, max_files=3)
    assert [p.exists() for p in paths] == [False, False, True, False, False]


def test_store_prunes_but_keeps_new_file(folder, monkeypatch):
    old = folder / f"old{diskcache.SUFFIX}"
    old.write_bytes(b"x")
    os.utime(old, (0, 0))
    monkeypatch.setattr(diskcache, "MAX_FILES", 1)
    report = _report()
    diskcache.store(folder, report.sha256, report)
    assert not old.exists()
    assert diskcache.report_path(folder, report.sha256).exists()


def test_load_refreshes_mtime(folder):
    report = _report()
    key = report.sha256
    diskcache.store(folder, key, report)
    path = diskcache.report_path(folder, key)
    os.utime(path, (0, 0))
    assert diskcache.load(folder, key) is not None
    assert path.stat().st_mtime > 0


def test_unwritable_folder_is_not_an_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_bytes(b"")
    report = _report()
    diskcache.store(blocker / "sub", report.sha256, report)
    assert diskcache.load(blocker / "sub", report.sha256) is None


def test_load_restores_gc_state(folder):
    report = _report()
    diskcache.store(folder, report.sha256, report)
    assert gc.isenabled()
    assert diskcache.load(folder, report.sha256) is not None
    assert gc.isenabled()
    diskcache.report_path(folder, report.sha256).write_bytes(b"bad")
    assert diskcache.load(folder, report.sha256) is None
    assert gc.isenabled()
