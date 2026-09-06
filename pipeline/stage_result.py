"""stage_result.py — Structured execution result models for LP Agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.plate_distributor import PlatePackage
from core.strategy_selector import StrategyEvaluation


@dataclass
class PipelineExecutionSummary:
    """End-to-end execution summary of LP Agent."""

    success: bool
    date_str: str
    target_directory: Path
    mode_selected: str
    total_scanned_parts: int
    total_valid_parts: int
    total_quarantined: int
    total_plates_generated: int
    plates: list[PlatePackage] = field(default_factory=list)
    strategy_eval: StrategyEvaluation | None = None
    manifest_path: Path | None = None
    error_message: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime = field(default_factory=datetime.now)

    @property
    def duration_seconds(self) -> float:
        """Total run duration in seconds."""
        return (self.completed_at - self.started_at).total_seconds()
