"""Logging utility for LP Agent with console and file output support."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


class Logger:
    """Configurable logger for LP Agent."""

    _logger: logging.Logger | None = None

    @classmethod
    def get_logger(cls) -> logging.Logger:
        """Retrieve or initialize the singleton logger."""
        if cls._logger is None:
            cls._logger = logging.getLogger("LPAgent")
            cls._logger.setLevel(logging.INFO)
            cls._logger.propagate = False

            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            # Console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            cls._logger.addHandler(console_handler)

            # Optional file handler in current directory or logs folder
            log_file = Path("lp_agent.log")
            try:
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
                file_handler.setFormatter(formatter)
                cls._logger.addHandler(file_handler)
            except Exception:
                pass

        return cls._logger

    @classmethod
    def info(cls, message: str) -> None:
        """Log informational message."""
        cls.get_logger().info(message)

    @classmethod
    def warning(cls, message: str) -> None:
        """Log warning message."""
        cls.get_logger().warning(message)

    @classmethod
    def error(cls, message: str) -> None:
        """Log error message."""
        cls.get_logger().error(message)

    @classmethod
    def debug(cls, message: str) -> None:
        """Log debug message."""
        cls.get_logger().debug(message)
