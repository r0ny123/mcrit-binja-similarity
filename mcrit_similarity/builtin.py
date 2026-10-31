"""An MCRIT server inside the Binary Ninja process, for matching with nothing to set up.

MCRIT runs with in-memory storage and its in-request job queue, on a loopback port that only
answers requests carrying a token generated for this process. Samples last as long as the
process does.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import logging
import secrets
import sys
import threading
import types
from dataclasses import dataclass

from mcrit_similarity.errors import McritUnavailableError
from mcrit_similarity.log import log_error, log_info

BUILTIN = "builtin"
# Older LocalQueues minted job ids that MCRIT's own /jobs routes reject; 1.16 is the oldest checked.
MINIMUM_MCRIT = (1, 16)
_INSTALL_HINT = (
    "Install the mcrit package into Binary Ninja's Python (command palette: 'Install python3 "
    f"module', then mcrit>={'.'.join(map(str, MINIMUM_MCRIT))}), or set the server URL to a "
    "running MCRIT server."
)


@dataclass(frozen=True)
class Endpoint:
    url: str
    token: str


_lock = threading.Lock()
_endpoint: Endpoint | None = None
# The corpus file that failed to load at start, which an export must not overwrite.
unloaded_corpus = ""


def is_builtin(server: str) -> bool:
    return server.strip().lower() == BUILTIN


def endpoint(corpus: str = "") -> Endpoint:
    """The built-in server, started on first use with ``corpus`` loaded; callers wait meanwhile."""
    global _endpoint
    with _lock:
        if _endpoint is None:
            started = _start()
            if corpus:
                _load_corpus(started, corpus)
            _endpoint = started
        return _endpoint


def _load_corpus(started: Endpoint, path: str) -> None:
    global unloaded_corpus
    from mcrit_similarity.corpus import import_file, import_summary
    from mcrit_similarity.mcrit.client import McritClient

    try:
        report = import_file(McritClient(started.url, apitoken=started.token, timeout=None), path)
    except Exception as exc:
        # The server is already running; matching still works against what sessions upload.
        unloaded_corpus = path
        log_error(f"The built-in MCRIT server could not load the corpus {path}: {exc}")
        return
    log_info(import_summary(report, path))


def _version(text: str) -> tuple[int, ...]:
    parts = []
    for part in text.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def _start() -> Endpoint:
    _keep_logging_as_it_is()
    log_info("Starting the built-in MCRIT server…")
    try:
        from mcrit.config.McritConfig import McritConfig
        from mcrit.queue.QueueFactory import QueueFactory
        from mcrit.storage.StorageFactory import StorageFactory
        from waitress import create_server
    except ImportError as exc:
        raise McritUnavailableError(_import_problem(exc)) from exc
    if _version(McritConfig.VERSION) < MINIMUM_MCRIT:
        minimum = ".".join(map(str, MINIMUM_MCRIT))
        raise McritUnavailableError(
            f"The built-in MCRIT server needs mcrit {minimum} or newer, found "
            f"{McritConfig.VERSION}. {_INSTALL_HINT}"
        )

    token = secrets.token_urlsafe(32)
    # MCRIT reads these class attributes whenever it builds a config, so they apply process-wide.
    McritConfig.AUTH_TOKEN = token
    McritConfig.STORAGE_CONFIG.STORAGE_METHOD = StorageFactory.STORAGE_METHOD_MEMORY
    McritConfig.QUEUE_CONFIG.QUEUE_METHOD = QueueFactory.QUEUE_METHOD_FAKE
    # The pools spawn worker processes outside Binary Ninja's process; matching in-process gives
    # identical results.
    McritConfig.MINHASH_CONFIG.MINHASH_POOL_INDEXING = False
    McritConfig.MINHASH_CONFIG.MINHASH_POOL_MATCHING = False

    try:
        from mcrit.server.application_routes import get_app

        _silence_progress_bars()
    except ImportError as exc:
        raise McritUnavailableError(_import_problem(exc)) from exc
    # The in-request queue runs each job inside the request that submits it, on in-memory storage
    # that no lock protects, so one thread serves all requests.
    try:
        server = create_server(get_app(), host="127.0.0.1", port=0, threads=1)
    except Exception as exc:
        # Binding the loopback port or building MCRIT's index can fail, e.g. in a sandbox.
        raise McritUnavailableError(f"The built-in MCRIT server could not start: {exc}") from exc
    threading.Thread(target=server.run, name="mcrit-builtin", daemon=True).start()
    url = f"http://127.0.0.1:{server.effective_port}/"
    log_info(f"Started the built-in MCRIT {McritConfig.VERSION} server at {url}")
    return Endpoint(url=url, token=token)


def _import_problem(exc: ImportError) -> str:
    other = sys.modules.get("mcrit")
    if other is None:
        return f"The built-in MCRIT server needs the mcrit package ({exc}). {_INSTALL_HINT}"
    # Binary Ninja imports each plugin folder as a top-level module named after the folder, so a
    # plugin installed as "mcrit" takes the package's name.
    try:
        is_package = importlib.util.find_spec("mcrit.config") is not None
    except (ImportError, ValueError):
        is_package = False
    if not is_package:
        location = getattr(other, "__file__", None) or next(
            iter(getattr(other, "__path__", ())), ""
        )
        return (
            f"The built-in MCRIT server cannot import the mcrit package: the module named mcrit "
            f"at {location} is something else, such as a Binary Ninja plugin folder named mcrit. "
            "Rename that folder."
        )
    return (
        f"The built-in MCRIT server cannot load one of mcrit's dependencies ({exc}). "
        "Reinstall mcrit into Binary Ninja's Python."
    )


def _keep_logging_as_it_is() -> None:
    # MCRIT calls logging.basicConfig whenever root has no handler, which would print every INFO
    # record; Python's last-resort handler on root keeps warnings-to-stderr and makes that a no-op.
    if not logging.root.handlers and logging.lastResort is not None:
        logging.root.addHandler(logging.lastResort)
    logging.getLogger("mcrit").setLevel(logging.WARNING)


def _silence_progress_bars() -> None:
    import tqdm

    quiet = types.SimpleNamespace(tqdm=functools.partial(tqdm.tqdm, disable=True))
    for name in ("mcrit.Worker", "mcrit.matchers.MatcherInterface"):
        setattr(importlib.import_module(name), "tqdm", quiet)
