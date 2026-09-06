"""strategy_selector.py — Subagent Tactician: Strategy & Footprint Evaluator.

Parses STL geometry to extract AABB bounding boxes, calculates footprint area,
evaluates batch homogeneity, and selects 2D Flat Nesting vs 3D Packing for the WaxJet 51C.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.date_scanner import ScannedPart
from utils.logger import Logger


@dataclass
class BoundingBox:
    """Axis-Aligned Bounding Box (AABB)."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    @property
    def size_x(self) -> float:
        return max(0.0, self.max_x - self.min_x)

    @property
    def size_y(self) -> float:
        return max(0.0, self.max_y - self.min_y)

    @property
    def size_z(self) -> float:
        return max(0.0, self.max_z - self.min_z)

    @property
    def footprint_area_2d(self) -> float:
        """Raw 2D bounding area (mm^2)."""
        return self.size_x * self.size_y

    def buffered_footprint(self, clearance: float) -> float:
        """Footprint area including clearance buffer on all sides (mm^2)."""
        return (self.size_x + 2 * clearance) * (self.size_y + 2 * clearance)

    def fits_in_platform(self, max_x: float, max_y: float, max_z: float) -> bool:
        """Check if part fits within the platform envelope in at least one orthogonal orientation."""
        dims = sorted([self.size_x, self.size_y, self.size_z])
        envelope = sorted([max_x, max_y, max_z])
        return dims[0] <= envelope[0] and dims[1] <= envelope[1] and dims[2] <= envelope[2]


@dataclass
class PartGeometry:
    """Evaluated geometry attributes of a single STL."""

    part: ScannedPart
    bbox: BoundingBox
    triangle_count: int
    is_oversized: bool


@dataclass
class StrategyEvaluation:
    """Comprehensive strategy evaluation report for the batch."""

    selected_mode: StrategyMode
    total_parts: int
    oversized_parts: list[PartGeometry]
    valid_geometries: list[PartGeometry]
    total_buffered_footprint_mm2: float
    platform_usable_area_mm2: float
    footprint_utilization_ratio: float
    batch_homogeneity: float
    estimated_plates_required: int
    reason: str


