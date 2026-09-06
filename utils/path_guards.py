"""Path safety guards and date-folder resolution utilities for LP Agent."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


class PathGuardViolation(ValueError):
    """Raised when an operation attempts to access an unauthorized path."""



def parse_date_spec(date_str: str | None = None) -> tuple[str, str]:
    """Parse date string into (month_name, dd_mm_yyyy).

    Accepts formats:
    - None (defaults to today)
    - "DD.MM.YYYY" (e.g. "06.09.2026")
    - "YYYY-MM-DD" (e.g. "2026-09-06")

    Returns:
        tuple[str, str]: (e.g. "September", "06.09.2026")
    """
    if not date_str:
        dt = datetime.now()
    else:
        date_str = date_str.strip()
        if "." in date_str:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        elif "-" in date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            raise ValueError(f"Invalid date format: '{date_str}'. Expected DD.MM.YYYY or YYYY-MM-DD.")

    month_name = dt.strftime("%B")
    dd_mm_yyyy = dt.strftime("%d.%m.%Y")
    return month_name, dd_mm_yyyy


def resolve_date_folder(printing_root: Path | str, date_str: str | None = None) -> Path:
    """Resolve full path to today's Agent staging folder.

    Hierarchy: <printing_root>/<Month>/<DD.MM.YYYY>/Agent/
    """
    root = Path(printing_root).resolve()
    month_name, dd_mm_yyyy = parse_date_spec(date_str)
    date_folder = root / month_name / dd_mm_yyyy / "Agent"
    return date_folder


def guard_output_path(target_path: Path | str, allowed_root: Path | str) -> Path:
    """Ensure target path resolves within the allowed root directory to prevent traversal."""
    resolved_target = Path(target_path).resolve()
    resolved_root = Path(allowed_root).resolve()

    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as err:
        raise PathGuardViolation(
            f"Security violation: Target path '{resolved_target}' resolves outside allowed root '{resolved_root}'."
        ) from err

    return resolved_target
