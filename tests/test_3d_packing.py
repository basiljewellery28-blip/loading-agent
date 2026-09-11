"""test_3d_packing.py — Volumetric stacking for repeat-production batches.

3D packing exists for RPM runs: a filename like `9500-SOLITAIRE-HEAD-PP X120` is one
design printed many times, and stacking tiers is what gets the whole order onto one plate.

The constraint that matters is print time. The WaxJet carriage sweeps the full platform on
every pass, so adding parts across a tier is nearly free while adding a tier is not
(MASTER_PROMPT §3.1) -- which is why these tests assert on tier count and plate height, not
just on whether everything fit.
"""

from __future__ import annotations

import struct
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.collision_checker import CollisionChecker, PlacedPartBox
from core.native_packer import _grid_capacity, pack_shelf
from core.quantity import expand_instances
from core.strategy_selector import BoundingBox, StrategySelector


def _make_stl(path: Path, size_x: float, size_y: float, size_z: float) -> Path:
    """A single-triangle STL whose bounding box is exactly size_x/y/z."""
    verts = [(0.0, 0.0, 0.0), (size_x, 0.0, 0.0), (size_x, size_y, size_z)]
    with open(path, "wb") as f:
        f.write(b"t".ljust(80, b"\0"))
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<12fH", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2], 0))
    return path


def _tier_z_values(result) -> list[float]:
    return sorted({round(p.min_z, 4) for p in result.placed})


class TestGridCapacity:
    """n items span n*size + (n-1)*gap, so capacity is floor((usable+gap)/(size+gap))."""

    def test_exact_fit_without_gap(self):
        assert _grid_capacity(10.0, 10.0, 100.0, 100.0, 0.0) == 100

    def test_gap_reduces_capacity(self):
        # (100+2)/(10+2) = 8.5 -> 8 per axis
        assert _grid_capacity(10.0, 10.0, 100.0, 100.0, 2.0) == 64

    def test_oversized_part_has_no_capacity(self):
        assert _grid_capacity(200.0, 10.0, 100.0, 100.0, 2.0) == 0

    def test_degenerate_size_is_safe(self):
        assert _grid_capacity(0.0, 10.0, 100.0, 100.0, 2.0) == 0


