"""test_height_cap.py — The stacking height limit and the decision it forces.

The machine reaches 100 mm, but a tall stack is punishing to clean: support wax has to be
washed out from between every tier. Plates are therefore packed to a preferred ceiling
(60 mm by default), and when respecting it costs an extra plate, that trade is put to the
operator rather than decided silently.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from config.lp_config import LPConfig, StrategyMode
from core.collision_checker import CollisionChecker
from core.height_policy import (
    ASK,
    DEFAULT_UNATTENDED_POLICY,
    EXCEED,
    FAIL,
    SPLIT,
    HeightCapExceeded,
    HeightDecision,
    PlanOption,
    needs_decision,
    resolve,
)
from core.native_packer import pack_shelf
from core.netfabb_runner import NetfabbRunner
from core.plate_distributor import PlateDistributor
from core.quantity import expand_instances


def _make_stl(path: Path, size_x: float, size_y: float, size_z: float) -> Path:
    verts = [(0.0, 0.0, 0.0), (size_x, 0.0, 0.0), (size_x, size_y, size_z)]
    with open(path, "wb") as f:
        f.write(b"t".ljust(80, b"\0"))
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<12fH", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2], 0))
    return path


def _plan(placed: int, left: int = 0, tiers: int = 1, height: float = 10.0,
          limit: float = 60.0) -> PlanOption:
    return PlanOption(parts_placed=placed, parts_left=left, tiers=tiers,
                      height_mm=height, height_limit_mm=limit)


class TestDefaults:
    def test_default_cap_is_60(self):
        assert LPConfig().max_plate_height_mm == 60.0

    def test_machine_limit_is_unchanged_at_100(self):
        assert LPConfig().platform_z == 100.0

    def test_ui_default_policy_is_ask(self):
        assert LPConfig().on_cap_exceeded == ASK

    def test_unattended_default_respects_the_cap(self):
        """A run with nobody watching must not quietly make the cleaner's job harder."""
        assert DEFAULT_UNATTENDED_POLICY == SPLIT


