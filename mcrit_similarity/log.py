"""Logging that works inside Binary Ninja and in unit tests."""

from __future__ import annotations

import logging

from mcrit_similarity import LOGGER_NAME

_python_logger = logging.getLogger(LOGGER_NAME)


def _bn_logger():
    try:
        from binaryninja import Logger

        return Logger(0, LOGGER_NAME)
    except Exception:
        return None


def _log(bn_method: str, level: int, message: str) -> None:
    logger = _bn_logger()
    if logger is not None:
        getattr(logger, bn_method)(message)
    else:
        _python_logger.log(level, message)


def log_info(message: str) -> None:
    _log("log_info", logging.INFO, message)


def log_warn(message: str) -> None:
    _log("log_warn", logging.WARNING, message)


def log_error(message: str) -> None:
    _log("log_error", logging.ERROR, message)


def log_debug(message: str) -> None:
    _log("log_debug", logging.DEBUG, message)
