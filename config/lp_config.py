"""Central configuration dataclass for Loading Prints Agent (LP Agent).

Holds machine bounds, spacing parameters, Netfabb paths, and nesting strategies
for Flashforge WaxJet 51C production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StrategyMode(str, Enum):
    """Nesting strategy mode."""

    AUTO = "auto"
    NESTING_2D = "2d"
    PACKING_3D = "3d"


@dataclass
class LPConfig:
    """Central configuration for LP Agent."""

    # Target Hardware: Flashforge WaxJet 51C
    machine_name: str = "Flashforge WaxJet 51C"
    platform_x: float = 235.0  # Length (mm)
    platform_y: float = 138.0  # Width (mm)
    platform_z: float = 100.0  # Max Height (mm)

    # Nesting & Clearance Constraints
    clearance_buffer: float = 2.0  # Part-to-part distance (mm, min 1.5mm)
    border_spacing_xy: float = 3.0  # Distance from platform edges (mm)
    border_spacing_z: float = 0.5  # Base platform clearance (mm)
    voxel_size: float = 0.75  # Raster resolution (mm)
    avoid_interlocking: bool = True  # Ring shank / bail entanglement protection
    rotation_step_z_2d: int = 45  # Planar Z-rotation increments (degrees)

    # Strategy & Execution Mode
    mode: StrategyMode = StrategyMode.AUTO
    dry_run: bool = False
    batch_homogeneity_threshold: float = 0.70  # >= 70% same style triggers 3D packing

    # External Tool Paths
    netfabb_executable: str = r"C:\Program Files\Autodesk\Netfabb 2027\netfabb_console.exe"
    timeout_seconds: float = 180.0

    # Date Staging & Network Roots
    printing_root: str = r"Q:\Printing"
    fallback_printing_roots: list[str] = field(
        default_factory=lambda: [
            r"C:\Users\21824341\Desktop\Dev Projects\Q-2026\Printing",
        ]
    )

    @property
    def platform_area(self) -> float:
        """Usable 2D platform area in mm^2 (excluding borders)."""
        usable_x = max(0.0, self.platform_x - 2 * self.border_spacing_xy)
        usable_y = max(0.0, self.platform_y - 2 * self.border_spacing_xy)
        return usable_x * usable_y

    @property
    def build_volume(self) -> float:
        """Total build envelope volume in mm^3."""
        return self.platform_x * self.platform_y * self.platform_z

    def resolve_printing_root(self) -> Path:
        """Resolve accessible printing root directory, falling back to local replica."""
        primary = Path(self.printing_root)
        if primary.exists():
            return primary

        for fallback in self.fallback_printing_roots:
            fb = Path(fallback)
            if fb.exists():
                return fb

        return primary
