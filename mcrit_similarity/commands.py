"""Plugin commands."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

from mcrit_similarity import builtin, corpus
from mcrit_similarity.builtin import is_builtin
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.connection import client_for, resolve
from mcrit_similarity.errors import McritError
from mcrit_similarity.log import log_error, log_info
from mcrit_similarity.settings import global_defaults

COMMAND_NAME = "MCRIT Similarity\\Test Server Connection"
IMPORT_COMMAND = "MCRIT Similarity\\Import Corpus into Built-in Server..."
EXPORT_COMMAND = "MCRIT Similarity\\Export Built-in Corpus..."

_TITLE = "MCRIT Binary Similarity"


def check_connection(config: ProviderConfig) -> tuple[bool, str]:
    """Ask the server for its version, which mcritweb only answers for a valid token."""
    try:
        client = client_for(resolve(config))
        version = client.get_version()
    except McritError as exc:
        return False, str(exc)
    suffix = f" (MCRIT {version})" if version else ""
    where = "the built-in MCRIT server" if is_builtin(config.server) else client.mcrit_server
    return True, f"Connected to {where}{suffix}"


def import_corpus(config: ProviderConfig, path: str) -> tuple[bool, str]:
    try:
        report = corpus.import_file(client_for(resolve(config)), path)
    except (McritError, OSError) as exc:
        return False, f"Could not import {path}: {exc}"
    return True, corpus.import_summary(report, path)


def export_corpus(config: ProviderConfig, path: str) -> tuple[bool, str]:
    try:
        content = corpus.export_file(client_for(resolve(config)), path)
    except (McritError, OSError) as exc:
        return False, f"Could not export the corpus to {path}: {exc}"
    return True, (
        f"Saved {corpus.count(content.get('num_samples', 0), 'sample')} "
        f"({corpus.count(content.get('num_functions', 0), 'function')}) to {path}"
    )


def _same_file(path: str, other: str) -> bool:
    return bool(other) and os.path.abspath(os.path.expanduser(path)) == os.path.abspath(
        os.path.expanduser(other)
    )


def _tell(ok: bool, message: str) -> None:
    from binaryninja import MessageBoxIcon, execute_on_main_thread, show_message_box

    (log_info if ok else log_error)(message)
    icon = MessageBoxIcon.InformationIcon if ok else MessageBoxIcon.ErrorIcon
    execute_on_main_thread(lambda: show_message_box(_TITLE, message, icon=icon))


def _in_background(name: str, work: Callable[[], tuple[bool, str]]) -> None:
    """Run ``work`` off the UI thread; its outcome goes to the log and a message box."""

    def run() -> None:
        try:
            outcome = work()
        except McritError as exc:
            outcome = False, str(exc)
        except Exception as exc:
            outcome = False, f"MCRIT command failed: {type(exc).__name__}: {exc}"
        _tell(*outcome)

    threading.Thread(target=run, name=name, daemon=True).start()


def run_connection_check() -> None:
    config = global_defaults()
    _in_background("mcrit-connection-check", lambda: check_connection(config))


def _builtin_config() -> ProviderConfig | None:
    from binaryninja import MessageBoxIcon, show_message_box

    config = global_defaults()
    if is_builtin(config.server):
        return config
    show_message_box(
        _TITLE,
        f"The Server URL setting names {config.server}, not the built-in server. "
        "To import or export a server's corpus, use mcrit client import or export.",
        icon=MessageBoxIcon.WarningIcon,
    )
    return None


def run_corpus_import() -> None:
    from binaryninja import get_open_filename_input

    config = _builtin_config()
    if config is None:
        return
    path = get_open_filename_input("MCRIT export to import", "MCRIT export (*.json)")
    if path:
        _in_background("mcrit-corpus-import", lambda: import_corpus(config, path))


def run_corpus_export() -> None:
    from binaryninja import get_save_filename_input

    config = _builtin_config()
    if config is None:
        return
    path = get_save_filename_input("Save the built-in corpus as", "*.json", "mcrit-corpus.json")
    if path and _same_file(path, builtin.unloaded_corpus):
        from binaryninja import MessageBoxIcon, show_message_box

        show_message_box(
            _TITLE,
            f"The built-in server could not load {path} when it started, so it holds only this "
            "session's samples. Exporting there would replace that corpus; choose another file.",
            icon=MessageBoxIcon.WarningIcon,
        )
        return
    if path:
        _in_background("mcrit-corpus-export", lambda: export_corpus(config, path))
