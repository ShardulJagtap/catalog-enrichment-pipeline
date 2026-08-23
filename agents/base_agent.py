"""
agents/base_agent.py
--------------------
Abstract base class for all pipeline agents.
Enforces a consistent interface and provides shared logging + timing utilities.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from utils.logger import get_logger


class BaseAgent(ABC):
    """
    Every agent inherits from this class and implements `run()`.

    Design principle:
    - Each agent has a single, clearly scoped responsibility.
    - Agents log their decisions with reasoning, not just outputs.
    - Agents never raise exceptions that crash the pipeline; they flag
      issues and return partial results where possible.
    """

    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)
        self._stats: Dict[str, Any] = {
            "processed": 0,
            "flagged": 0,
            "errors": 0,
            "duration_s": 0.0,
        }

    @abstractmethod
    def run(self, data: Any) -> Any:
        """
        Execute the agent's core logic.

        Args:
            data: Input data — type varies per agent.

        Returns:
            Processed output — type varies per agent.
        """
        ...

    def execute(self, data: Any) -> Any:
        """
        Wrapper around `run()` that adds timing, entry/exit logging,
        and top-level error handling.
        """
        agent_name = self.__class__.__name__
        self.logger.info("▶ %s starting", agent_name)
        start = time.perf_counter()
        try:
            result = self.run(data)
            elapsed = time.perf_counter() - start
            self._stats["duration_s"] = round(elapsed, 3)
            self.logger.info(
                "✔ %s completed in %.3fs | stats=%s",
                agent_name, elapsed, self._stats
            )
            return result
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self._stats["errors"] += 1
            self._stats["duration_s"] = round(elapsed, 3)
            self.logger.error(
                "✘ %s failed after %.3fs: %s",
                agent_name, elapsed, exc, exc_info=True
            )
            raise

    def get_stats(self) -> Dict[str, Any]:
        """Return execution statistics for the reporting agent."""
        return {self.__class__.__name__: self._stats.copy()}
