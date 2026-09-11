"""test_plate_build_integrity.py — Regression guards for the empty-plate defect.

Production symptom: a batch of 31 real STLs produced `merged_plate_01.stl` containing::

    solid simulated_plate
    endsolid simulated_plate

49 bytes, no geometry -- and the work order still reported "Verified: PASS". Netfabb had
exceeded a fixed 180s timeout and the fallback wrote a placeholder that every downstream
consumer treated as a real build.

These tests pin the three behaviours that stop that recurring: a fallback must build a
real plate, a placeholder must be detectable as unbuilt, and parts that do not fit must
overflow to the next plate instead of being reported as packed.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.native_packer import inspect_stl, pack_shelf, write_merged_stl
from core.netfabb_runner import NetfabbRunner
from core.quantity import PartInstance, expand_instances
from tests.conftest import create_mock_binary_stl


def _make_stl(path: Path, size_x: float, size_y: float, size_z: float = 4.0) -> Path:
    """Write a binary STL of a single triangle spanning the requested bounding box."""
    verts = [
        (0.0, 0.0, 0.0),
        (size_x, 0.0, 0.0),
        (size_x, size_y, size_z),
    ]
    payload = struct.pack("<12fH", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2], 0)
    with open(path, "wb") as f:
        f.write(b"test".ljust(80, b"\0"))
        f.write(struct.pack("<I", 1))
        f.write(payload)
    return path


class TestStubDetection:
    """inspect_stl must tell a real plate from a placeholder."""

    def test_legacy_stub_plate_is_not_built(self, tmp_path: Path):
        stub = tmp_path / "merged_plate_01.stl"
        stub.write_text("solid simulated_plate\nendsolid simulated_plate\n", encoding="utf-8")

        info = inspect_stl(stub)

        assert info["exists"] is True
        assert info["built"] is False, "the 49-byte stub must never report as a built plate"
        assert info["triangles"] == 0
        assert info["reason"]

    def test_missing_plate_is_not_built(self, tmp_path: Path):
        info = inspect_stl(tmp_path / "absent.stl")
        assert info["exists"] is False
        assert info["built"] is False

    def test_real_binary_plate_is_built(self, tmp_path: Path):
        real = _make_stl(tmp_path / "real.stl", 10.0, 10.0)
        info = inspect_stl(real)
        assert info["built"] is True
        assert info["triangles"] == 1
        assert info["format"] == "binary"


class TestNativeFallbackBuildsRealPlate:
    """The fallback path must produce geometry, not a placeholder."""

    def test_fallback_writes_real_merged_stl(self, tmp_path: Path):
        stls = [_make_stl(tmp_path / f"part_{i}.stl", 12.0, 8.0) for i in range(5)]

        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "no_such_netfabb.exe")
        runner = NetfabbRunner(config)

        result = runner.execute_nesting(stls, tmp_path / "out", plate_index=1,
                                        mode=StrategyMode.NESTING_2D)

        assert result.builder == "lp-native"
        assert result.plate_built is True
        assert result.merged_stl_triangles == 5
        assert len(result.packed_files) == 5

        # The regression itself: the plate on disk must hold geometry.
        info = inspect_stl(result.merged_plate_stl)
        assert info["built"] is True
        assert info["triangles"] == 5
        assert info["size_bytes"] > 84

    def test_merged_geometry_lands_inside_the_envelope(self, tmp_path: Path):
        """Placed positions must match the geometry actually written to the plate."""
        from core.strategy_selector import StrategySelector

        config = LPConfig()
        stls = [_make_stl(tmp_path / f"p{i}.stl", 20.0, 15.0, 6.0) for i in range(12)]

        packed = pack_shelf(config, stls, mode=StrategyMode.NESTING_2D)
        dest = tmp_path / "merged.stl"
        write_merged_stl(packed.placed, dest)

        bbox, _ = StrategySelector.calculate_stl_aabb(dest)
        assert bbox.min_x >= -0.01
        assert bbox.min_y >= -0.01
        assert bbox.max_x <= config.platform_x + 0.01
        assert bbox.max_y <= config.platform_y + 0.01
        assert bbox.max_z <= config.platform_z + 0.01

    def test_empty_plate_refuses_to_publish(self, tmp_path: Path):
        """Writing a plate with no parts must raise, not emit an empty file."""
        import pytest

        with pytest.raises(ValueError, match="zero triangles"):
            write_merged_stl([], tmp_path / "empty.stl")


class TestStaleArtifactsNeverSurvive:
    """A previous run's output must never be read back as this run's result.

    A Netfabb run whose Lua died before writing its audit inherited a *simulated* audit
    from an earlier run, so the plate was verified and drawn against 31 stale positions
    that had nothing to do with the geometry on it.
    """

    def test_previous_plate_artifacts_are_cleared(self, tmp_path: Path):
        out = tmp_path / "out"
        plate_dir = out / "Plate_01"
        plate_dir.mkdir(parents=True)

        stale_audit = plate_dir / "audit_results_01.json"
        stale_audit.write_text('{"simulated": true, "packed_files": ["ghost.stl"]}', encoding="utf-8")
        stale_stl = plate_dir / "merged_plate_01.stl"
        stale_stl.write_text("solid simulated_plate\nendsolid simulated_plate\n", encoding="utf-8")
        (plate_dir / "plate_01.fabbproject").write_text("simulated_fabbproject", encoding="utf-8")

        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        stls = [_make_stl(tmp_path / f"p{i}.stl", 12.0, 9.0) for i in range(3)]

        result = NetfabbRunner(config).execute_nesting(stls, out, plate_index=1)

        audit = json.loads(stale_audit.read_text(encoding="utf-8"))
        assert audit.get("simulated") is not True, "stale audit must be replaced"
        assert audit["builder"] == "lp-native"
        assert "ghost.stl" not in audit["packed_files"]
        assert result.plate_built is True
        assert inspect_stl(stale_stl)["built"] is True

    def test_stale_stub_is_gone_even_if_the_run_fails(self, tmp_path: Path):
        """A failed run must leave no plate at all, rather than the previous stub."""
        out = tmp_path / "out"
        plate_dir = out / "Plate_01"
        plate_dir.mkdir(parents=True)
        stale_stl = plate_dir / "merged_plate_01.stl"
        stale_stl.write_text("solid simulated_plate\nendsolid simulated_plate\n", encoding="utf-8")

        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        huge = _make_stl(tmp_path / "huge.stl", 500.0, 500.0)  # cannot fit any plate

        result = NetfabbRunner(config).execute_nesting([huge], out, plate_index=1)

        assert result.plate_built is False
        assert not stale_stl.exists(), "a failed run must not leave a stale plate behind"


class TestNetfabbAuditOverStdout:
    """Netfabb's Lua is sandboxed with no `io` library, so the audit arrives on stdout.

    An earlier revision called io.open in the Lua and died with "attempt to index global
    'io' (a nil value)", losing the packed positions on every single Netfabb run.
    """

    def test_lua_never_calls_io(self):
        # Strip Lua comments first: the explanation above the emit block names io.open.
        code = "\n".join(
            line for line in NetfabbRunner.LUA_TEMPLATE.splitlines()
            if not line.lstrip().startswith("--")
        )
        assert "io." not in code, "Netfabb's sandboxed Lua has no io library"
        assert "LPBOX|" in code

    def test_parses_boxes_and_leftovers(self):
        stdout = (
            "some noise\n"
            "LPBOX|3.000|22.500|3.000|35.250|0.500|9.000|ring-PP1.stl\n"
            "LPBOX|24.500|31.000|3.000|21.750|0.500|9.600|ring-PP2.stl\n"
            "LPLEFT|too-big.stl\n"
            "LPRESULT|0|2|1"
        )

        packed, leftover, boxes = NetfabbRunner._parse_audit_from_stdout(stdout)

        assert packed == ["ring-PP1.stl", "ring-PP2.stl"]
        assert leftover == ["too-big.stl"]
        assert boxes[0] == {
            "name": "ring-PP1.stl",
            "min_x": 3.0, "max_x": 22.5,
            "min_y": 3.0, "max_y": 35.25,
            "min_z": 0.5, "max_z": 9.0,
        }

    def test_delimiter_inside_a_filename_is_safe(self):
        """The name is last on the line, so a '|' in it cannot corrupt the coordinates."""
        stdout = "LPBOX|1.000|2.000|3.000|4.000|5.000|6.000|odd|name.stl"

        _, _, boxes = NetfabbRunner._parse_audit_from_stdout(stdout)

        assert boxes[0]["name"] == "odd|name.stl"
        assert boxes[0]["max_z"] == 6.0

    def test_malformed_lines_are_skipped(self):
        stdout = "LPBOX|not|a|number|x|y|z|bad.stl\nLPBOX|1|2|3|4|5|6|good.stl"

        packed, _, boxes = NetfabbRunner._parse_audit_from_stdout(stdout)

        assert packed == ["good.stl"]
        assert len(boxes) == 1

    def test_no_markers_yields_nothing(self):
        packed, leftover, boxes = NetfabbRunner._parse_audit_from_stdout("no markers here")
        assert (packed, leftover, boxes) == ([], [], [])


class TestNetfabbNameReconciliation:
    """Netfabb labels parts from the staged filename and may drop the extension.

    Unreconciled, a perfectly packed 35-part plate matched zero instances and was thrown
    away by the distributor as "0 parts packed".
    """

    def test_extensionless_names_are_matched_back(self):
        instances = [PartInstance(source=Path(r"C:\x\ring-PP1 (repaired).stl"))]

        packed, _, boxes = NetfabbRunner._reconcile_names(
            instances,
            ["ring-PP1 (repaired)"],           # as Netfabb reports it
            [],
            [{"name": "ring-PP1 (repaired)", "min_x": 1.0}],
        )

        assert packed == ["ring-PP1 (repaired).stl"]
        assert boxes[0]["name"] == "ring-PP1 (repaired).stl"
        assert boxes[0]["min_x"] == 1.0, "other box fields must survive untouched"

    def test_exact_names_pass_through(self):
        instances = [PartInstance(source=Path("ring.stl"))]
        packed, _, _ = NetfabbRunner._reconcile_names(instances, ["ring.stl"], [], [])
        assert packed == ["ring.stl"]

    def test_copies_resolve_to_their_instance_names(self):
        instances = expand_instances([Path("ring-PP X2.stl")])
        reported = [i.stage_name().removesuffix(".stl") for i in instances]

        packed, _, _ = NetfabbRunner._reconcile_names(instances, reported, [], [])

        assert packed == [i.name for i in instances]
        assert len(set(packed)) == 2

    def test_unknown_names_are_kept_not_dropped(self):
        instances = [PartInstance(source=Path("ring.stl"))]
        packed, _, _ = NetfabbRunner._reconcile_names(instances, ["mystery.stl"], [], [])
        assert packed == ["mystery.stl"], "an unmatched name must stay visible"

    def test_leftovers_are_reconciled_too(self):
        instances = [PartInstance(source=Path("big-PP.stl"))]
        _, leftover, _ = NetfabbRunner._reconcile_names(instances, [], ["big-PP"], [])
        assert leftover == ["big-PP.stl"]

    def test_lua_reports_the_full_filename(self):
        """The Lua must capture the name with its extension, not just the stem."""
        assert '"([^/\\\\]+)$"' in NetfabbRunner.LUA_TEMPLATE
        assert '%.%w+$' not in NetfabbRunner.LUA_TEMPLATE


class TestOverflowIsHonest:
    """Parts that do not fit must be reported as leftovers, not as packed."""

    def test_surplus_parts_become_leftovers(self, tmp_path: Path):
        config = LPConfig()
        # 60 x 60 mm parts on a 235 x 138 mm plate: only a handful fit per plate.
        stls = [_make_stl(tmp_path / f"big_{i}.stl", 60.0, 60.0) for i in range(20)]

        result = pack_shelf(config, stls, mode=StrategyMode.NESTING_2D)

        assert len(result.leftover) > 0, "surplus parts must overflow, not vanish"
        assert len(result.placed) + len(result.leftover) + len(result.oversize) == 20

    def test_wider_spacing_fits_fewer_parts(self, tmp_path: Path):
        """The spacing control must actually change the packing result."""
        stls = [_make_stl(tmp_path / f"m_{i}.stl", 25.0, 25.0) for i in range(30)]

        tight = LPConfig()
        tight.clearance_buffer = 1.5
        loose = LPConfig()
        loose.clearance_buffer = 15.0

        assert len(pack_shelf(loose, stls).placed) < len(pack_shelf(tight, stls).placed)

    def test_oversize_part_is_not_retried_forever(self, tmp_path: Path):
        config = LPConfig()
        huge = _make_stl(tmp_path / "huge.stl", 400.0, 400.0)

        result = pack_shelf(config, [huge], mode=StrategyMode.NESTING_2D)

        assert [i.source for i in result.oversize] == [huge]
        assert result.placed == []
        assert result.leftover == []

    def test_distributor_opens_a_second_plate(self, tmp_path: Path):
        """End-to-end: surplus parts must land on Plate_02, not be claimed by Plate_01."""
        from core.collision_checker import CollisionChecker
        from core.plate_distributor import PlateDistributor

        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        stls = [_make_stl(tmp_path / f"w_{i}.stl", 70.0, 65.0) for i in range(12)]

        distributor = PlateDistributor(config, NetfabbRunner(config), CollisionChecker(config))
        result = distributor.distribute_parts(stls, tmp_path / "plates",
                                              mode=StrategyMode.NESTING_2D)

        assert result.total_plates_generated >= 2
        assert result.total_parts_assigned == 12
        assert result.unassigned_files == []
        for plate in result.plates:
            assert plate.plate_built is True


class TestTimeoutBudget:
    """The Netfabb budget must scale with the batch, not sit at a fixed 180s."""

    def test_budget_grows_with_part_count(self):
        config = LPConfig()
        assert config.compute_netfabb_timeout(50) > config.compute_netfabb_timeout(5)

    def test_budget_grows_with_mesh_size(self):
        config = LPConfig()
        big = config.compute_netfabb_timeout(10, 900 * 1024 * 1024)
        small = config.compute_netfabb_timeout(10, 1024 * 1024)
        assert big > small

    def test_real_batch_gets_more_than_the_old_fixed_budget(self):
        """The 31-part, ~900 MB batch that failed in production must now get real time."""
        config = LPConfig()
        budget = config.compute_netfabb_timeout(31, 900 * 1024 * 1024)
        assert budget > 660, "must exceed the 11 minutes the failing production run needed"

    def test_budget_is_capped(self):
        config = LPConfig()
        assert config.compute_netfabb_timeout(100000, 10**12) == config.timeout_max_seconds


class TestDryRunUnchanged:
    """Dry-run planning must still work and must not write a plate."""

    def test_dry_run_reports_plan_without_writing(self, tmp_path: Path):
        config = LPConfig(dry_run=True)
        runner = NetfabbRunner(config)
        stl = tmp_path / "a.stl"
        create_mock_binary_stl(stl)

        out = tmp_path / "out"
        result = runner.execute_nesting([stl], out, plate_index=1, mode=StrategyMode.NESTING_2D)

        assert result.success is True
        assert result.plate_built is False
        assert not (out / "Plate_01" / "merged_plate_01.stl").exists()
