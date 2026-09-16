"""Convert a Binary Ninja view into an SMDA report using BN analysis."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

from mcrit_similarity import diskcache
from mcrit_similarity.backend import BinjaSmdaInterface
from mcrit_similarity.cache import REPORT_KEYS, REPORTS, LockedCache
from mcrit_similarity.errors import McritJobError
from mcrit_similarity.log import log_debug

StopFn = Callable[[], bool]

_HASH_CHUNK = 16 * 1024 * 1024
# Render runs on the UI thread, so a cold lookup must stay within ~100 ms. Hashing costs
# ~3.5 ms/MiB of file and loading ~115 ms/MiB of stored report, so these caps bound a lookup
# at ~15 ms + ~90 ms. Larger binaries wait for the session to run.
_COLD_MAX_RAW_BYTES = 4 * 1024 * 1024
_COLD_MAX_STORED_BYTES = 768 * 1024
# session_id -> sha256 of the file, or "" when it is too large to hash on the UI thread.
_COLD_SHA256 = LockedCache()
# (session_id, key) pairs whose report could not be loaded for the cold render path.
_COLD_MISSES = LockedCache()
# session_id -> key of its last export. A later export under another key means analysis
# changed in this process, so the disk copy of the earlier analysis must not stand in.
_EXPORTED_KEYS = LockedCache()
_DISK_FOLDER = LockedCache()


def _resolve_disk_folder() -> Path | None:
    import binaryninja

    # The unit-test stub of binaryninja has no user_directory, which disables the disk cache.
    getter = getattr(binaryninja, "user_directory", None)
    root = getter() if getter is not None else None
    return Path(root) / "mcrit_similarity" / "smda_reports" if root else None


def _disk_folder() -> Path | None:
    return _DISK_FOLDER.get_or_set("folder", _resolve_disk_folder, cache_empty=True)


def _raw_sha256(raw) -> str:
    digest = hashlib.sha256()
    end = raw.start + raw.length
    for offset in range(raw.start, end, _HASH_CHUNK):
        digest.update(raw.read(offset, min(_HASH_CHUNK, end - offset)))
    return digest.hexdigest()


def _view_cache_key(bv) -> tuple[str, int] | None:
    """Content key for a view's report, or None when there is no file data to identify it by."""
    raw = bv.file.raw
    if raw is None or raw.length <= 0:
        return None
    return _raw_sha256(raw), len(bv.functions)


def _cold_sha256(bv) -> str:
    raw = bv.file.raw
    if raw is None or not 0 < raw.length <= _COLD_MAX_RAW_BYTES:
        return ""
    return _raw_sha256(raw)


def cached_report(bv) -> object | None:
    """The report for this open file from memory, or a small one from disk (UI-thread safe)."""
    session = bv.file.session_id
    key = REPORT_KEYS.get(session)
    if key is None:
        sha256 = _COLD_SHA256.get_or_set(session, lambda: _cold_sha256(bv))
        if not sha256:
            return None
        key = (sha256, len(bv.functions))
    report = REPORTS.get(key)
    if report is None:
        if _COLD_MISSES.get((session, key)):
            return None
        report = _load_small(key)
        if report is None:
            _COLD_MISSES.set((session, key), True)
            return None
        REPORTS.set(key, report)
    REPORT_KEYS.set(session, key)
    return report


# Preloading runs off the UI thread, so it can afford larger files than the cold render path.
_PRELOAD_MAX_RAW_BYTES = 64 * 1024 * 1024


def _warm_architecture(bv) -> None:
    # Binary Ninja builds its Python Architecture wrapper (~150-250 ms) on first use and caches it
    # process-wide. Exporting used to pay this; with stored reports it would land on the UI thread.
    _ = bv.arch


def preload_report(bv) -> None:
    """Load a stored report for a freshly analysed view so its first render needs no disk I/O."""
    _warm_architecture(bv)
    raw = bv.file.raw
    if bv.view_type == "Raw" or raw is None or not 0 < raw.length <= _PRELOAD_MAX_RAW_BYTES:
        return
    session = bv.file.session_id
    folder = _disk_folder()
    if folder is None or REPORT_KEYS.get(session) is not None:
        return
    sha256 = _raw_sha256(raw)
    if raw.length <= _COLD_MAX_RAW_BYTES:
        _COLD_SHA256.set(session, sha256)
    key = (sha256, len(bv.functions))
    report = REPORTS.get(key)
    if report is None:
        report = diskcache.load(folder, sha256)
        if report is None:
            return
        REPORTS.set(key, report)
    REPORT_KEYS.set(session, key)


def _load_small(key: tuple[str, int]) -> object | None:
    folder = _disk_folder()
    if folder is None:
        return None
    try:
        if diskcache.report_path(folder, key[0]).stat().st_size > _COLD_MAX_STORED_BYTES:
            return None
    except OSError:
        return None
    return diskcache.load(folder, key[0])


def _build_report(bv) -> object:
    from smda.Disassembler import Disassembler
    from smda.ida.IdaExporter import IdaExporter

    # Capstone often cannot decode a few malware bytes; that is a skip, not a failed export.
    logging.getLogger("smda.ida.IdaExporter").setLevel(logging.ERROR)

    interface = BinjaSmdaInterface(bv)
    disassembler = Disassembler()
    disassembler.disassembler = IdaExporter(disassembler.config, ida_interface=interface)
    disassembler._explicit_backend = True
    report = disassembler.disassembleBuffer(
        interface.getBinary(),
        interface.getBaseAddr(),
        bitness=interface.getBitness(),
        architecture=interface.getArchitecture(),
    )
    filename = os.path.basename(bv.file.original_filename or bv.file.filename or "")
    if filename:
        report.filename = filename
    sha256 = getattr(report, "sha256", None) or ""
    functions = list(report.getFunctions()) if hasattr(report, "getFunctions") else []
    log_debug(f"Exported SMDA report {sha256[:12]}… ({len(functions)} functions)")
    return report


def _load_or_build(bv, key: tuple[str, int]) -> object:
    folder = _disk_folder()
    if folder is None:
        return _build_report(bv)
    first_export = _EXPORTED_KEYS.get(bv.file.session_id) is None
    report = diskcache.load(folder, key[0]) if first_export else None
    if report is None:
        report = _build_report(bv)
        diskcache.store(folder, key[0], report)
    return report


def export_smda_report(bv, *, should_stop: StopFn | None = None) -> object:
    if should_stop and should_stop():
        raise McritJobError("SMDA export cancelled")
    # Key only after analysis settles, so the key names the function set the report holds.
    bv.update_analysis_and_wait()
    if should_stop and should_stop():
        raise McritJobError("SMDA export cancelled")
    _warm_architecture(bv)
    key = _view_cache_key(bv)
    if key is None:
        return _build_report(bv)
    report = REPORTS.get_or_set(key, lambda: _load_or_build(bv, key), should_stop=should_stop)
    REPORT_KEYS.set(bv.file.session_id, key)
    _EXPORTED_KEYS.set(bv.file.session_id, key)
    return report
