import os
import sys

import pytest
from smda.Disassembler import Disassembler

from mcrit_similarity import diskcache, export
from mcrit_similarity.cache import REPORT_KEYS, REPORTS


class _Raw:
    start = 0

    def __init__(self, data: bytes):
        self.data = data
        self.length = len(data)

    def read(self, start, length):
        return self.data[start : start + length]


class _View:
    """Analysis adds a function when it runs, like a view whose analysis is still settling."""

    arch = None

    def __init__(self, data: bytes | None, session_id: int):
        raw = _Raw(data) if data is not None else None
        self.file = type("File", (), {"raw": raw, "session_id": session_id})()
        self.functions = [0]
        self.analysis_runs = 0

    def update_analysis_and_wait(self):
        self.analysis_runs += 1
        if self.analysis_runs == 1:
            self.functions.append(1)


def test_key_names_the_analysed_function_set(monkeypatch):
    builds = []
    monkeypatch.setattr(export, "_build_report", lambda bv: builds.append(bv) or object())
    view = _View(b"analysed-set", session_id=201)
    first = export.export_smda_report(view)
    second = export.export_smda_report(view)
    assert first is second
    assert len(builds) == 1
    assert REPORT_KEYS.get(201)[1] == 2


def test_view_without_file_data_is_never_cached(monkeypatch):
    monkeypatch.setattr(export, "_build_report", lambda bv: object())
    view = _View(None, session_id=202)
    assert export._view_cache_key(view) is None
    assert export.export_smda_report(view) is not export.export_smda_report(view)
    assert REPORT_KEYS.get(202) is None


def test_hash_spans_chunks(monkeypatch):
    monkeypatch.setattr(export, "_HASH_CHUNK", 3)
    whole = export._view_cache_key(_View(b"abcdefgh", session_id=203))
    monkeypatch.setattr(export, "_HASH_CHUNK", 1024)
    assert export._view_cache_key(_View(b"abcdefgh", session_id=204)) == whole


def test_report_cache_is_bounded():
    assert REPORTS._max_items is not None


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "_disk_folder", lambda: tmp_path)
    return tmp_path


def _tiny_report():
    return Disassembler().disassembleBuffer(bytes.fromhex("554889e55dc3"), 0x1000, bitness=64)


def _forget(key):
    REPORTS._items.pop(key, None)


def _no_analysis(view):
    view.update_analysis_and_wait = None


def test_export_writes_disk_and_restart_skips_export(folder, monkeypatch):
    builds = []
    monkeypatch.setattr(export, "_build_report", lambda bv: builds.append(bv) or _tiny_report())
    first = export.export_smda_report(_View(b"persisted", session_id=301))
    key = REPORT_KEYS.get(301)
    assert diskcache.report_path(folder, key[0]).exists()
    _forget(key)

    second = export.export_smda_report(_View(b"persisted", session_id=302))
    assert len(builds) == 1
    assert second is not first
    assert getattr(second, "xcfg").keys() == getattr(first, "xcfg").keys()


def test_cold_render_loads_from_disk(folder, monkeypatch):
    monkeypatch.setattr(export, "_build_report", lambda bv: _tiny_report())
    export.export_smda_report(_View(b"cold-render", session_id=311))
    key = REPORT_KEYS.get(311)
    _forget(key)

    view = _View(b"cold-render", session_id=312)
    view.functions.append(1)
    _no_analysis(view)
    monkeypatch.setattr(export, "_build_report", None)
    report = export.cached_report(view)
    assert report is not None
    assert REPORT_KEYS.get(312) == key
    assert REPORTS.get(key) is report
    assert export.cached_report(view) is report


def test_cold_render_reuses_report_in_memory(folder, monkeypatch):
    monkeypatch.setattr(export, "_build_report", lambda bv: _tiny_report())
    first = export.export_smda_report(_View(b"reopened", session_id=321))
    loads = []
    monkeypatch.setattr(diskcache, "load", lambda *a: loads.append(a))
    view = _View(b"reopened", session_id=322)
    view.functions.append(1)
    assert export.cached_report(view) is first
    assert loads == []


def test_cold_render_skips_large_files_without_hashing(folder, monkeypatch):
    hashes = []
    monkeypatch.setattr(export, "_COLD_MAX_RAW_BYTES", 4)
    monkeypatch.setattr(export, "_raw_sha256", lambda raw: hashes.append(raw) or "x")
    view = _View(b"too large", session_id=331)
    assert export.cached_report(view) is None
    assert export.cached_report(view) is None
    assert hashes == []
    assert export.cached_report(_View(None, session_id=332)) is None


