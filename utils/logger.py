"""
utils/logger.py
---------------
Centralized logging setup with color support and per-agent log files.
Every agent decision is logged with its reasoning — full auditability.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

try:
    import colorlog
    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False

from config.settings import LOG_LEVEL, LOG_DIR


def get_logger(agent_name: str) -> logging.Logger:
    """
    Returns a logger for the given agent. Each agent gets:
    - Console output with color (if colorlog is installed)
    - A dedicated log file: logs/<agent_name>_<date>.log
    - The root pipeline log: logs/pipeline_<date>.log
    """
    logger = logging.getLogger(agent_name)

    # Avoid adding duplicate handlers on re-import
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    date_str = datetime.now().strftime("%Y%m%d")
    formatter_str = "%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s"
    date_fmt = "%H:%M:%S"

    # ── Console Handler ────────────────────────────────────────────────────────
    if HAS_COLORLOG:
        color_formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s | %(name)-30s | %(levelname)-8s%(reset)s | %(message)s",
            datefmt=date_fmt,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(color_formatter)
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter(formatter_str, datefmt=date_fmt))

    logger.addHandler(console_handler)

    # ── Agent-specific File Handler ────────────────────────────────────────────
    agent_log_path = LOG_DIR / f"{agent_name}_{date_str}.log"
    file_handler = logging.FileHandler(agent_log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(formatter_str, datefmt=date_fmt))
    logger.addHandler(file_handler)

    # ── Shared Pipeline Log ────────────────────────────────────────────────────
    pipeline_log_path = LOG_DIR / f"pipeline_{date_str}.log"
    pipeline_handler = logging.FileHandler(pipeline_log_path, encoding="utf-8")
    pipeline_handler.setFormatter(logging.Formatter(formatter_str, datefmt=date_fmt))
    logger.addHandler(pipeline_handler)

    return logger
