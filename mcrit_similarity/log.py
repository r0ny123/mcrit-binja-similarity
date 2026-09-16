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


def log_info(message: str) -> None:
    logger = _bn_logger()
    if logger is not None:
        logger.log_info(message)
    else:
        _python_logger.info(message)


def log_warn(message: str) -> None:
    logger = _bn_logger()
    if logger is not None:
        logger.log_warn(message)
    else:
        _python_logger.warning(message)


def log_error(message: str) -> None:
    logger = _bn_logger()
    if logger is not None:
        logger.log_error(message)
    else:
        _python_logger.error(message)


def log_debug(message: str) -> None:
    logger = _bn_logger()
    if logger is not None:
        logger.log_debug(message)
    else:
        _python_logger.debug(message)