def test_cold_render_skips_large_stored_reports(folder, monkeypatch):
    monkeypatch.setattr(export, "_build_report", lambda bv: _tiny_report())
    export.export_smda_report(_View(b"big report", session_id=341))
    key = REPORT_KEYS.get(341)
    _forget(key)
    monkeypatch.setattr(export, "_COLD_MAX_STORED_BYTES", 10)
    view = _View(b"big report", session_id=342)
    view.functions.append(1)
    assert export.cached_report(view) is None
    assert REPORT_KEYS.get(342) is None


def test_cold_render_memoizes_hash_and_misses(folder, monkeypatch):
    hashes = []
    real_hash = export._raw_sha256
    monkeypatch.setattr(export, "_raw_sha256", lambda raw: hashes.append(1) or real_hash(raw))
    loads = []
    monkeypatch.setattr(export, "_load_small", lambda key: loads.append(key))
    view = _View(b"never exported", session_id=351)
    assert export.cached_report(view) is None
    assert export.cached_report(view) is None
    assert len(hashes) == 1
    assert len(loads) == 1
    # Analysis finding more functions changes the key, so the lookup is retried once.
    view.functions.append(1)
    assert export.cached_report(view) is None
    assert export.cached_report(view) is None
    assert len(hashes) == 1
    assert len(loads) == 2


def test_cold_render_without_disk_cache(monkeypatch):
    monkeypatch.setattr(export, "_disk_folder", lambda: None)
    view = _View(b"no disk", session_id=361)
    assert export.cached_report(view) is None
    assert view.analysis_runs == 0


def test_restart_with_other_function_count_uses_disk(folder, monkeypatch):
    builds = []
    monkeypatch.setattr(export, "_build_report", lambda bv: builds.append(bv) or _tiny_report())
    export.export_smda_report(_View(b"recount", session_id=371))
    view = _View(b"recount", session_id=372)
    view.functions.extend([2, 3])
    export.export_smda_report(view)
    assert len(builds) == 1
    assert REPORT_KEYS.get(372)[1] == 4


def test_analysis_change_in_process_rebuilds_and_rewrites(folder, monkeypatch):
    builds = []
    monkeypatch.setattr(export, "_build_report", lambda bv: builds.append(bv) or _tiny_report())
    view = _View(b"reanalysed", session_id=381)
    export.export_smda_report(view)
    path = diskcache.report_path(folder, REPORT_KEYS.get(381)[0])
    os.utime(path, (0, 0))
    view.functions.append(2)
    export.export_smda_report(view)
    assert len(builds) == 2
    assert path.stat().st_mtime > 0


def test_disk_cache_disabled_without_user_directory(monkeypatch):
    monkeypatch.delattr(sys.modules["binaryninja"], "user_directory", raising=False)
    builds = []
    monkeypatch.setattr(export, "_build_report", lambda bv: builds.append(bv) or object())
    assert export._resolve_disk_folder() is None
    assert export._disk_folder() is None
    export.export_smda_report(_View(b"stub has no user dir", session_id=391))
    assert len(builds) == 1


def test_disk_folder_is_under_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys.modules["binaryninja"], "user_directory", lambda: str(tmp_path), raising=False
    )
    assert export._resolve_disk_folder() == tmp_path / "mcrit_similarity" / "smda_reports"


class _PreloadView(_View):
    view_type = "Mach-O"


def test_preload_fills_the_render_lookup_from_disk(monkeypatch, tmp_path):
    stored = object()
    monkeypatch.setattr(export, "_disk_folder", lambda: tmp_path)
    monkeypatch.setattr(export.diskcache, "load", lambda folder, sha256: stored)
    view = _PreloadView(b"preload-me", session_id=9701)
    export.preload_report(view)
    assert export.cached_report(view) is stored


def test_preload_skips_raw_views_and_missing_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(export, "_disk_folder", lambda: tmp_path)
    monkeypatch.setattr(export.diskcache, "load", lambda folder, sha256: None)
    view = _PreloadView(b"not-stored", session_id=9702)
    export.preload_report(view)
    assert REPORT_KEYS.get(9702) is None

    raw_view = _PreloadView(b"raw", session_id=9703)
    raw_view.view_type = "Raw"
    monkeypatch.setattr(export.diskcache, "load", lambda folder, sha256: object())
    export.preload_report(raw_view)
    assert REPORT_KEYS.get(9703) is None


def test_export_and_preload_warm_the_architecture(monkeypatch, tmp_path):
    touched = []

    class _ArchView(_PreloadView):
        @property
        def arch(self):
            touched.append(self.file.session_id)

    monkeypatch.setattr(export, "_build_report", lambda bv: object())
    monkeypatch.setattr(export, "_disk_folder", lambda: None)
    export.export_smda_report(_ArchView(b"warm-export", session_id=9711))
    export.preload_report(_ArchView(b"warm-preload", session_id=9712))
    assert touched == [9711, 9712]
