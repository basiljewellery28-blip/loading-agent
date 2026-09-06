"""test_orchestrator.py — Integration tests for LPOrchestrator."""

import json
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from pipeline.orchestrator import LPOrchestrator
from tests.conftest import create_mock_binary_stl


def test_orchestrator_zero_parts_clean_exit(tmp_path: Path):
    """Test that orchestrator handles empty date folder gracefully."""
    config = LPConfig(printing_root=str(tmp_path), dry_run=True)
    orchestrator = LPOrchestrator(config)

    summary = orchestrator.run("06.09.2026")
    assert summary.success is True
    assert summary.total_valid_parts == 0
    assert summary.total_plates_generated == 0


def test_orchestrator_end_to_end_dry_run(tmp_path: Path):
    """Test end-to-end pipeline execution in dry-run mode."""
    date_folder = tmp_path / "September" / "06.09.2026" / "Agent"
    date_folder.mkdir(parents=True, exist_ok=True)

    # Stage a 4-part ring set
    for i in range(1, 5):
        stl_path = date_folder / f"9847-M-0.25ct-NFG-217244-PP{i} (repaired).stl"
        create_mock_binary_stl(stl_path, size_x=22.0, size_y=22.0, size_z=6.0)

    config = LPConfig(printing_root=str(tmp_path), dry_run=True)
    orchestrator = LPOrchestrator(config)

    summary = orchestrator.run("06.09.2026")

    assert summary.success is True
    assert summary.total_valid_parts == 4
    assert summary.mode_selected == StrategyMode.NESTING_2D.value
    assert summary.total_plates_generated >= 1
    assert len(summary.plates) >= 1


def test_orchestrator_live_run_manifest_generation(tmp_path: Path):
    """Test full pipeline run generates plate_manifest.json and plate folders."""
    date_folder = tmp_path / "September" / "06.09.2026" / "Agent"
    date_folder.mkdir(parents=True, exist_ok=True)

    stl_path = date_folder / "solitaire_ring (repaired).stl"
    create_mock_binary_stl(stl_path, size_x=25.0, size_y=20.0, size_z=8.0)

    # Point netfabb executable to non-existent so simulation runs
    config = LPConfig(
        printing_root=str(tmp_path),
        netfabb_executable=str(tmp_path / "fake_netfabb.exe"),
        dry_run=False,
    )
    orchestrator = LPOrchestrator(config)

    summary = orchestrator.run("06.09.2026")

    assert summary.success is True
    assert summary.total_plates_generated == 1
    assert summary.manifest_path is not None
    assert summary.manifest_path.exists()

    with open(summary.manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert manifest_data["machine_name"] == "Flashforge WaxJet 51C"
    assert manifest_data["totals"]["valid_parts"] == 1
    assert len(manifest_data["plates"]) == 1
    assert manifest_data["plates"][0]["plate_name"] == "Plate_01"
