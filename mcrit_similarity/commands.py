"""Plugin commands."""

from __future__ import annotations

import threading

from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.errors import McritError
from mcrit_similarity.log import log_error, log_info
from mcrit_similarity.mcrit.client import McritClient
from mcrit_similarity.settings import global_defaults

COMMAND_NAME = "MCRIT Similarity\\Test Server Connection"


def check_connection(config: ProviderConfig) -> tuple[bool, str]:
    """Ask the server for its version, which mcritweb only answers for a valid token."""
    try:
        client = McritClient(
            config.server,
            apitoken=config.api_token or None,
            username=config.username or None,
            timeout=config.timeout,
        )
        version = client.get_version()
    except McritError as exc:
        return False, str(exc)
    suffix = f" (MCRIT {version})" if version else ""
    return True, f"Connected to {client.mcrit_server}{suffix}"


def run_connection_check(_view=None) -> None:
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
