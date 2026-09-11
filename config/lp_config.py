"""Central configuration dataclass for Loading Prints Agent (LP Agent).

Holds machine bounds, spacing parameters, Netfabb paths, and nesting strategies
for Flashforge WaxJet 51C production.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Absolute floor for part-to-part spacing, in mm.
#
# 2.0 mm is the production default and 1.5 mm the usual safe minimum for prongs, but
# dense small-part plates are packed tighter deliberately, so the hard floor is set at the
# resolution the machine and the packer can actually honour rather than at process policy.
MIN_CLEARANCE_MM = 0.3


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
    clearance_buffer: float = 2.0  # Part-to-part distance (mm); floor is MIN_CLEARANCE_MM
    border_spacing_xy: float = 3.0  # Distance from platform edges (mm)
    border_spacing_z: float = 0.5  # Base platform clearance (mm)
    voxel_size: float = 0.75  # Raster resolution (mm)
    avoid_interlocking: bool = True  # Ring shank / bail entanglement protection
    rotation_step_z_2d: int = 45  # Planar Z-rotation increments (degrees)

    # 3D Packing (vertical stacking for repeat-production batches)
    #
    # Vertical clearance between stacked tiers. Distinct from border_spacing_z, which is
    # the base-platform standoff: this is the part-to-part gap the support wax has to fill
    # and later be washed out of, so it is wider than the XY gap by design (MASTER_PROMPT
    # 3.2 specifies >= 3.0 mm).
    layer_gap_z: float = 3.0

    # A file whose name asks for this many copies or more marks the batch as repeat
    # production, which is the case 3D packing exists for.
    quantity_3d_threshold: int = 3

    # Preferred ceiling for a stacked plate, in mm. The machine reaches platform_z (100),
    # but a tall stack is punishing to clean: support wax has to be washed out from between
    # every tier, and the deeper the stack the worse that job gets. 60 mm is the working
    # default, 90 mm the outer safe setting, 100 mm the physical limit.
    #
    # Applies to 3D packing only -- a 2D plate is a single tier and cannot approach it.
    max_plate_height_mm: float = 60.0

    # What to do when respecting max_plate_height_mm costs an extra plate:
    #   "ask"    - pause and let the operator choose (UI default)
    #   "split"  - respect the cap, accept more plates (unattended default)
    #   "exceed" - allow up to platform_z to save a plate, and warn
    #   "fail"   - refuse to pack and report
    on_cap_exceeded: str = "ask"

    # Strategy & Execution Mode
    mode: StrategyMode = StrategyMode.AUTO
    dry_run: bool = False
    batch_homogeneity_threshold: float = 0.70  # >= 70% same style triggers 3D packing

    # Build Plate Size & Mesh Decimation Budget (WaxJetPrint Memory Protection)
    # A full jewelry build plate should typically be between 150 MB and 600 MB, not 3.9 GB.
    # Decimating STLs by 70%-90% drops plate size to <500 MB without losing casting quality,
    # reducing WaxJetPrint RAM usage from 45 GB to ~6-8 GB and preventing out-of-memory crashes.
    min_recommended_plate_mb: float = 150.0
    max_recommended_plate_mb: float = 600.0
    max_safe_plate_mb: float = 1000.0
    mesh_decimation_target_percent: float = 75.0

    # External Tool Paths
    netfabb_executable: str = field(
        default_factory=lambda: os.environ.get(
            "NETFABB_CONSOLE_PATH",
            r"C:\Program Files\Autodesk\Netfabb 2027\netfabb_console.exe",
        )
    )

    # Netfabb Timeout Budget
    #
    # A fixed 180s budget was the direct cause of empty 1 KB plates in production:
    # real jewellery batches (31-50 multi-million-facet STLs) need 6-11 minutes inside
    # Netfabb, so every full-size batch timed out and fell through to the stub writer.
    # The budget now scales with the actual work being asked of the packer.
    timeout_base_seconds: float = 120.0  # Fixed cost: process start, Lua boot, tray init
    timeout_per_part_seconds: float = 25.0  # Marginal cost per part loaded and nested
    timeout_per_mb_seconds: float = 1.5  # Marginal cost per MB of mesh parsed
    timeout_max_seconds: float = 5400.0  # Hard ceiling (90 min) so a hung run still returns
    timeout_seconds: float = 180.0  # Legacy floor; kept so existing callers keep working

    # Fallback Behaviour
    #
    # When Netfabb is unavailable or fails, LP Agent packs the plate natively and writes a
    # REAL merged STL. It must never emit a placeholder plate that reports success -- an
    # operator cannot tell a stub from a build, and a stub reaches the printer as a no-op.
    allow_stub_plates: bool = False

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

    def compute_netfabb_timeout(self, part_count: int, total_bytes: int = 0) -> float:
        """Timeout budget (seconds) for one Netfabb nesting run of this size.

        Scales with part count and mesh volume, then clamps to
        [timeout_seconds, timeout_max_seconds]. See the timeout fields above for why
        a fixed budget is not safe here.
        """
        total_mb = max(0.0, total_bytes / (1024.0 * 1024.0))
        budget = (
            self.timeout_base_seconds
            + self.timeout_per_part_seconds * max(0, part_count)
            + self.timeout_per_mb_seconds * total_mb
        )
        return max(self.timeout_seconds, min(budget, self.timeout_max_seconds))

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
