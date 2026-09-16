"""SMDA reports persisted in a folder, so restarts skip re-exporting.

Files are keyed by file sha256 alone: BN's function count for the same bytes varies by a few
between processes, so it cannot identify a report across restarts.
"""

from __future__ import annotations

import contextlib
import gc
import gzip
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from mcrit_similarity.log import log_debug, log_warn

# Bump when the file layout changes; files of other versions are never read.
FORMAT_VERSION = 1
SUFFIX = ".json.gz"
# A 130 KiB malware sample stores ~0.5 MiB and /bin/zsh ~2 MiB, so these hold hundreds of
# binaries while keeping the folder small next to the user's .bndb files.
MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 1000
# Temp files older than this belong to a writer that died before os.replace.
_STALE_TEMP_SECONDS = 24 * 60 * 60


def _smda_version() -> str:
    import smda

    return re.sub(r"[^0-9A-Za-z.]", "_", str(smda.__version__))


def report_path(folder: Path, sha256: str) -> Path:
    return folder / f"{sha256}-v{FORMAT_VERSION}-smda{_smda_version()}{SUFFIX}"


def _header(sha256: str) -> dict:
    return {"format": FORMAT_VERSION, "smda": _smda_version(), "sha256": sha256}


def load(folder: Path, sha256: str) -> Any:
    path = report_path(folder, sha256)
    from smda.common.SmdaReport import SmdaReport

    # fromDict allocates objects per instruction; cyclic GC passes during it double the load time.
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        with gzip.open(path, "rb") as handle:
            document = json.loads(handle.read())
        if {k: document.get(k) for k in ("format", "smda", "sha256")} != _header(sha256):
            raise ValueError("header does not match the requested file")
        report = SmdaReport.fromDict(document["report"])
    except FileNotFoundError:
        return None
    except Exception as exc:
        # Truncated gzip, bad JSON or a report SMDA rejects: all unusable, so drop the file.
        log_warn(f"MCRIT: discarding unreadable cached SMDA report {path.name}: {exc}")
        _unlink(path)
        return None
    finally:
        if gc_was_enabled:
            gc.enable()
    with contextlib.suppress(OSError):
        os.utime(path)
    log_debug(f"Loaded cached SMDA report {path.name}")
    return report


def store(folder: Path, sha256: str, report: Any) -> None:
    path = report_path(folder, sha256)
    document = _header(sha256)
    document["report"] = report.toDict()
    payload = json.dumps(document, separators=(",", ":")).encode()
    temp_name = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=folder, prefix=".", suffix=".tmp")
        with (
            os.fdopen(fd, "wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as out,
        ):
            out.write(payload)
        # Same-directory rename is atomic, so readers see the old file or the whole new one.
        os.replace(temp_name, path)
        temp_name = None
        prune(folder, keep=path)
    except OSError as exc:
        log_warn(f"MCRIT: could not cache SMDA report on disk: {exc}")
    finally:
        if temp_name is not None:
            _unlink(Path(temp_name))


def prune(
    folder: Path,
    *,
    keep: Path | None = None,
    max_bytes: int | None = None,
    max_files: int | None = None,
) -> None:
    """Delete least recently used reports (oldest mtime first) beyond the size and count caps."""
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    max_files = MAX_FILES if max_files is None else max_files
    now = time.time()
    entries = []
    with os.scandir(folder) as it:
        for entry in it:
            try:
                stat = entry.stat()
            except OSError:
                continue
            if entry.name.endswith(".tmp"):
                if now - stat.st_mtime > _STALE_TEMP_SECONDS:
                    _unlink(Path(entry.path))
            elif entry.name.endswith(SUFFIX):
                entries.append((stat.st_mtime, stat.st_size, Path(entry.path)))
    entries.sort()
    total = sum(size for _, size, _ in entries)
    count = len(entries)
    for _, size, path in entries:
        if total <= max_bytes and count <= max_files:
            break
        if path == keep:
            continue
        _unlink(path)
        total -= size
        count -= 1


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