class TestCapLimitsStacking:
    def test_stack_stops_at_the_cap(self, tmp_path: Path):
        # 8mm part + 3mm gap: a 25mm cap allows two tiers (to 19.5mm), not a third (30.5mm).
        config = LPConfig()
        config.max_plate_height_mm = 25.0
        src = _make_stl(tmp_path / "head-PP X200.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert result.plate_height_mm <= 25.0
        assert result.leftover, "parts beyond the cap belong on the next plate"

    def test_a_higher_cap_fits_more_on_one_plate(self, tmp_path: Path):
        src = _make_stl(tmp_path / "head-PP X300.stl", 20.0, 15.0, 8.0)
        instances = expand_instances([src])

        low = LPConfig()
        low.max_plate_height_mm = 40.0
        high = LPConfig()
        high.max_plate_height_mm = 100.0

        assert len(pack_shelf(high, instances, mode=StrategyMode.PACKING_3D).placed) > \
            len(pack_shelf(low, instances, mode=StrategyMode.PACKING_3D).placed)

    def test_cap_never_exceeds_the_machine(self, tmp_path: Path):
        """Asking for more than the machine has does not conjure extra envelope."""
        config = LPConfig()
        config.max_plate_height_mm = 500.0
        src = _make_stl(tmp_path / "head-PP X500.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert result.plate_height_mm <= config.platform_z

    def test_cap_is_ignored_in_2d(self, tmp_path: Path):
        """2D is a single tier and the cap must not reject a legal flat plate."""
        config = LPConfig()
        config.max_plate_height_mm = 5.0  # below the part height
        src = _make_stl(tmp_path / "tall-PP.stl", 20.0, 15.0, 20.0)

        result = pack_shelf(config, [src], mode=StrategyMode.NESTING_2D)

        assert len(result.placed) == 1
        assert result.leftover == []

    def test_part_taller_than_the_cap_is_deferred_not_quarantined(self, tmp_path: Path):
        """The machine can print it, so it is leftover for another plate, not oversize."""
        config = LPConfig()
        config.max_plate_height_mm = 10.0
        src = _make_stl(tmp_path / "tall-PP X3.stl", 20.0, 15.0, 20.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)

        assert result.placed == []
        assert len(result.leftover) == 3
        assert result.oversize == []

    def test_capped_plate_still_verifies(self, tmp_path: Path):
        from core.collision_checker import PlacedPartBox
        from core.strategy_selector import BoundingBox

        config = LPConfig()
        config.max_plate_height_mm = 45.0
        src = _make_stl(tmp_path / "head-PP X200.stl", 20.0, 15.0, 8.0)

        result = pack_shelf(config, expand_instances([src]), mode=StrategyMode.PACKING_3D)
        boxes = [
            PlacedPartBox(p.name, BoundingBox(p.min_x, p.max_x, p.min_y, p.max_y,
                                              p.min_z, p.max_z))
            for p in result.placed
        ]

        assert CollisionChecker(config).verify_placed_boxes(boxes).passed is True


class TestDecisionDetection:
    def test_decision_needed_when_the_cap_costs_parts(self):
        assert needs_decision(_plan(210), _plan(250)) is True

    def test_no_decision_when_the_cap_costs_nothing(self):
        assert needs_decision(_plan(250), _plan(250)) is False

    def test_a_taller_plan_holding_no_more_is_never_offered(self):
        """Height with no extra parts is strictly worse -- not worth asking about."""
        assert needs_decision(_plan(250, height=59.0), _plan(250, height=71.0)) is False


class TestPolicyResolution:
    def _decision(self) -> HeightDecision:
        return HeightDecision(
            plate_name="Plate_01",
            capped=_plan(210, left=40, tiers=5, height=59.0, limit=60.0),
            uncapped=_plan(250, left=0, tiers=6, height=71.0, limit=100.0),
        )

    def test_split_policy_respects_the_cap(self):
        assert resolve(SPLIT, self._decision()) == SPLIT

    def test_exceed_policy_lifts_the_cap(self):
        assert resolve(EXCEED, self._decision()) == EXCEED

    def test_fail_policy_raises(self):
        with pytest.raises(HeightCapExceeded):
            resolve(FAIL, self._decision())

    def test_ask_uses_the_callback(self):
        assert resolve(ASK, self._decision(), ask=lambda d: EXCEED) == EXCEED

    def test_ask_without_a_callback_falls_back_safely(self):
        """No interactive caller means nobody consented to a taller plate."""
        assert resolve(ASK, self._decision(), ask=None) == SPLIT

    def test_a_nonsense_answer_falls_back_safely(self):
        assert resolve(ASK, self._decision(), ask=lambda d: "maybe") == SPLIT

    def test_decision_reports_what_the_cap_costs(self):
        decision = self._decision()
        assert decision.extra_parts_if_exceeded == 40
        assert decision.extra_height_if_exceeded == pytest.approx(12.0)
        assert "Plate_01" in decision.summary()

    def test_decision_serialises_for_the_ui(self):
        payload = self._decision().as_dict()
        assert payload["capped"]["height_mm"] == 59.0
        assert payload["uncapped"]["parts_placed"] == 250
        assert payload["extra_parts_if_exceeded"] == 40


class TestDistributorHonoursTheDecision:
    def _distributor(self, config: LPConfig) -> PlateDistributor:
        return PlateDistributor(config, NetfabbRunner(config), CollisionChecker(config))

    def _batch(self, tmp_path: Path) -> list:
        src = _make_stl(tmp_path / "head-PP X200.stl", 20.0, 15.0, 8.0)
        return expand_instances([src])

    def test_split_produces_more_but_shorter_plates(self, tmp_path: Path):
        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        config.max_plate_height_mm = 25.0
        config.on_cap_exceeded = SPLIT
        config.dry_run = True

        result = self._distributor(config).distribute_parts(
            self._batch(tmp_path), tmp_path / "out", mode=StrategyMode.PACKING_3D,
        )

        assert result.total_plates_generated >= 2
        for plate in result.plates:
            assert plate.netfabb_result.plate_height_mm <= 25.0

    def test_exceed_produces_a_taller_plate(self, tmp_path: Path):
        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        config.max_plate_height_mm = 25.0
        config.on_cap_exceeded = EXCEED
        config.dry_run = True

        result = self._distributor(config).distribute_parts(
            self._batch(tmp_path), tmp_path / "out", mode=StrategyMode.PACKING_3D,
        )

        assert result.plates[0].netfabb_result.plate_height_mm > 25.0

    def test_ask_is_consulted_per_plate(self, tmp_path: Path):
        """The right answer can differ between Plate_01 and Plate_02, so each is asked."""
        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        config.max_plate_height_mm = 25.0
        config.on_cap_exceeded = ASK
        config.dry_run = True

        seen: list[str] = []

        def decide(decision: HeightDecision) -> str:
            seen.append(decision.plate_name)
            return SPLIT

        self._distributor(config).distribute_parts(
            self._batch(tmp_path), tmp_path / "out",
            mode=StrategyMode.PACKING_3D, decide=decide,
        )

        assert seen, "the operator must be asked when the cap costs parts"
        assert seen == sorted(set(seen)), f"each plate asked at most once: {seen}"

    def test_no_prompt_when_the_cap_is_not_binding(self, tmp_path: Path):
        """A batch that fits comfortably must not interrupt the operator."""
        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        config.on_cap_exceeded = ASK
        config.dry_run = True
        small = _make_stl(tmp_path / "head-PP X4.stl", 20.0, 15.0, 8.0)

        asked: list[str] = []
        self._distributor(config).distribute_parts(
            expand_instances([small]), tmp_path / "out",
            mode=StrategyMode.PACKING_3D,
            decide=lambda d: asked.append(d.plate_name) or SPLIT,
        )

        assert asked == []

    def test_2d_never_prompts(self, tmp_path: Path):
        config = LPConfig()
        config.netfabb_executable = str(tmp_path / "absent.exe")
        config.max_plate_height_mm = 5.0
        config.on_cap_exceeded = ASK
        config.dry_run = True

        asked: list[str] = []
        self._distributor(config).distribute_parts(
            self._batch(tmp_path), tmp_path / "out",
            mode=StrategyMode.NESTING_2D,
            decide=lambda d: asked.append(d.plate_name) or SPLIT,
        )

        assert asked == []


class TestNetfabbReceivesTheCap:
    def test_lua_tray_height_is_the_cap_in_3d(self, tmp_path: Path):
        config = LPConfig()
        runner = NetfabbRunner(config)
        stl = _make_stl(tmp_path / "p.stl", 10.0, 10.0, 5.0)

        lua = runner._generate_lua_script(
            stl_paths=[stl], merged_stl_path=tmp_path / "m.stl",
            fabbproject_path=tmp_path / "p.fabbproject",
            log_file_path=tmp_path / "l.log",
            mode=StrategyMode.PACKING_3D, height_limit_mm=60.0,
        )

        assert "local MACHINE_Z = 60.0" in lua

    def test_lua_tray_height_is_the_machine_in_2d(self, tmp_path: Path):
        config = LPConfig()
        runner = NetfabbRunner(config)
        stl = _make_stl(tmp_path / "p.stl", 10.0, 10.0, 5.0)

        lua = runner._generate_lua_script(
            stl_paths=[stl], merged_stl_path=tmp_path / "m.stl",
            fabbproject_path=tmp_path / "p.fabbproject",
            log_file_path=tmp_path / "l.log",
            mode=StrategyMode.NESTING_2D, height_limit_mm=60.0,
        )

        assert "local MACHINE_Z = 100.0" in lua
