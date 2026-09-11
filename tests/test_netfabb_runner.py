"""test_netfabb_runner.py — Unit tests for Subagent Vulcan (NetfabbRunner)."""

from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.netfabb_runner import NetfabbRunner
from tests.conftest import create_mock_binary_stl


def test_lua_script_generation(tmp_path: Path):
    """Test that Vulcan generates a valid Lua script with all WaxJet parameters."""
    config = LPConfig(
        platform_x=235.0,
        platform_y=138.0,
        platform_z=100.0,
        clearance_buffer=2.0,
        border_spacing_xy=3.0,
        border_spacing_z=0.5,
        voxel_size=0.75,
        avoid_interlocking=True,
    )
    runner = NetfabbRunner(config)

    stl1 = tmp_path / "part1.stl"
    stl2 = tmp_path / "part2.stl"
    create_mock_binary_stl(stl1)
    create_mock_binary_stl(stl2)

    merged_stl = tmp_path / "merged_plate_01.stl"
    fabbproject = tmp_path / "plate_01.fabbproject"
    log_file = tmp_path / "netfabb.log"

    lua_code = runner._generate_lua_script(
        stl_paths=[stl1, stl2],
        merged_stl_path=merged_stl,
        fabbproject_path=fabbproject,
        log_file_path=log_file,
        mode=StrategyMode.NESTING_2D,
    )

    # Assert critical jewelry parameters are injected
    assert "local MACHINE_X = 235.0" in lua_code
    assert "local MACHINE_Y = 138.0" in lua_code
    assert "local MACHINE_Z = 100.0" in lua_code
    assert "local CLEARANCE = 2.0" in lua_code
    assert "local BORDER_XY = 3.0" in lua_code
    assert "local BORDER_Z  = 0.5" in lua_code
    assert "local IS_2D     = true" in lua_code
    assert "tray.packingid_2d" in lua_code
    assert "master_mesh:merge(part_mesh)" in lua_code
    assert "master_mesh:savetostl" in lua_code
    assert "part1.stl" in lua_code
    assert "part2.stl" in lua_code


def test_dry_run_simulation_execution(tmp_path: Path):
    """Test that Vulcan produces valid simulated run results during dry-run."""
    config = LPConfig(dry_run=True)
    runner = NetfabbRunner(config)

    stl1 = tmp_path / "model_a.stl"
    stl2 = tmp_path / "model_b.stl"
    create_mock_binary_stl(stl1)
    create_mock_binary_stl(stl2)

    out_dir = tmp_path / "output"
    result = runner.execute_nesting([stl1, stl2], out_dir, plate_index=1, mode=StrategyMode.NESTING_2D)

    assert result.success is True
    assert result.exit_code == 0
    assert len(result.packed_files) == 2
    assert "model_a.stl" in result.packed_files
    assert "model_b.stl" in result.packed_files
    assert len(result.leftover_files) == 0
