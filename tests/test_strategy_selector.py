"""test_strategy_selector.py — Unit tests for Subagent Tactician (StrategySelector)."""

from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.date_scanner import ScannedPart
from core.strategy_selector import StrategySelector
from tests.conftest import create_mock_binary_stl


def test_aabb_calculation_accuracy(tmp_path: Path):
    """Test that binary STL AABB calculation accurately matches dimensions."""
    stl_path = tmp_path / "ring_test.stl"
    create_mock_binary_stl(stl_path, size_x=25.0, size_y=15.0, size_z=8.0, origin_x=10.0, origin_y=-5.0, origin_z=2.0)

    bbox, tri_count = StrategySelector.calculate_stl_aabb(stl_path)

    assert bbox.min_x == 10.0
    assert bbox.max_x == 35.0
    assert bbox.size_x == 25.0

    assert bbox.min_y == -5.0
    assert bbox.max_y == 10.0
    assert bbox.size_y == 15.0

    assert bbox.min_z == 2.0
    assert bbox.max_z == 10.0
    assert bbox.size_z == 8.0

    assert tri_count == 12


def test_oversized_part_detection(tmp_path: Path):
    """Test that models exceeding the WaxJet 51C build envelope are flagged."""
    config = LPConfig(platform_x=235.0, platform_y=138.0, platform_z=100.0)
    selector = StrategySelector(config)

    # Normal part
    p1 = tmp_path / "normal.stl"
    create_mock_binary_stl(p1, 25.0, 25.0, 10.0)

    # Oversized part (300mm length)
    p2 = tmp_path / "oversized.stl"
    create_mock_binary_stl(p2, 300.0, 20.0, 10.0)

    parts = [
        ScannedPart(p1, "normal", True, False, None, "normal", p1.stat().st_size),
        ScannedPart(p2, "oversized", True, False, None, "oversized", p2.stat().st_size),
    ]

    eval_result = selector.evaluate_batch(parts)

    assert len(eval_result.valid_geometries) == 1
    assert len(eval_result.oversized_parts) == 1
    assert eval_result.oversized_parts[0].part.stem == "oversized"


def test_auto_strategy_selects_2d_for_small_batch(tmp_path: Path):
    """Test that batches comfortably fitting flat on the platform select 2D Nesting."""
    config = LPConfig()
    selector = StrategySelector(config)

    # 4 small ring parts (approx 25x25mm each -> total area ~ 3,364 mm^2 vs 30,228 mm^2 platform)
    parts = []
    for i in range(4):
        p = tmp_path / f"ring_{i}.stl"
        create_mock_binary_stl(p, 25.0, 25.0, 10.0)
        parts.append(ScannedPart(p, f"ring_{i}", True, False, None, f"style_{i}", p.stat().st_size))

    eval_result = selector.evaluate_batch(parts)
    assert eval_result.selected_mode == StrategyMode.NESTING_2D
    assert eval_result.estimated_plates_required == 1


def test_auto_strategy_selects_3d_for_large_homogeneous_batch(tmp_path: Path):
    """Test that large batches of identical parts select 3D Packing."""
    config = LPConfig(batch_homogeneity_threshold=0.70)
    selector = StrategySelector(config)

    # 35 large parts of the identical style (exceeds 1 2D plate area)
    parts = []
    for i in range(35):
        p = tmp_path / f"repeat_ring_{i}.stl"
        create_mock_binary_stl(p, 35.0, 35.0, 15.0)
        parts.append(ScannedPart(p, f"repeat_ring_{i}", True, False, None, "RPM_Eternity_Band", p.stat().st_size))

    eval_result = selector.evaluate_batch(parts)
    assert eval_result.selected_mode == StrategyMode.PACKING_3D
    assert eval_result.batch_homogeneity == 1.0
