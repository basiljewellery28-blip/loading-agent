"""strategy_selector.py — Subagent Tactician: Strategy & Footprint Evaluator.

Parses STL geometry to extract AABB bounding boxes, calculates footprint area,
evaluates batch homogeneity, and selects 2D Flat Nesting vs 3D Packing for the WaxJet 51C.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.date_scanner import ScannedPart
from utils.logger import Logger

ProgressFn = Callable[[str, float], None]


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
    # Share of parts coming from xN repeat-production files; drives the 3D decision.
    repeat_ratio: float = 0.0


class StrategySelector:
    """Subagent Tactician: Calculates AABB footprint and selects nesting mode."""

    def __init__(self, config: LPConfig):
        self.config = config

    def evaluate_batch(
        self,
        scanned_parts: list[ScannedPart],
        progress: ProgressFn | None = None,
    ) -> StrategyEvaluation:
        """Analyze batch geometries and determine nesting mode."""
        valid_geometries: list[PartGeometry] = []
        oversized: list[PartGeometry] = []
        total = len(scanned_parts)

        for idx, part in enumerate(scanned_parts):
            Logger.info(f"[TACTICIAN] Evaluating geometry [{idx + 1}/{total}]: {part.file_path.name}")
            if progress and total:
                progress(f"Measuring {part.file_path.name}", idx / total)
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

        # Weight each part by the quantity its filename asks for: an "x16" file occupies
        # sixteen footprints on the plate, and ignoring that under-counts the batch badly
        # enough to pick the wrong strategy and the wrong plate count.
        from core.quantity import parse_quantity

        total_buffered_footprint = sum(
            g.bbox.buffered_footprint(self.config.clearance_buffer)
            * parse_quantity(g.part.file_path.name)
            for g in valid_geometries
        )
        usable_area = self.config.platform_area
        ratio = total_buffered_footprint / usable_area if usable_area > 0 else 0.0

        # Homogeneity calculation: ratio of most common design stem or repeated styles
        homogeneity = self._calculate_homogeneity([g.part for g in valid_geometries])

        # Share of the batch that comes from repeat-production files -- names carrying an
        # xN multiplier at or above the threshold. This is what distinguishes an RPM run
        # from a mixed daily order, and it is the signal 3D packing exists to serve.
        repeat_ratio = self._calculate_repeat_ratio(valid_geometries)

        # Estimated plates (2D)
        estimated_plates = max(1, int(total_buffered_footprint // usable_area) + 1)

        # Mode determination
        selected_mode, reason = self._select_mode(ratio, homogeneity, repeat_ratio)

        Logger.info(
            f"[TACTICIAN] Strategy: {selected_mode.value.upper()} | "
            f"Footprint: {total_buffered_footprint:.0f} mm^2 ({ratio*100:.1f}% plate area) | "
            f"Homogeneity: {homogeneity*100:.1f}% | Repeat units: {repeat_ratio*100:.1f}% | "
            f"Reason: {reason}"
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
            repeat_ratio=repeat_ratio,
        )

    def _calculate_repeat_ratio(self, geometries: list[PartGeometry]) -> float:
        """Fraction of the batch's parts that come from repeat-production files.

        Counted in parts, not files: one "...-PP X16" file contributes sixteen parts and
        should weigh accordingly against a handful of one-off bespoke pieces.
        """
        from core.quantity import parse_quantity

        total_parts = 0
        repeat_parts = 0
        for geom in geometries:
            qty = parse_quantity(geom.part.file_path.name)
            total_parts += qty
            if qty >= self.config.quantity_3d_threshold:
                repeat_parts += qty

        return repeat_parts / total_parts if total_parts else 0.0

    def _select_mode(
        self,
        footprint_ratio: float,
        homogeneity: float,
        repeat_ratio: float = 0.0,
    ) -> tuple[StrategyMode, str]:
        """Determine strategy based on user configuration and geometric metrics."""
        if self.config.mode == StrategyMode.NESTING_2D:
            return StrategyMode.NESTING_2D, "Explicitly configured to 2D Flat Nesting"
        if self.config.mode == StrategyMode.PACKING_3D:
            return StrategyMode.PACKING_3D, "Explicitly configured to 3D Packing"

        # AUTO mode logic.
        #
        # Repeat production is checked before the footprint test. A batch dominated by
        # xN multiplied units is an RPM run, which is exactly what 3D packing is for --
        # and choosing it costs nothing when the batch is small, because the packer fills
        # one tier completely before ever opening a second.
        if repeat_ratio >= self.config.batch_homogeneity_threshold:
            return (
                StrategyMode.PACKING_3D,
                f"Repeat production batch ({repeat_ratio*100:.0f}% of parts come from "
                f"x{self.config.quantity_3d_threshold}+ multiplied files). 3D Packing "
                "stacks the units to maximise plate yield.",
            )

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

    # Keyed on (filename, size_bytes, mtime_ns). See calculate_stl_aabb for why mtime is
    # part of the key rather than name and size alone.
    _AABB_CACHE: dict[tuple[str, int, int], tuple[BoundingBox, int]] = {}
    _CACHE_LOADED: bool = False
    _CACHE_FILE: Path = Path(__file__).resolve().parent.parent / ".cache" / "aabb_cache.json"

    @classmethod
    def _ensure_cache_loaded(cls) -> None:
        if cls._CACHE_LOADED:
            return
        cls._CACHE_LOADED = True
        try:
            if cls._CACHE_FILE.exists():
                with open(cls._CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key, val in data.items():
                        # "<name>:<size>:<mtime_ns>". Entries written before mtime became
                        # part of the key are skipped rather than trusted -- they cannot be
                        # told apart from a file that has since been rewritten.
                        parts = key.rsplit(":", 2)
                        if len(parts) != 3:
                            continue
                        try:
                            name, size, mtime = parts[0], int(parts[1]), int(parts[2])
                        except ValueError:
                            continue
                        b = val["bbox"]
                        bbox = BoundingBox(
                            b["min_x"], b["max_x"], b["min_y"], b["max_y"], b["min_z"], b["max_z"]
                        )
                        cls._AABB_CACHE[(name, size, mtime)] = (bbox, val["triangles"])
        except Exception as e:
            Logger.debug(f"[TACTICIAN] Could not load persistent AABB cache: {e}")

    @classmethod
    def _save_cache_entry(
        cls, cache_key: tuple[str, int, int], bbox: BoundingBox, triangles: int
    ) -> None:
        try:
            cls._CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if cls._CACHE_FILE.exists():
                try:
                    with open(cls._CACHE_FILE, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}
            k_str = f"{cache_key[0]}:{cache_key[1]}:{cache_key[2]}"
            existing[k_str] = {
                "bbox": {
                    "min_x": bbox.min_x,
                    "max_x": bbox.max_x,
                    "min_y": bbox.min_y,
                    "max_y": bbox.max_y,
                    "min_z": bbox.min_z,
                    "max_z": bbox.max_z,
                },
                "triangles": triangles,
            }
            with open(cls._CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            Logger.debug(f"[TACTICIAN] Could not save persistent AABB cache: {e}")

    @classmethod
    def calculate_stl_aabb(cls, stl_path: Path) -> tuple[BoundingBox, int]:
        """Parse binary STL file and calculate exact Axis-Aligned Bounding Box.

        Binary STL format:
        - 80 bytes: Header
        - 4 bytes: uint32 triangle count (N)
        - N * 50 bytes: Each triangle has 12-byte normal, 3 * 12-byte vertices, 2-byte attribute.
        """
        cls._ensure_cache_loaded()
        try:
            stat = stl_path.stat()
            actual_size = stat.st_size
            # Modification time is part of the key on purpose. Keyed on name and size
            # alone, a file rewritten in place at the same byte count returns the previous
            # file's geometry -- and merged_plate_NN.stl is regenerated under the same name
            # on every run, very often at an identical size when the same parts are
            # repacked. That is a silently wrong bounding box, so mtime disambiguates it.
            mtime = int(stat.st_mtime_ns)
        except OSError:
            actual_size = 0
            mtime = 0

        cache_key = (stl_path.name, actual_size, mtime)
        if cache_key in cls._AABB_CACHE:
            return cls._AABB_CACHE[cache_key]

        with open(stl_path, "rb", buffering=2 * 1024 * 1024) as f:
            header = f.read(80)
            if len(header) < 80:
                raise ValueError("STL file smaller than 80 bytes")

            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                raise ValueError("STL missing triangle count")

            triangle_count = struct.unpack("<I", count_bytes)[0]
            expected_size = 84 + triangle_count * 50

            # Fallback check if file is ASCII
            if actual_size != expected_size and header.strip().startswith(b"solid"):
                res = cls._parse_ascii_stl_aabb(stl_path)
                cls._AABB_CACHE[cache_key] = res
                return res

            min_x = min_y = min_z = float("inf")
            max_x = max_y = max_z = float("-inf")

            # Read facets in chunks of 65536 facets (3.2 MB) for speed
            chunk_facets = 65536
            chunk_bytes = chunk_facets * 50

            for _ in range(0, triangle_count, chunk_facets):
                data = f.read(chunk_bytes)
                if not data:
                    break
                # Only unpack complete 50-byte facet records
                valid_len = (len(data) // 50) * 50
                if valid_len == 0:
                    break
                for x1, y1, z1, x2, y2, z2, x3, y3, z3 in struct.iter_unpack("<12x9f2x", data[:valid_len]):
                    if x1 < min_x: min_x = x1
                    if x2 < min_x: min_x = x2
                    if x3 < min_x: min_x = x3
                    if x1 > max_x: max_x = x1
                    if x2 > max_x: max_x = x2
                    if x3 > max_x: max_x = x3

                    if y1 < min_y: min_y = y1
                    if y2 < min_y: min_y = y2
                    if y3 < min_y: min_y = y3
                    if y1 > max_y: max_y = y1
                    if y2 > max_y: max_y = y2
                    if y3 > max_y: max_y = y3

                    if z1 < min_z: min_z = z1
                    if z2 < min_z: min_z = z2
                    if z3 < min_z: min_z = z3
                    if z1 > max_z: max_z = z1
                    if z2 > max_z: max_z = z2
                    if z3 > max_z: max_z = z3

            if min_x == float("inf"):
                min_x = max_x = min_y = max_y = min_z = max_z = 0.0

        res = (BoundingBox(min_x, max_x, min_y, max_y, min_z, max_z), triangle_count)
        cls._AABB_CACHE[cache_key] = res
        cls._save_cache_entry(cache_key, res[0], res[1])
        return res

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
