"""
Structured logging utilities.
Writes human-readable Rich output to stdout and JSONL audit traces to disk.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

console = Console()

# ---------------------------------------------------------------------------
# Standard Python logger (uses Rich for pretty terminal output)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)],
)

logger = logging.getLogger("cartographer")


def get_logger(name: str = "cartographer") -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Cartography trace (JSONL audit log)
# ---------------------------------------------------------------------------


class CartographyTracer:
    """
    Append-only JSONL audit log.  Every agent action, confidence level, and
    evidence source is recorded here – the Week-1 audit pattern applied to
    intelligence gathering.
    """

    def __init__(self, trace_path: Path) -> None:
        self.trace_path = trace_path
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate on new run
        self.trace_path.write_text("")

    def log(
        self,
        agent: str,
        action: str,
        target: str = "",
        evidence_source: str = "static_analysis",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": agent,
            "action": action,
            "target": target,
            "evidence_source": evidence_source,  # "static_analysis" | "llm_inference" | "git"
            "confidence": confidence,
            "metadata": metadata or {},
        }
        with self.trace_path.open("a") as f:
            f.write(json.dumps(record) + "\n")


# Module-level singleton; replaced by orchestrator once output path is known.
_tracer: CartographyTracer | None = None


def init_tracer(trace_path: Path) -> CartographyTracer:
    global _tracer
    _tracer = CartographyTracer(trace_path)
    return _tracer


def get_tracer() -> CartographyTracer | None:
    return _tracer