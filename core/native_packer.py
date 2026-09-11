"""native_packer.py — Subagent Vulcan's in-process fallback packer and STL merger.

Netfabb TrueShape nesting is still the preferred packer: it nests on real outlines, so
it fits materially more jewellery per plate than the shelf packer here. This module is
what runs when Netfabb is unavailable, times out, or packs nothing -- and its job is to
produce a *real* build plate, not a placeholder.

That distinction is the whole point of this file. The previous fallback wrote a 49-byte
stub::

    solid simulated_plate
    endsolid simulated_plate

and reported success, so `plate_manifest.json` said "Verified: PASS" for a plate holding
no geometry. An operator could not tell that apart from a real build until it reached the
printer. Everything here therefore packs against the true platform envelope, spills
genuine leftovers onto the next plate, and writes a real binary STL with the parts
actually transformed into their packed positions.

Pure stdlib on purpose -- the deployed venv (C:\\cadprod_venv) has no numpy, and this
path must work on a machine where the Autodesk toolchain is already broken.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.quantity import PartInstance, as_instances
from core.strategy_selector import BoundingBox, StrategySelector
from utils.logger import Logger

# Binary STL facet: 3 normal floats + 9 vertex floats + 2 attribute bytes = 50 bytes.
_FACET_READ = struct.Struct("<12f2x")
_FACET_WRITE = struct.Struct("<12fH")
_FACET_BYTES = 50
_STL_HEADER_BYTES = 84  # 80-byte header + uint32 facet count

ProgressFn = Callable[[str, float], None]


@dataclass
class PlacedPart:
    """One part committed to a plate, with the transform that puts it there.

    `offset` is applied *after* `rot_deg`, so the reconstruction order in
    `write_merged_stl` is rotate-then-translate. The AABB fields are the final
    platform-space bounds and are what Sentry verifies against the envelope.
    """

    name: str
    source: Path
    rot_deg: int
    offset: tuple[float, float, float]
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    triangle_count: int = 0

    def as_audit_dict(self) -> dict:
        """Serialise to the same shape Netfabb's Lua audit writes, so downstream
        consumers (Sentry, the manifest, the UI viewport) need no special case."""
        return {
            "name": self.name,
            "min_x": round(self.min_x, 4),
            "max_x": round(self.max_x, 4),
            "min_y": round(self.min_y, 4),
            "max_y": round(self.max_y, 4),
            "min_z": round(self.min_z, 4),
            "max_z": round(self.max_z, 4),
            "rot_deg": self.rot_deg,
            "triangles": self.triangle_count,
        }


@dataclass
class NativePackResult:
    """Outcome of one native packing pass over a candidate pool."""

    placed: list[PlacedPart] = field(default_factory=list)
    leftover: list[PartInstance] = field(default_factory=list)
    oversize: list[PartInstance] = field(default_factory=list)
    # Tallest column on the plate, in units. 1 means nothing is stacked.
    layers_used: int = 1
    # How many vertical stacks the plate holds. In 3D a design's copies share a column, so
    # this is the number of XY footprints actually occupied.
    column_count: int = 0
    # The Z ceiling this plan was packed against, so a caller comparing two plans can say
    # which cap produced which outcome.
    height_limit_mm: float = 0.0

    @property
    def packed_names(self) -> list[str]:
        return [p.name for p in self.placed]

    @property
    def plate_height_mm(self) -> float:
        """Tallest point on the plate.

        The WaxJet carriage sweeps the whole platform on every pass, so print time tracks
        this number almost exclusively -- adding parts across a tier is nearly free, adding
        a tier is not. It is the single most useful figure to report back from a 3D pack.
        """
        return max((p.max_z for p in self.placed), default=0.0)


def _rotated_footprint(bbox: BoundingBox, rot_deg: int) -> tuple[float, float]:
    """Footprint (size_x, size_y) after a Z rotation of 0 or 90 degrees."""
    if rot_deg == 90:
        return bbox.size_y, bbox.size_x
    return bbox.size_x, bbox.size_y


def _grid_capacity(
    size_x: float, size_y: float, usable_x: float, usable_y: float, gap: float
) -> int:
    """How many parts of this footprint fit in one tier, laid out as a grid.

    n items in a row span n*size + (n-1)*gap, so the count is
    floor((usable + gap) / (size + gap)).
    """
    if size_x <= 0 or size_y <= 0:
        return 0
    cols = int((usable_x + gap) // (size_x + gap))
    rows = int((usable_y + gap) // (size_y + gap))
    return max(0, cols) * max(0, rows)


def _rotated_min_corner(bbox: BoundingBox, rot_deg: int) -> tuple[float, float]:
    """Min X/Y corner of the bbox after rotating about Z.

    A 90 degree CCW rotation maps (x, y) -> (-y, x), so the new minimum X comes from
    the old maximum Y. Getting this backwards silently shifts every rotated part by its
    own width, which is exactly the kind of error that only shows up as a collision.
    """
    if rot_deg == 90:
        return -bbox.max_y, bbox.min_x
    return bbox.min_x, bbox.min_y


def _pack_columns(
    config: LPConfig,
    measured: list[tuple[PartInstance, BoundingBox, int]],
    z_ceiling: float,
    result: NativePackResult,
    progress: ProgressFn | None = None,
) -> None:
    """Stack each design's copies into vertical columns, in place on `result`.

    A file asking for `xN` means N of one design, and the operator wants those N sitting
    directly on top of each other -- one XY footprint, one orientation, the tier gap
    between them, like blocks. That is what makes a repeat plate legible: each design is a
    single identifiable stack rather than N units scattered across a tier.

    A column is bounded by `z_ceiling`, not by N. Once a stack reaches the height limit the
    next copy starts a fresh column alongside it, so an x40 of a 9 mm part under a 60 mm cap
    becomes 8 columns of 5 rather than one 480 mm tower.

    Orientation is preserved: every copy keeps the model's own angle. The part is rotated
    90 degrees only when it would not otherwise fit the plate at all, and then the whole
    column rotates together so the stack still reads as one block.
    """
    border = config.border_spacing_xy
    clearance = config.clearance_buffer
    gap = config.layer_gap_z
    base_z = config.border_spacing_z
    usable_x = config.platform_x - 2 * border
    usable_y = config.platform_y - 2 * border

    # Group copies by their source file. dict preserves insertion order, and `measured`
    # arrives in scan order, so designs keep the order the operator sees in the parts list.
    groups: dict[Path, list[tuple[PartInstance, BoundingBox, int]]] = {}
    for entry in measured:
        groups.setdefault(entry[0].source, []).append(entry)

    # Tallest design first: a tall column claims its XY slot while the plate is empty,
    # and shorter designs fill in around it.
    ordered = sorted(groups.items(), key=lambda kv: -kv[1][0][1].size_z)

    cursor_x = border
    cursor_y = border
    shelf_height = 0.0
    tallest_column = 0
    columns = 0
    plate_full = False

    for done, (source, members) in enumerate(ordered):
        if progress and ordered:
            progress(f"Stacking {source.name}", done / len(ordered))

        if plate_full:
            result.leftover.extend(m[0] for m in members)
            continue

        bbox = members[0][1]

        # Keep the model's own angle; rotate only to make it fit at all.
        rot = 0
        size_x, size_y = bbox.size_x, bbox.size_y
        if size_x > usable_x or size_y > usable_y:
            if bbox.size_y <= usable_x and bbox.size_x <= usable_y:
                rot = 90
                size_x, size_y = _rotated_footprint(bbox, rot)
            else:
                Logger.warning(
                    f"[VULCAN] '{source.name}' ({bbox.size_x:.1f} x {bbox.size_y:.1f} mm) "
                    f"cannot fit the {usable_x:.1f} x {usable_y:.1f} mm usable area."
                )
                result.oversize.extend(m[0] for m in members)
                continue

        height = bbox.size_z
        if height > config.platform_z - base_z:
            Logger.warning(f"[VULCAN] '{source.name}' is taller than the build envelope.")
            result.oversize.extend(m[0] for m in members)
            continue

        if base_z + height > z_ceiling:
            # Printable, but not under this plate's cleaning cap -- defer, never quarantine.
            Logger.warning(
                f"[VULCAN] '{source.name}' ({height:.1f} mm) exceeds the "
                f"{z_ceiling:.0f} mm stacking cap on its own."
            )
            result.leftover.extend(m[0] for m in members)
            continue

        # How many copies stack before the column reaches the cap.
        per_column = max(1, int((z_ceiling - base_z + gap) // (height + gap)))

        index = 0
        while index < len(members):
            chunk = members[index:index + per_column]

            # Find an XY slot for this column, wrapping to a new shelf row as needed.
            if cursor_x + size_x > border + usable_x:
                cursor_x = border
                cursor_y += shelf_height + clearance
                shelf_height = 0.0

            if cursor_y + size_y > border + usable_y:
                # No XY room left: this column and everything after it goes to the next plate.
                result.leftover.extend(m[0] for m in members[index:])
                plate_full = True
                break

            rot_min_x, rot_min_y = _rotated_min_corner(bbox, rot)
            level_z = base_z
            for inst, part_bbox, tri_count in chunk:
                result.placed.append(
                    PlacedPart(
                        name=inst.name,
                        source=inst.source,
                        rot_deg=rot,
                        offset=(
                            cursor_x - rot_min_x,
                            cursor_y - rot_min_y,
                            level_z - part_bbox.min_z,
                        ),
                        min_x=cursor_x,
                        max_x=cursor_x + size_x,
                        min_y=cursor_y,
                        max_y=cursor_y + size_y,
                        min_z=level_z,
                        max_z=level_z + part_bbox.size_z,
                        triangle_count=tri_count,
                    )
                )
                level_z += part_bbox.size_z + gap

            columns += 1
            tallest_column = max(tallest_column, len(chunk))
            cursor_x += size_x + clearance
            shelf_height = max(shelf_height, size_y)
            index += per_column

    result.layers_used = max(1, tallest_column)
    result.column_count = columns


def pack_shelf(
    config: LPConfig,
    candidates: list[Path] | list[PartInstance],
    mode: StrategyMode = StrategyMode.NESTING_2D,
    progress: ProgressFn | None = None,
    height_limit_mm: float | None = None,
) -> NativePackResult:
    """Shelf-pack `candidates` onto one plate within the machine envelope.

    Accepts plain paths or `PartInstance`s; quantity expansion is the caller's job, so a
    file requesting several copies arrives here as several instances sharing one source.

    `height_limit_mm` caps how tall a 3D stack may grow, defaulting to
    `config.max_plate_height_mm`. It is a *preference* about cleaning effort rather than a
    machine limit, so callers pass it explicitly to cost a capped and an uncapped plan
    against each other. 2D ignores it: a single tier cannot approach it.

    Parts that do not fit are returned as `leftover` for the caller to push onto the next
    plate -- never silently declared packed. Parts that cannot fit any plate in any
    orientation are returned as `oversize` so they are reported once and not retried
    forever on successive plates.
    """
    result = NativePackResult()
    candidates = as_instances(candidates)

    border = config.border_spacing_xy
    clearance = config.clearance_buffer
    usable_x = config.platform_x - 2 * border
    usable_y = config.platform_y - 2 * border

    if usable_x <= 0 or usable_y <= 0:
        Logger.error(
            f"[VULCAN] Border spacing {border}mm leaves no usable area on a "
            f"{config.platform_x}x{config.platform_y}mm platform."
        )
        result.leftover = list(candidates)
        return result

    # Measure every candidate first. Sorting by depth needs all footprints up front, and
    # calculate_stl_aabb is cached on (name, size) so repeated copies of one source and
    # repeat runs both come back from cache.
    measured: list[tuple[PartInstance, BoundingBox, int]] = []
    total = len(candidates)
    for idx, inst in enumerate(candidates):
        if progress and total:
            progress(f"Measuring {inst.name}", idx / total)
        try:
            bbox, tri_count = StrategySelector.calculate_stl_aabb(inst.source)
        except Exception as err:
            Logger.error(f"[VULCAN] Cannot measure {inst.name}, treating as oversize: {err}")
            result.oversize.append(inst)
            continue
        measured.append((inst, bbox, tri_count))

    is_3d = mode == StrategyMode.PACKING_3D
    layer_gap = config.layer_gap_z

    # Ceiling for this plate. 2D is a single tier, so only the machine limit applies there.
    if is_3d:
        preferred = height_limit_mm if height_limit_mm is not None else config.max_plate_height_mm
        z_ceiling = min(max(0.0, preferred), config.platform_z)
    else:
        z_ceiling = config.platform_z
    result.height_limit_mm = z_ceiling

    if is_3d:
        _pack_columns(config, measured, z_ceiling, result, progress)
        if result.placed:
            Logger.info(
                f"[VULCAN] 3D pack: {len(result.placed)} parts in "
                f"{result.column_count} column(s), tallest {result.layers_used} high, "
                f"plate height {result.plate_height_mm:.1f} mm of {z_ceiling:.0f} mm cap "
                f"({layer_gap:.1f} mm tier gap, {clearance:.1f} mm XY gap)."
            )
        return result

    # Tallest shelf first: classic shelf packing wastes the least strip height that
    # way, which keeps a mixed jewellery batch on one plate instead of two.
    measured.sort(key=lambda m: (-m[1].size_y, -m[1].size_x))

    cursor_x = border
    cursor_y = border
    shelf_height = 0.0
    layer_z = config.border_spacing_z

    for inst, bbox, tri_count in measured:
        # Choose the orientation that fits; prefer the one that wastes less shelf height.
        options: list[int] = []
        for rot in (0, 90):
            fx, fy = _rotated_footprint(bbox, rot)
            if fx <= usable_x and fy <= usable_y:
                options.append(rot)

        if not options:
            Logger.warning(
                f"[VULCAN] '{inst.name}' ({bbox.size_x:.1f} x {bbox.size_y:.1f} mm) cannot fit "
                f"the {usable_x:.1f} x {usable_y:.1f} mm usable area in any orientation."
            )
            result.oversize.append(inst)
            continue

        if bbox.size_z > config.platform_z - config.border_spacing_z:
            Logger.warning(f"[VULCAN] '{inst.name}' is taller than the build envelope.")
            result.oversize.append(inst)
            continue

        rot = min(options, key=lambda r: _rotated_footprint(bbox, r)[1])
        size_x, size_y = _rotated_footprint(bbox, rot)

        # Advance to the next shelf if this part overruns the current strip.
        if cursor_x + size_x > border + usable_x:
            cursor_x = border
            cursor_y += shelf_height + clearance
            shelf_height = 0.0

        # 2D is a single tier, so a full plate spills to Plate N+1.
        if cursor_y + size_y > border + usable_y:
            result.leftover.append(inst)
            continue

        rot_min_x, rot_min_y = _rotated_min_corner(bbox, rot)
        offset = (
            cursor_x - rot_min_x,
            cursor_y - rot_min_y,
            layer_z - bbox.min_z,
        )

        result.placed.append(
            PlacedPart(
                name=inst.name,
                source=inst.source,
                rot_deg=rot,
                offset=offset,
                min_x=cursor_x,
                max_x=cursor_x + size_x,
                min_y=cursor_y,
                max_y=cursor_y + size_y,
                min_z=layer_z,
                max_z=layer_z + bbox.size_z,
                triangle_count=tri_count,
            )
        )

        cursor_x += size_x + clearance
        shelf_height = max(shelf_height, size_y)

    result.layers_used = 1
    result.column_count = len(result.placed)
    return result


def _read_stl_facets(path: Path) -> tuple[bytes, int]:
    """Return (facet block, facet count) for a binary STL, converting ASCII if needed."""
    with open(path, "rb") as f:
        header = f.read(80)
        if len(header) < 80:
            raise ValueError(f"{path.name} is shorter than an STL header")
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            raise ValueError(f"{path.name} has no facet count")
        declared = struct.unpack("<I", count_bytes)[0]
        block = f.read(declared * _FACET_BYTES)

    actual = len(block) // _FACET_BYTES
    if actual != declared and header.strip().startswith(b"solid"):
        return _read_ascii_stl_facets(path)

    if actual < declared:
        Logger.warning(
            f"[VULCAN] {path.name} declares {declared} facets but holds {actual}; "
            "using what is present."
        )
    return block[: actual * _FACET_BYTES], actual


def _read_ascii_stl_facets(path: Path) -> tuple[bytes, int]:
    """Convert an ASCII STL to a binary facet block so the merger has one code path."""
    out = bytearray()
    count = 0
    normal = (0.0, 0.0, 0.0)
    verts: list[tuple[float, float, float]] = []

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            head = parts[0].lower()
            if head == "facet" and len(parts) >= 5:
                try:
                    normal = (float(parts[2]), float(parts[3]), float(parts[4]))
                except ValueError:
                    normal = (0.0, 0.0, 0.0)
                verts = []
            elif head == "vertex" and len(parts) == 4:
                try:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except ValueError:
                    continue
            elif head == "endfacet" and len(verts) == 3:
                out += _FACET_WRITE.pack(
                    normal[0], normal[1], normal[2],
                    verts[0][0], verts[0][1], verts[0][2],
                    verts[1][0], verts[1][1], verts[1][2],
                    verts[2][0], verts[2][1], verts[2][2],
                    0,
                )
                count += 1
                verts = []

    return bytes(out), count


def _transform_facets(block: bytes, rot_deg: int, offset: tuple[float, float, float]) -> bytes:
    """Rotate about Z then translate every vertex and normal in a facet block."""
    dx, dy, dz = offset
    out = bytearray(len(block))
    pos = 0
    pack_into = _FACET_WRITE.pack_into

    if rot_deg == 90:
        # (x, y) -> (-y, x); the normal rotates with the geometry or lighting inverts.
        for (nx, ny, nz, x1, y1, z1, x2, y2, z2, x3, y3, z3) in _FACET_READ.iter_unpack(block):
            pack_into(
                out, pos,
                -ny, nx, nz,
                -y1 + dx, x1 + dy, z1 + dz,
                -y2 + dx, x2 + dy, z2 + dz,
                -y3 + dx, x3 + dy, z3 + dz,
                0,
            )
            pos += _FACET_BYTES
    else:
        for (nx, ny, nz, x1, y1, z1, x2, y2, z2, x3, y3, z3) in _FACET_READ.iter_unpack(block):
            pack_into(
                out, pos,
                nx, ny, nz,
                x1 + dx, y1 + dy, z1 + dz,
                x2 + dx, y2 + dy, z2 + dz,
                x3 + dx, y3 + dy, z3 + dz,
                0,
            )
            pos += _FACET_BYTES

    return bytes(out)


def write_merged_stl(
    placed: list[PlacedPart],
    dest: Path,
    progress: ProgressFn | None = None,
) -> tuple[int, int]:
    """Write every placed part into one binary STL at its packed position.

    Returns (bytes_written, triangle_count). Streams one part at a time and backfills the
    facet count, so peak memory stays at roughly one part rather than the whole plate.

    Raises if the result would hold no geometry -- an empty plate must surface as a
    failure, never as a file that merely looks like a build.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    total_parts = len(placed)
    written_facets = 0

    with open(dest, "wb") as out:
        header = f"LP Agent merged plate - {total_parts} parts".encode("ascii", "replace")
        out.write(header.ljust(80, b"\0")[:80])
        out.write(struct.pack("<I", 0))  # Placeholder; rewritten once the true count is known.

        for idx, part in enumerate(placed):
            if progress and total_parts:
                progress(f"Merging {part.name}", idx / total_parts)
            try:
                block, count = _read_stl_facets(part.source)
            except Exception as err:
                Logger.error(f"[VULCAN] Skipping {part.name} during merge: {err}")
                continue
            if count == 0:
                Logger.warning(f"[VULCAN] {part.name} holds no facets; skipped.")
                continue

            out.write(_transform_facets(block, part.rot_deg, part.offset))
            written_facets += count

        out.flush()
        out.seek(80)
        out.write(struct.pack("<I", written_facets))

    size_bytes = dest.stat().st_size

    if written_facets == 0:
        raise ValueError(
            f"Merged plate {dest.name} would contain zero triangles -- refusing to publish "
            "an empty plate as a build."
        )

    size_mb = size_bytes / (1024 * 1024)
    Logger.info(
        f"[VULCAN] Merged plate written: {dest.name} "
        f"({written_facets:,} triangles, {size_mb:.1f} MB)"
    )
    if size_mb > 600.0:
        Logger.warning(
            f"[VULCAN] ⚠️ PLATE SIZE EXCEEDS BUDGET: {dest.name} is {size_mb:.1f} MB (> 600 MB limit!). "
            "A full jewelry build plate should typically be between 150 MB and 600 MB, not multi-gigabytes. "
            "Run 70%-90% Mesh Reduction / Decimation on jewelry pieces in MatrixGold/Rhino or Netfabb before merging. "
            "Un-decimated plates (>1GB - 3.9GB) cause Flashforge WaxJetPrint to consume 45+ GB RAM, freeze, "
            "and crash with Out of Memory."
        )
    return size_bytes, written_facets


