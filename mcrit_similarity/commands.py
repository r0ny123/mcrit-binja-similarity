"""Plugin commands."""

from __future__ import annotations

import threading

from mcrit_similarity.builtin import is_builtin
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.connection import client_for, resolve
from mcrit_similarity.errors import McritError
from mcrit_similarity.log import log_error, log_info
from mcrit_similarity.settings import global_defaults

COMMAND_NAME = "MCRIT Similarity\\Test Server Connection"


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


def run_connection_check() -> None:
    """Check the configured server off the UI thread and report in the log and a message box."""

    def run() -> None:
        from binaryninja import MessageBoxIcon, execute_on_main_thread, show_message_box

        ok, message = check_connection(global_defaults())
        (log_info if ok else log_error)(message)
        icon = MessageBoxIcon.InformationIcon if ok else MessageBoxIcon.ErrorIcon
        execute_on_main_thread(
            lambda: show_message_box("MCRIT Binary Similarity", message, icon=icon)
        )

    threading.Thread(target=run, name="mcrit-connection-check", daemon=True).start()
