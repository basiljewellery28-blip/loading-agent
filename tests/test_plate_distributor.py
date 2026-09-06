"""test_plate_distributor.py — Unit tests for Subagent Marshal (PlateDistributor)."""

from pathlib import Path
from unittest.mock import MagicMock

from config.lp_config import LPConfig, StrategyMode
from core.collision_checker import CollisionChecker
from core.netfabb_runner import NetfabbRunner, NetfabbRunResult
from core.plate_distributor import PlateDistributor
from tests.conftest import create_mock_binary_stl


def test_overflow_multi_plate_distribution(tmp_path: Path):
    """Test that leftovers from Plate 1 automatically populate Plate 2."""
    config = LPConfig(dry_run=True)
    verifier = CollisionChecker(config)
    runner = NetfabbRunner(config)

    # Mock runner to simulate Plate 1 packing 2 files and leaving 2 files as leftover
    files = [tmp_path / f"part_{i}.stl" for i in range(4)]
    for f in files:
        create_mock_binary_stl(f)

    # First call packs part_0 and part_1; second call packs part_2 and part_3
    mock_run = MagicMock()
    mock_run.side_effect = [
        NetfabbRunResult(
            success=True,
            exit_code=0,
            packed_files=["part_0.stl", "part_1.stl"],
            leftover_files=["part_2.stl", "part_3.stl"],
        ),
        NetfabbRunResult(
            success=True,
            exit_code=0,
            packed_files=["part_2.stl", "part_3.stl"],
            leftover_files=[],
        ),
    ]
    runner.execute_nesting = mock_run

    distributor = PlateDistributor(config, runner, verifier)
    out_dir = tmp_path / "output_plates"

    result = distributor.distribute_parts(files, out_dir, mode=StrategyMode.NESTING_2D)

    assert result.total_plates_generated == 2
    assert result.total_parts_assigned == 4
    assert len(result.unassigned_files) == 0

    assert len(result.plates[0].packed_files) == 2
    assert result.plates[0].plate_name == "Plate_01"

    assert len(result.plates[1].packed_files) == 2
    assert result.plates[1].plate_name == "Plate_02"