def inspect_stl(path: Path) -> dict:
    """Report whether an STL on disk is a real build plate or a placeholder.

    This is what backs the UI's "is the plate actually built?" badge. A file is only
    `built` when it parses as an STL and carries geometry; the 49-byte stub that started
    this whole investigation reports built=False with an explicit reason.
    """
    info = {
        "exists": False,
        "built": False,
        "size_bytes": 0,
        "triangles": 0,
        "format": "unknown",
        "reason": "",
    }

    if not path or not Path(path).exists():
        info["reason"] = "File does not exist"
        return info

    path = Path(path)
    info["exists"] = True
    info["size_bytes"] = path.stat().st_size

    if info["size_bytes"] < _STL_HEADER_BYTES:
        info["format"] = "stub"
        info["reason"] = f"Only {info['size_bytes']} bytes - placeholder, not a build plate"
        return info

    try:
        with open(path, "rb") as f:
            header = f.read(80)
            declared = struct.unpack("<I", f.read(4))[0]
    except Exception as err:
        info["reason"] = f"Unreadable STL: {err}"
        return info

    expected = _STL_HEADER_BYTES + declared * _FACET_BYTES
    if expected == info["size_bytes"] and declared > 0:
        info["format"] = "binary"
        info["triangles"] = declared
        info["built"] = True
        return info

    if header.strip().startswith(b"solid"):
        try:
            _, count = _read_ascii_stl_facets(path)
        except Exception as err:
            info["format"] = "ascii"
            info["reason"] = f"Unparseable ASCII STL: {err}"
            return info
        info["format"] = "ascii"
        info["triangles"] = count
        info["built"] = count > 0
        if count == 0:
            info["reason"] = "ASCII STL declares no facets - placeholder, not a build plate"
        return info

    info["reason"] = (
        f"Facet count {declared} does not match file size {info['size_bytes']} bytes"
    )
    return info