class StrategySelector:
    """Subagent Tactician: Calculates AABB footprint and selects nesting mode."""

    def __init__(self, config: LPConfig):
        self.config = config

    def evaluate_batch(self, scanned_parts: list[ScannedPart]) -> StrategyEvaluation:
        """Analyze batch geometries and determine nesting mode."""
        valid_geometries: list[PartGeometry] = []
        oversized: list[PartGeometry] = []

        for part in scanned_parts:
            try:
                bbox, triangle_count = self.calculate_stl_aabb(part.file_path)
                fits = bbox.fits_in_platform(
                    self.config.platform_x,
                    self.config.platform_y,
                    self.config.platform_z,
                )
                geom = PartGeometry(
                    part=part,
                    bbox=bbox,
                    triangle_count=triangle_count,
                    is_oversized=not fits,
                )
                if fits:
                    valid_geometries.append(geom)
                else:
                    Logger.warning(
                        f"[TACTICIAN] Part '{part.file_path.name}' exceeds WaxJet 51C envelope "
                        f"({bbox.size_x:.1f} x {bbox.size_y:.1f} x {bbox.size_z:.1f} mm)"
                    )
                    oversized.append(geom)
            except Exception as err:
                Logger.error(f"[TACTICIAN] Failed to parse STL geometry for {part.file_path.name}: {err}")
                # Treat unparseable file as oversized/exception
                dummy_bbox = BoundingBox(0, 0, 0, 0, 0, 0)
                oversized.append(PartGeometry(part=part, bbox=dummy_bbox, triangle_count=0, is_oversized=True))

        total_buffered_footprint = sum(
            g.bbox.buffered_footprint(self.config.clearance_buffer) for g in valid_geometries
        )
        usable_area = self.config.platform_area
        ratio = total_buffered_footprint / usable_area if usable_area > 0 else 0.0

        # Homogeneity calculation: ratio of most common design stem or repeated styles
        homogeneity = self._calculate_homogeneity([g.part for g in valid_geometries])

        # Estimated plates (2D)
        estimated_plates = max(1, int(total_buffered_footprint // usable_area) + 1)

        # Mode determination
        selected_mode, reason = self._select_mode(ratio, homogeneity)

        Logger.info(
            f"[TACTICIAN] Strategy: {selected_mode.value.upper()} | "
            f"Footprint: {total_buffered_footprint:.0f} mm^2 ({ratio*100:.1f}% plate area) | "
            f"Homogeneity: {homogeneity*100:.1f}% | Reason: {reason}"
        )

        return StrategyEvaluation(
            selected_mode=selected_mode,
            total_parts=len(scanned_parts),
            oversized_parts=oversized,
            valid_geometries=valid_geometries,
            total_buffered_footprint_mm2=total_buffered_footprint,
            platform_usable_area_mm2=usable_area,
            footprint_utilization_ratio=ratio,
            batch_homogeneity=homogeneity,
            estimated_plates_required=estimated_plates,
            reason=reason,
        )

    def _select_mode(self, footprint_ratio: float, homogeneity: float) -> tuple[StrategyMode, str]:
        """Determine strategy based on user configuration and geometric metrics."""
        if self.config.mode == StrategyMode.NESTING_2D:
            return StrategyMode.NESTING_2D, "Explicitly configured to 2D Flat Nesting"
        if self.config.mode == StrategyMode.PACKING_3D:
            return StrategyMode.PACKING_3D, "Explicitly configured to 3D Packing"

        # AUTO mode logic
        if footprint_ratio <= 1.05:
            return (
                StrategyMode.NESTING_2D,
                f"Batch footprint fits flat on platform ({footprint_ratio*100:.1f}% area). "
                "2D Flat Nesting minimizes Z-height and print time.",
            )

        if homogeneity >= self.config.batch_homogeneity_threshold:
            return (
                StrategyMode.PACKING_3D,
                f"Batch volume exceeds 1 plate ({footprint_ratio*100:.1f}%) and is homogeneous "
                f"({homogeneity*100:.1f}% same design). 3D Packing activated for volume density.",
            )

        return (
            StrategyMode.NESTING_2D,
            f"Batch footprint exceeds 1 plate ({footprint_ratio*100:.1f}%) for mixed daily orders. "
            "Using 2D Flat Nesting with multi-plate distribution to protect jewelry prongs.",
        )

    def _calculate_homogeneity(self, parts: list[ScannedPart]) -> float:
        """Calculate batch homogeneity (0.0 to 1.0) based on base design keys."""
        if not parts:
            return 0.0
        counts: dict[str, int] = {}
        for p in parts:
            counts[p.base_design_key] = counts.get(p.base_design_key, 0) + 1
        max_count = max(counts.values()) if counts else 0
        return max_count / len(parts)

    @staticmethod
    def calculate_stl_aabb(stl_path: Path) -> tuple[BoundingBox, int]:
        """Parse binary STL file and calculate exact Axis-Aligned Bounding Box.

        Binary STL format:
        - 80 bytes: Header
        - 4 bytes: uint32 triangle count (N)
        - N * 50 bytes: Each triangle has 12-byte normal, 3 * 12-byte vertices, 2-byte attribute.
        """
        with open(stl_path, "rb") as f:
            header = f.read(80)
            if len(header) < 80:
                raise ValueError("STL file smaller than 80 bytes")

            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                raise ValueError("STL missing triangle count")

            triangle_count = struct.unpack("<I", count_bytes)[0]
            expected_size = 84 + triangle_count * 50
            actual_size = stl_path.stat().st_size

            # Fallback check if file is ASCII
            if actual_size != expected_size and header.strip().startswith(b"solid"):
                return StrategySelector._parse_ascii_stl_aabb(stl_path)

            min_x = min_y = min_z = float("inf")
            max_x = max_y = max_z = float("-inf")

            # Read facets in chunks of 4096 facets (204,800 bytes) for speed
            chunk_facets = 4096
            chunk_bytes = chunk_facets * 50

            for _ in range(0, triangle_count, chunk_facets):
                data = f.read(chunk_bytes)
                num_facets = len(data) // 50
                for i in range(num_facets):
                    offset = i * 50 + 12  # Skip 12-byte normal vector
                    # Read 3 vertices (each 3 floats = 9 floats = 36 bytes)
                    v = struct.unpack("<9f", data[offset : offset + 36])
                    xs = (v[0], v[3], v[6])
                    ys = (v[1], v[4], v[7])
                    zs = (v[2], v[5], v[8])

                    v_min_x, v_max_x = min(xs), max(xs)
                    v_min_y, v_max_y = min(ys), max(ys)
                    v_min_z, v_max_z = min(zs), max(zs)

                    min_x = min(min_x, v_min_x)
                    max_x = max(max_x, v_max_x)
                    min_y = min(min_y, v_min_y)
                    max_y = max(max_y, v_max_y)
                    min_z = min(min_z, v_min_z)
                    max_z = max(max_z, v_max_z)

            if min_x == float("inf"):
                min_x = max_x = min_y = max_y = min_z = max_z = 0.0

            return BoundingBox(min_x, max_x, min_y, max_y, min_z, max_z), triangle_count

    @staticmethod
    def _parse_ascii_stl_aabb(stl_path: Path) -> tuple[BoundingBox, int]:
        """Fallback parser for ASCII STL format."""
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        vertex_count = 0

        with open(stl_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 4 and parts[0].lower() == "vertex":
                    try:
                        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                        min_x = min(min_x, x)
                        max_x = max(max_x, x)
                        min_y = min(min_y, y)
                        max_y = max(max_y, y)
                        min_z = min(min_z, z)
                        max_z = max(max_z, z)
                        vertex_count += 1
                    except ValueError:
                        continue

        triangles = vertex_count // 3
        if min_x == float("inf"):
            min_x = max_x = min_y = max_y = min_z = max_z = 0.0

        return BoundingBox(min_x, max_x, min_y, max_y, min_z, max_z), triangles
