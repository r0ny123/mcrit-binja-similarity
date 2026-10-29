"""Plugin commands."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from mcrit_similarity import corpus
from mcrit_similarity.builtin import is_builtin
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.connection import client_for, resolve
from mcrit_similarity.errors import McritError
from mcrit_similarity.log import log_error, log_info, log_warn
from mcrit_similarity.mcrit.client import is_loopback
from mcrit_similarity.mcrit.samples import ensure_sample
from mcrit_similarity.settings import global_defaults

COMMAND_NAME = "MCRIT Similarity\\Test Server Connection"
IMPORT_COMMAND = "MCRIT Similarity\\Import Corpus into Built-in Server..."
EXPORT_COMMAND = "MCRIT Similarity\\Export Built-in Corpus..."
ADD_COMMAND = "MCRIT Similarity\\Add Binary to Corpus..."
MATCHES_COMMAND = "MCRIT Similarity\\Show Corpus Matches"

_TITLE = "MCRIT Binary Similarity"


def _where(config: ProviderConfig) -> str:
    return "the built-in MCRIT server" if is_builtin(config.server) else config.server


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


def add_to_corpus(
    config: ProviderConfig, report: Any, family: str, version: str
) -> tuple[bool, str]:
    try:
        sample = ensure_sample(
            client_for(resolve(config)), report, persist=True, family=family, version=version
        )
    except McritError as exc:
        return False, str(exc)
    if sample.existed:
        return True, (
            f"{_where(config)} already holds this binary as sample {sample.sample_id}; "
            "MCRIT keeps its family and version"
        )
    return True, f"Added this binary to {_where(config)} as sample {sample.sample_id}"


def function_matches(
    config: ProviderConfig, report: Any, function: Any
) -> list[corpus.CorpusMatch]:
    resolved = resolve(config)
    if not is_loopback(resolved.server):
        log_warn(
            f"Sending the disassembly of the function at {function.offset:#x} to {config.server}"
        )
    client = client_for(resolved)
    result = client.query_function(
        corpus.single_function_report(report, function),
        minhash_threshold=config.minhash_threshold,
        pichash_size=config.pichash_size,
        band_matches_required=config.band_matches_required,
    )
    return corpus.corpus_matches(result, client, report.sha256)


def _tell(ok: bool, message: str) -> None:
    from binaryninja import MessageBoxIcon, execute_on_main_thread, show_message_box

    (log_info if ok else log_error)(message)
    icon = MessageBoxIcon.InformationIcon if ok else MessageBoxIcon.ErrorIcon
    execute_on_main_thread(lambda: show_message_box(_TITLE, message, icon=icon))


def _in_background(name: str, work: Callable[[], tuple[bool, str] | None]) -> None:
    """Run ``work`` off the UI thread; its outcome, if any, goes to the log and a message box."""

    def run() -> None:
        try:
            outcome = work()
        except McritError as exc:
            outcome = False, str(exc)
        except Exception as exc:
            outcome = False, f"MCRIT command failed: {type(exc).__name__}: {exc}"
        if outcome is not None:
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
    if path:
        _in_background("mcrit-corpus-export", lambda: export_corpus(config, path))


def run_add_binary(bv) -> None:
    from binaryninja import LabelField, TextLineField, get_form_input

    from mcrit_similarity.export import export_smda_report

    config = global_defaults()
    family = TextLineField("Family")
    version = TextLineField("Version")
    fields = [
        LabelField(f"Uploads this binary's disassembly to {_where(config)}."),
        family,
        version,
    ]
    if not get_form_input(fields, "Add Binary to MCRIT Corpus"):
        return
    labels = (str(family.result or "").strip(), str(version.result or "").strip())
    _in_background(
        "mcrit-add-binary", lambda: add_to_corpus(config, export_smda_report(bv), *labels)
    )


def run_corpus_matches(bv, function) -> None:
    config = global_defaults()
    name, address = function.name, function.start

    def work() -> tuple[bool, str] | None:
        from binaryninja import execute_on_main_thread

        from mcrit_similarity.export import export_smda_report
        from mcrit_similarity.render import smda_function

        report = export_smda_report(bv)
        found = smda_function(report, address)
        if found is None:
            return False, f"The SMDA export of this binary has no function at {address:#x}"
        too_small = corpus.too_small(name, found)
        if too_small:
            return True, too_small
        matches = function_matches(config, report, found)
        markdown = corpus.matches_markdown(name, address, matches, _where(config))
        execute_on_main_thread(
            lambda: bv.show_markdown_report(f"MCRIT matches: {name}", markdown, markdown)
        )
        return None

    _in_background("mcrit-corpus-matches", work)
