"""
Structured logging: console handler (INFO by default, DEBUG with --verbose)
+ daily-rotating file handler (always DEBUG, kept 14 days).
"""
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

import config

_initialized = False
_console_handler: logging.Handler | None = None


def _init_handlers() -> None:
    global _initialized, _console_handler
    if _initialized:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture all; individual handlers filter

    # Console: INFO by default
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    _console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"
        )
    )
    root.addHandler(_console_handler)

    # File: always DEBUG, daily rotation, keep 14 days
    log_file = config.LOGS_DIR / f"pipeline_{datetime.now():%Y%m%d}.log"
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, initializing handlers on first call."""
    _init_handlers()
    return logging.getLogger(name)


def set_verbose(verbose: bool) -> None:
    """Switch console handler: DEBUG (verbose=True) or back to config LOG_LEVEL."""
    _init_handlers()
    if _console_handler is not None:
        level = (
            logging.DEBUG
            if verbose
            else getattr(logging, config.LOG_LEVEL, logging.INFO)
        )
        _console_handler.setLevel(level)
