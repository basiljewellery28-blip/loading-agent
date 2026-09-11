"""test_quantity.py — Filename quantity suffix parsing and instance expansion.

The multiplier cases and the traps below are all taken from real filenames in the
production library, because the risk here is asymmetric: missing a multiplier prints too
few parts, but mistaking a stone dimension for a multiplier floods a plate with parts
nobody ordered.
"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

from config.lp_config import MIN_CLEARANCE_MM, LPConfig, StrategyMode
from core.native_packer import pack_shelf
from core.quantity import (
    MAX_QUANTITY,
    PartInstance,
    as_instances,
    expand_instances,
    parse_quantity,
)


class TestQuantityParsing:
    """Real names from the library, both multiplied and deliberately not."""

    def test_multiplier_after_pp_before_repaired(self):
        assert parse_quantity("6104-I-1.50mm-NFG-219956-PP x2 (repaired).stl") == 2

    def test_multiplier_after_repaired_tag(self):
        assert parse_quantity("6104-I-1.50mm-NFG-219956-PP (repaired) x2.stl") == 2

    def test_uppercase_multiplier(self):
        assert parse_quantity("MC9841-0.50ct Vintage Cushion-219962-PP1 X2.stl") == 2

    def test_multiplier_glued_to_repaired_tag(self):
        assert parse_quantity("10093-Arrow-PP (repaired)x4.stl") == 4

    def test_multiplier_glued_to_pp_marker(self):
        assert parse_quantity("Star of David-20mm-PPx2.stl") == 2

    def test_multiplier_glued_after_pp_index(self):
        assert parse_quantity("MC9387-J1.2-218894-PP1x2 (repaired).stl") == 2

    def test_multiplier_after_several_repair_tags(self):
        assert parse_quantity("9500-I-NFG-PP1 (repaired) (repaired) X16.stl") == 16

    def test_two_digit_multiplier(self):
        assert parse_quantity("9621-(7x5mm)-v2-(10119 GA)-PP1 (repaired) X29.stl") == 29

    def test_last_multiplier_wins(self):
        """'PP X2 ... X2' -- the trailing one is the quantity."""
        assert parse_quantity("MC9760-TEST-PP X2 (repaired) (repaired) X2.stl") == 2

    def test_self_intersection_tag_is_stripped_too(self):
        name = "9536-N-0.50ct-v4-WFG-PP1 (repaired) (self-intersections removed) X2.stl"
        assert parse_quantity(name) == 2


class TestQuantityFalsePositives:
    """Stone dimensions and other mid-name x characters must never multiply a plate."""

    def test_stone_count_in_middle_of_name_ignored(self):
        assert parse_quantity("9455-R-3.30mmx7 Protea Eternity-WFG--219933-PP (repaired).stl") == 1

    def test_dimension_in_parentheses_ignored(self):
        assert parse_quantity("6104-Q-(14X1.5mm)-v1-219936-PP (repaired).stl") == 1

    def test_stone_size_prefix_ignored(self):
        assert parse_quantity("MC9461-10x7mm-Tanzanite-215288-PP1 (repaired).stl") == 1

    def test_dimension_before_trailing_repair_tag_ignored(self):
        assert parse_quantity("MC9454-N-8 X 6mm-216460-PP1 (repaired) (repaired).stl") == 1

    def test_word_ending_in_x_digit_ignored(self):
        assert parse_quantity("SomeMatrix2.stl") == 1

    def test_plain_name_is_single(self):
        assert parse_quantity("MC9535PT-K-NFG-212658-PP3.stl") == 1

    def test_windows_copy_suffix_is_not_a_quantity(self):
        """A '- Copy' file is a duplicate on disk, not an order for more parts."""
        assert parse_quantity("9877-GA-PP (repaired) x7 - Copy.stl") == 1


class TestQuantityLimits:
    def test_absurd_quantity_is_capped(self):
        assert parse_quantity("thing-PP x999.stl") == MAX_QUANTITY

    def test_zero_falls_back_to_one(self):
        assert parse_quantity("thing-PP x0.stl") == 1


class TestInstanceExpansion:
    def test_expansion_produces_one_instance_per_copy(self):
        paths = [Path("a-PP X3.stl"), Path("b-PP.stl")]
        instances = expand_instances(paths)

        assert len(instances) == 4
        assert sum(1 for i in instances if i.source.name == "a-PP X3.stl") == 3
        assert sum(1 for i in instances if i.source.name == "b-PP.stl") == 1

    def test_copies_get_distinct_names(self):
        """Distinct names are what let a partly-placed set spill correctly."""
        instances = expand_instances([Path("ring-PP X3.stl")])
        names = [i.name for i in instances]

        assert len(set(names)) == 3
        assert "[1 of 3]" in names[0]
        assert "[3 of 3]" in names[2]

    def test_single_copy_keeps_the_plain_filename(self):
        instances = expand_instances([Path("ring-PP.stl")])
        assert instances[0].name == "ring-PP.stl"
        assert instances[0].is_copy is False

    def test_copies_share_a_source(self):
        instances = expand_instances([Path("ring-PP X2.stl")])
        assert instances[0].source == instances[1].source
        assert instances[0].source_name == instances[1].source_name

    def test_as_instances_does_not_re_expand(self):
        """Normalising an already-expanded list must not multiply a second time."""
        once = expand_instances([Path("ring-PP X4.stl")])
        assert len(as_instances(once)) == 4

    def test_as_instances_wraps_bare_paths(self):
        wrapped = as_instances([Path("ring-PP X4.stl")])
        assert len(wrapped) == 1
        assert isinstance(wrapped[0], PartInstance)

    def test_copy_name_contains_no_path_separator(self):
        """Copy names are used as real filenames when staging for Netfabb.

        A "[1/2]" form turned the stage path into a missing subdirectory, so every copy
        failed to stage with WinError 3 and was silently dropped from the plate.
        """
        for inst in expand_instances([Path("ring-PP X2.stl")]):
            assert "/" not in inst.name
            assert "\\" not in inst.name

    def test_stage_name_strips_every_illegal_character(self):
        inst = PartInstance(source=Path("a<b>c:d|e?f*g.stl"), copy_index=1, copy_total=2)
        assert not set(inst.stage_name()) & set('<>:"/\\|?*')

    def test_copy_stages_to_a_real_file(self, tmp_path: Path):
        """The staged path for a copy must be a file directly in the stage directory."""
        src = _make_stl(tmp_path / "ring-PP X2.stl", 10.0, 10.0)
        stage = tmp_path / "stage"
        stage.mkdir()

        for inst in expand_instances([src]):
            dst = stage / inst.stage_name()
            assert dst.parent == stage, "a separator in the name would nest it in a subdir"
            shutil.copy2(inst.source, dst)
            assert dst.is_file()

        assert len(list(stage.iterdir())) == 2


def _make_stl(path: Path, size_x: float, size_y: float, size_z: float = 4.0) -> Path:
    verts = [(0.0, 0.0, 0.0), (size_x, 0.0, 0.0), (size_x, size_y, size_z)]
    with open(path, "wb") as f:
        f.write(b"t".ljust(80, b"\0"))
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<12fH", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2], 0))
    return path


class TestQuantityReachesThePlate:
    def test_multiplied_file_is_placed_repeatedly(self, tmp_path: Path):
        src = _make_stl(tmp_path / "ring-PP X5.stl", 20.0, 20.0)

        result = pack_shelf(LPConfig(), expand_instances([src]), mode=StrategyMode.NESTING_2D)

        assert len(result.placed) == 5
        assert len({p.name for p in result.placed}) == 5, "each copy needs its own identity"
        # Every copy is a separate physical position on the plate.
        assert len({(p.min_x, p.min_y) for p in result.placed}) == 5

    def test_merged_plate_contains_every_copy(self, tmp_path: Path):
        from core.native_packer import write_merged_stl

        src = _make_stl(tmp_path / "ring-PP X4.stl", 15.0, 15.0)
        packed = pack_shelf(LPConfig(), expand_instances([src]))
        _, triangles = write_merged_stl(packed.placed, tmp_path / "merged.stl")

        # One triangle per source mesh, four copies.
        assert triangles == 4


class TestMinimumSpacing:
    def test_floor_is_point_three(self):
        assert MIN_CLEARANCE_MM == 0.3

    def test_packer_honours_a_sub_millimetre_gap(self, tmp_path: Path):
        """At 0.3 mm the parts must sit 0.3 mm apart, not be pushed to a wider default."""
        config = LPConfig()
        config.clearance_buffer = 0.3
        stls = [_make_stl(tmp_path / f"p{i}.stl", 20.0, 20.0) for i in range(2)]

        result = pack_shelf(config, stls, mode=StrategyMode.NESTING_2D)

        assert len(result.placed) == 2
        first, second = sorted(result.placed, key=lambda p: p.min_x)
        assert abs((second.min_x - first.max_x) - 0.3) < 1e-6

    def test_tighter_spacing_fits_more_parts(self, tmp_path: Path):
        stls = [_make_stl(tmp_path / f"q{i}.stl", 22.0, 22.0) for i in range(80)]

        tight = LPConfig()
        tight.clearance_buffer = 0.3
        normal = LPConfig()
        normal.clearance_buffer = 2.0

        assert len(pack_shelf(tight, stls).placed) > len(pack_shelf(normal, stls).placed)

    def test_sentry_judges_against_configured_spacing(self):
        """A deliberately tight plate must not be failed by a hardcoded 1.5 mm rule."""
        from core.collision_checker import CollisionChecker, PlacedPartBox
        from core.strategy_selector import BoundingBox

        config = LPConfig()
        config.clearance_buffer = 0.3
        checker = CollisionChecker(config)

        # A 0.5 mm gap: legal at 0.3 mm spacing, would have failed the old fixed threshold.
        a = PlacedPartBox("A", BoundingBox(10.0, 30.0, 10.0, 30.0, 0.5, 10.5))
        b = PlacedPartBox("B", BoundingBox(30.5, 50.0, 10.0, 30.0, 0.5, 10.5))

        assert checker.verify_placed_boxes([a, b]).passed is True

    def test_exact_gap_is_not_a_violation(self):
        """A plate packed at exactly the requested gap must verify.

        Float bbox arithmetic lands on 0.29999..., and a strict `<` comparison then failed
        every neighbouring pair on a correctly packed 0.3 mm plate.
        """
        from core.collision_checker import CollisionChecker, PlacedPartBox
        from core.strategy_selector import BoundingBox

        config = LPConfig()
        config.clearance_buffer = 0.3
        checker = CollisionChecker(config)

        a = PlacedPartBox("A", BoundingBox(3.0, 23.104019165, 3.0, 23.0, 0.5, 10.5))
        b = PlacedPartBox("B", BoundingBox(23.404019165, 43.0, 3.0, 23.0, 0.5, 10.5))

        report = checker.verify_placed_boxes([a, b])
        assert report.passed is True, "an exactly-spaced plate must not fail verification"

    def test_packed_plate_verifies_at_the_floor_spacing(self, tmp_path: Path):
        """End-to-end: pack at 0.3 mm, then verify the result Sentry actually sees."""
        from core.collision_checker import CollisionChecker, PlacedPartBox
        from core.strategy_selector import BoundingBox

        config = LPConfig()
        config.clearance_buffer = MIN_CLEARANCE_MM
        stls = [_make_stl(tmp_path / f"r{i}.stl", 18.0, 14.0) for i in range(24)]

        packed = pack_shelf(config, stls, mode=StrategyMode.NESTING_2D)
        boxes = [
            PlacedPartBox(
                p.name,
                BoundingBox(p.min_x, p.max_x, p.min_y, p.max_y, p.min_z, p.max_z),
            )
            for p in packed.placed
        ]

        report = CollisionChecker(config).verify_placed_boxes(boxes)
        assert report.passed is True
        assert report.clearance_violations == []

    def test_sentry_still_catches_a_breach_of_the_configured_spacing(self):
        from core.collision_checker import CollisionChecker, PlacedPartBox
        from core.strategy_selector import BoundingBox

        config = LPConfig()
        config.clearance_buffer = 0.3
        checker = CollisionChecker(config)

        a = PlacedPartBox("A", BoundingBox(10.0, 30.0, 10.0, 30.0, 0.5, 10.5))
        b = PlacedPartBox("B", BoundingBox(30.1, 50.0, 10.0, 30.0, 0.5, 10.5))

        assert checker.verify_placed_boxes([a, b]).passed is False