class TestStackingBehaviour:
    def test_copies_of_one_file_form_a_single_column(self, tmp_path: Path):
        """xN means N of one design stacked like blocks: one footprint, N levels.

        This is the defining behaviour of 3D packing. Spreading the copies across a tier
        instead would leave the operator hunting for a design's units across the plate.
        """
        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X3.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert len(result.placed) == 3
        assert result.column_count == 1
        assert result.layers_used == 3

        # One XY position, three distinct heights.
        assert len({(round(p.min_x, 4), round(p.min_y, 4)) for p in result.placed}) == 1
        assert len(_tier_z_values(result)) == 3

    def test_a_column_starts_at_the_base_standoff(self, tmp_path: Path):
        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X3.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert min(p.min_z for p in result.placed) == config.border_spacing_z

    def test_each_design_gets_its_own_column(self, tmp_path: Path):
        """Two designs must not share a stack -- a column is one design."""
        config = LPConfig()
        a = _make_stl(tmp_path / "A-ring-PP X3.stl", 20.0, 15.0, 8.0)
        b = _make_stl(tmp_path / "B-pendant-PP X2.stl", 25.0, 18.0, 12.0)

        result = pack_shelf(
            config, expand_instances([a]) + expand_instances([b]),
            mode=StrategyMode.PACKING_3D,
        )

        columns: dict[tuple, set[str]] = {}
        for p in result.placed:
            columns.setdefault((round(p.min_x, 4), round(p.min_y, 4)), set()).add(p.source.name)

        assert len(columns) == 2
        for sources in columns.values():
            assert len(sources) == 1, "a column must hold a single design"

    def test_overflowing_batch_stacks_instead_of_spilling(self, tmp_path: Path):
        """The whole point: 3D keeps a repeat order on one plate where 2D would not."""
        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, 8.0)
        instances = expand_instances([src])

        flat = pack_shelf(config, instances, mode=StrategyMode.NESTING_2D)
        stacked = pack_shelf(config, instances, mode=StrategyMode.PACKING_3D)

        assert flat.leftover, "this batch is meant to overflow a single flat tier"
        assert len(stacked.placed) == 100
        assert stacked.leftover == []
        assert stacked.layers_used > 1

    def test_stacking_costs_height(self, tmp_path: Path):
        """Height is the price of stacking, and it is what print time tracks."""
        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, 8.0)
        instances = expand_instances([src])

        flat = pack_shelf(config, instances, mode=StrategyMode.NESTING_2D)
        stacked = pack_shelf(config, instances, mode=StrategyMode.PACKING_3D)

        assert stacked.plate_height_mm > flat.plate_height_mm

    def test_tiers_never_exceed_the_build_envelope(self, tmp_path: Path):
        config = LPConfig()
        src = _make_stl(tmp_path / "tall-PP X400.stl", 40.0, 40.0, 25.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert result.plate_height_mm <= config.platform_z
        assert result.leftover, "parts beyond the envelope must spill to the next plate"

    def test_copies_of_one_design_stay_contiguous(self, tmp_path: Path):
        """Grouping by design keeps a tier readable and pickable for the operator."""
        config = LPConfig()
        a = _make_stl(tmp_path / "design-a-PP X6.stl", 30.0, 30.0, 6.0)
        b = _make_stl(tmp_path / "design-b-PP X6.stl", 30.0, 30.0, 6.0)

        result = pack_shelf(
            config, expand_instances([a]) + expand_instances([b]),
            mode=StrategyMode.PACKING_3D,
        )

        sources = [p.source.name for p in result.placed]
        # Every design appears as one unbroken run.
        runs = [sources[0]]
        for name in sources[1:]:
            if name != runs[-1]:
                runs.append(name)
        assert len(runs) == len(set(runs)), f"designs are interleaved: {runs}"


class TestTierSpacing:
    """The tier gap is the operator-facing 3D spacing control."""

    def test_gap_between_tiers_is_exactly_the_setting(self, tmp_path: Path):
        config = LPConfig()
        config.layer_gap_z = 3.0
        part_height = 8.0
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, part_height)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)
        tiers = _tier_z_values(result)

        assert len(tiers) >= 2
        gap = tiers[1] - (tiers[0] + part_height)
        assert abs(gap - config.layer_gap_z) < 1e-6

    def test_wider_gap_shortens_the_columns(self, tmp_path: Path):
        """A wider tier gap fits fewer units under the cap, so columns get shorter.

        Counter-intuitive but correct: the height limit -- not the unit count -- bounds a
        column, so loosening the gap trades stack depth for plate area, never extra height.
        """
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, 8.0)
        instances = expand_instances([src])

        tight = LPConfig()
        tight.layer_gap_z = 3.0
        loose = LPConfig()
        loose.layer_gap_z = 12.0

        packed_tight = pack_shelf(tight, instances, mode=StrategyMode.PACKING_3D)
        packed_loose = pack_shelf(loose, instances, mode=StrategyMode.PACKING_3D)

        assert packed_loose.layers_used < packed_tight.layers_used
        assert packed_loose.column_count > packed_tight.column_count
        assert packed_loose.plate_height_mm <= packed_loose.height_limit_mm
        assert packed_tight.plate_height_mm <= packed_tight.height_limit_mm

    def test_tier_gap_is_independent_of_the_base_standoff(self, tmp_path: Path):
        """border_spacing_z positions tier 1; layer_gap_z separates tiers above it.

        These were the same value once, which made the vertical gap 0.5 mm -- far too
        tight for support wax to wash out of.
        """
        config = LPConfig()
        config.border_spacing_z = 0.5
        config.layer_gap_z = 7.0
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, 8.0)

        tiers = _tier_z_values(
            pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)
        )

        assert tiers[0] == 0.5, "tier 1 sits at the base standoff"
        assert abs((tiers[1] - (tiers[0] + 8.0)) - 7.0) < 1e-6

    def test_xy_spacing_applies_between_columns(self, tmp_path: Path):
        """Neighbouring columns keep the part gap, same as neighbouring parts in 2D."""
        config = LPConfig()
        config.clearance_buffer = 4.0
        src = _make_stl(tmp_path / "head-PP X20.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        # One representative part per column, along the first row.
        first_row_y = min(p.min_y for p in result.placed)
        columns = sorted(
            {round(p.min_x, 4): p for p in result.placed if abs(p.min_y - first_row_y) < 1e-6}
            .values(),
            key=lambda p: p.min_x,
        )

        assert len(columns) >= 2
        assert abs((columns[1].min_x - columns[0].max_x) - 4.0) < 1e-6


class TestOrientationChoice:
    def test_the_model_keeps_its_own_angle(self, tmp_path: Path):
        """A stack must read as the model repeated, not as parts turned to suit the packer."""
        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X6.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert {p.rot_deg for p in result.placed} == {0}

    def test_every_unit_in_a_column_shares_one_orientation(self, tmp_path: Path):
        """Mixed angles within a stack would defeat the point of stacking like blocks."""
        config = LPConfig()
        # Too wide at its natural angle, so the whole column must rotate together.
        src = _make_stl(tmp_path / "wide-PP X4.stl", 200.0, 40.0, 6.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert result.placed, "a rotation should have made this fit"
        assert len({p.rot_deg for p in result.placed}) == 1

    def test_rotation_is_used_only_when_the_part_would_not_fit(self, tmp_path: Path):
        """229 x 132 usable: 200mm fits along X but not Y, so this one has to turn."""
        config = LPConfig()
        src = _make_stl(tmp_path / "tall-y-PP X2.stl", 40.0, 200.0, 6.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert result.placed
        assert {p.rot_deg for p in result.placed} == {90}


class TestNoCollisions:
    def test_stacked_plate_passes_verification(self, tmp_path: Path):
        """A 3D layout must satisfy Sentry in all three axes, not just XY."""
        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)
        boxes = [
            PlacedPartBox(p.name, BoundingBox(p.min_x, p.max_x, p.min_y, p.max_y,
                                              p.min_z, p.max_z))
            for p in result.placed
        ]

        report = CollisionChecker(config).verify_placed_boxes(boxes)

        assert report.passed is True
        assert report.envelope_violations == []
        assert report.clearance_violations == []

    def test_mixed_height_designs_do_not_collide_across_tiers(self, tmp_path: Path):
        """A short design in tier 1 must not be pierced by the tier above it."""
        config = LPConfig()
        tall = _make_stl(tmp_path / "tall-PP X40.stl", 25.0, 25.0, 14.0)
        short = _make_stl(tmp_path / "short-PP X40.stl", 25.0, 25.0, 4.0)

        result = pack_shelf(
            config, expand_instances([tall]) + expand_instances([short]),
            mode=StrategyMode.PACKING_3D,
        )
        boxes = [
            PlacedPartBox(p.name, BoundingBox(p.min_x, p.max_x, p.min_y, p.max_y,
                                              p.min_z, p.max_z))
            for p in result.placed
        ]

        assert CollisionChecker(config).verify_placed_boxes(boxes).passed is True


class TestStrategySelection:
    """AUTO must route repeat-production batches to 3D and leave bespoke work in 2D."""

    def _scanned(self, path: Path):
        from core.date_scanner import ScannedPart

        return ScannedPart(
            file_path=path, stem=path.stem, is_repaired=False, is_multipart=False,
            component_index=None, base_design_key=path.stem,
            file_size_bytes=path.stat().st_size,
        )

    def test_repeat_batch_selects_3d(self, tmp_path: Path):
        src = _make_stl(tmp_path / "9500-HEAD-PP X40.stl", 20.0, 15.0, 8.0)

        evaluation = StrategySelector(LPConfig()).evaluate_batch([self._scanned(src)])

        assert evaluation.selected_mode == StrategyMode.PACKING_3D
        assert evaluation.repeat_ratio == 1.0
        assert "Repeat production" in evaluation.reason

    def test_threshold_is_x3(self, tmp_path: Path):
        """'x3 going up' -- x2 is a pair, not a production run."""
        selector = StrategySelector(LPConfig())
        two = _make_stl(tmp_path / "pair-PP X2.stl", 20.0, 15.0, 8.0)
        three = _make_stl(tmp_path / "run-PP X3.stl", 20.0, 15.0, 8.0)

        assert selector.evaluate_batch([self._scanned(two)]).selected_mode == \
            StrategyMode.NESTING_2D
        assert selector.evaluate_batch([self._scanned(three)]).selected_mode == \
            StrategyMode.PACKING_3D

    def test_bespoke_batch_stays_2d(self, tmp_path: Path):
        one_offs = [
            self._scanned(_make_stl(tmp_path / f"MC95{i}-219{i}-PP.stl", 20.0, 15.0, 8.0))
            for i in range(5)
        ]

        evaluation = StrategySelector(LPConfig()).evaluate_batch(one_offs)

        assert evaluation.selected_mode == StrategyMode.NESTING_2D
        assert evaluation.repeat_ratio == 0.0

    def test_repeat_ratio_is_weighted_by_parts_not_files(self, tmp_path: Path):
        """One x50 file outweighs a few one-offs, because it is 50 parts on the plate."""
        batch = [
            self._scanned(_make_stl(tmp_path / "bulk-PP X50.stl", 10.0, 10.0, 5.0)),
            self._scanned(_make_stl(tmp_path / "one-off-a-PP.stl", 10.0, 10.0, 5.0)),
            self._scanned(_make_stl(tmp_path / "one-off-b-PP.stl", 10.0, 10.0, 5.0)),
        ]

        evaluation = StrategySelector(LPConfig()).evaluate_batch(batch)

        assert evaluation.repeat_ratio > 0.9
        assert evaluation.selected_mode == StrategyMode.PACKING_3D

    def test_explicit_mode_overrides_the_repeat_heuristic(self, tmp_path: Path):
        config = LPConfig()
        config.mode = StrategyMode.NESTING_2D
        src = _make_stl(tmp_path / "9500-HEAD-PP X40.stl", 20.0, 15.0, 8.0)

        evaluation = StrategySelector(config).evaluate_batch([self._scanned(src)])

        assert evaluation.selected_mode == StrategyMode.NESTING_2D


class TestEngineSelection:
    """LP Agent decides the 3D layout; Netfabb places, merges and exports it."""

    def test_3d_falls_back_to_a_real_native_plate_without_netfabb(self, tmp_path: Path):
        from core.netfabb_runner import NetfabbRunner

        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        src = _make_stl(tmp_path / "head-PP X3.stl", 20.0, 15.0, 8.0)

        result = NetfabbRunner(config).execute_nesting(
            expand_instances([src]), tmp_path / "out",
            plate_index=1, mode=StrategyMode.PACKING_3D,
        )

        assert result.builder == "lp-native"
        assert result.plate_built is True
        assert result.layers_used == 3, "the column must survive the fallback"

    def test_2d_still_prefers_netfabb(self, tmp_path: Path):
        """TrueShape outline nesting is why Netfabb is here; 2D must keep using it."""
        from core.netfabb_runner import NetfabbRunner

        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        src = _make_stl(tmp_path / "ring-PP.stl", 20.0, 15.0, 8.0)

        result = NetfabbRunner(config).execute_nesting(
            [src], tmp_path / "out", plate_index=1, mode=StrategyMode.NESTING_2D,
        )

        # Netfabb is absent here, so it falls back -- but the reason must be the missing
        # binary, not a deliberate 3D bypass.
        assert "not found" in result.failure_reason


class TestPlacementScript:
    """The 3D Lua places parts at computed positions; it must never re-pack them."""

    def _lua(self, tmp_path: Path, src_name: str = "head-PP X3.stl", **kw):
        from core.netfabb_runner import NetfabbRunner

        config = LPConfig()
        src = _make_stl(tmp_path / src_name, kw.pop("sx", 20.0), kw.pop("sy", 15.0), 8.0)
        packed = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)
        staged = {src: tmp_path / "staged" / src.name}
        lua = NetfabbRunner(config)._generate_placement_lua(
            packed.placed, staged, tmp_path / "m.stl",
            tmp_path / "p.fabbproject", tmp_path / "l.log",
        )
        return lua, packed

    def test_placement_never_invokes_the_packer(self, tmp_path: Path):
        lua, _ = self._lua(tmp_path)
        assert "createpacker" not in lua
        assert ":pack()" not in lua

    def test_every_part_gets_an_explicit_position(self, tmp_path: Path):
        lua, packed = self._lua(tmp_path)

        for part in packed.placed:
            assert f"x={part.min_x:.4f}" in lua
            assert f"y={part.min_y:.4f}" in lua
            assert f"z={part.min_z:.4f}" in lua
        assert lua.count("file=[[") == len(packed.placed)

    def test_rotation_is_expressed_in_radians(self, tmp_path: Path):
        """Netfabb's rotate takes radians -- degrees silently turns the part wrongly."""
        lua, _ = self._lua(tmp_path)
        assert "math.pi / 2" in lua
        assert "rotate(0, 0, 1" in lua

    def test_unrotated_parts_are_flagged_zero(self, tmp_path: Path):
        lua, packed = self._lua(tmp_path)
        assert {p.rot_deg for p in packed.placed} == {0}
        assert "rot=1" not in lua

    def test_rotated_parts_are_flagged_one(self, tmp_path: Path):
        lua, packed = self._lua(tmp_path, src_name="wide-PP X2.stl", sx=40.0, sy=200.0)
        assert {p.rot_deg for p in packed.placed} == {90}
        assert "rot=1" in lua

    def test_position_is_applied_from_the_measured_box(self, tmp_path: Path):
        """Translating from the post-rotation outbox lands on target whatever the mesh
        origin or rotation centre happens to be."""
        lua, _ = self._lua(tmp_path)
        assert "tm:translate(p.x - o.minx, p.y - o.miny, p.z - o.minz)" in lua

    def test_copies_reference_one_staged_source(self, tmp_path: Path):
        """A 40-up batch must not stage the same mesh forty times."""
        lua, packed = self._lua(tmp_path)
        staged_paths = {
            line.split("file=[[")[1].split("]]")[0]
            for line in lua.splitlines() if "file=[[" in line
        }
        assert len(packed.placed) == 3
        assert len(staged_paths) == 1

    def test_names_survive_parentheses_and_spaces(self, tmp_path: Path):
        """Jewellery filenames are full of both; Lua long brackets must carry them."""
        lua, _ = self._lua(tmp_path, src_name="MC9535 (0.70ct)-PP X2.stl")
        assert "name=[[MC9535 (0.70ct)-PP X2 [1 of 2].stl]]" in lua


class TestMergedOutput:
    def test_stacked_plate_merges_every_copy(self, tmp_path: Path):
        """The plate STL must contain the geometry for all tiers, at their real heights."""
        from core.native_packer import write_merged_stl

        config = LPConfig()
        src = _make_stl(tmp_path / "head-PP X100.stl", 20.0, 15.0, 8.0)

        packed = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)
        dest = tmp_path / "merged.stl"
        _, triangles = write_merged_stl(packed.placed, dest)

        assert triangles == 100  # one triangle per source mesh, 100 copies

        bbox, _ = StrategySelector.calculate_stl_aabb(dest)
        assert bbox.max_z > 8.5, "the upper levels must be present in the geometry"
        # The merged geometry must actually reach the height the plan reported.
        assert abs(bbox.max_z - packed.plate_height_mm) < 0.01
