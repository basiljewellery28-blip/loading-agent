"""test_date_scanner.py — Unit tests for Subagent Scout (DateScanner)."""

from pathlib import Path

from core.date_scanner import DateScanner
from tests.conftest import create_mock_binary_stl


def test_scan_empty_directory(tmp_path: Path):
    """Test that scanning an empty directory exits cleanly with 0 valid parts."""
    scanner = DateScanner(tmp_path)
    result = scanner.scan_date("06.09.2026")
    assert len(result.valid_parts) == 0
    assert len(result.quarantined_files) == 0


def test_provenance_preference_repaired_over_raw(tmp_path: Path):
    """Test that repaired STL is chosen when both raw and repaired exist."""
    date_dir = tmp_path / "September" / "06.09.2026" / "Agent"
    date_dir.mkdir(parents=True, exist_ok=True)

    raw_stl = date_dir / "7900-M-0.35ct-Solitaire-210228-PP.stl"
    repaired_stl = date_dir / "7900-M-0.35ct-Solitaire-210228-PP (repaired).stl"

    create_mock_binary_stl(raw_stl, 20.0, 20.0, 5.0)
    create_mock_binary_stl(repaired_stl, 20.0, 20.0, 5.0)

    scanner = DateScanner(tmp_path)
    result = scanner.scan_date("06.09.2026")

    assert len(result.valid_parts) == 1
    selected = result.valid_parts[0]
    assert selected.file_path == repaired_stl
    assert selected.is_repaired is True


def test_multipart_completeness_success(tmp_path: Path):
    """Test that a complete multi-part set (PP1..PP4) is ingested successfully."""
    date_dir = tmp_path / "September" / "06.09.2026" / "Agent"
    date_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, 5):
        stl_name = f"9847-M-0.25ct-NFG-217244-PP{i} (repaired).stl"
        create_mock_binary_stl(date_dir / stl_name, 20.0, 20.0, 5.0)

    scanner = DateScanner(tmp_path)
    result = scanner.scan_date("06.09.2026")

    assert len(result.valid_parts) == 4
    for part in result.valid_parts:
        assert part.is_multipart is True
        assert part.component_index in [1, 2, 3, 4]
        assert part.base_design_key == "9847-M-0.25ct-NFG-217244"


def test_multipart_incomplete_quarantines(tmp_path: Path):
    """Test that a broken multi-part set (missing PP3) is quarantined."""
    date_dir = tmp_path / "September" / "06.09.2026" / "Agent"
    date_dir.mkdir(parents=True, exist_ok=True)

    # Only create PP1, PP2, and PP4 (missing PP3)
    for i in [1, 2, 4]:
        stl_name = f"9847-M-0.25ct-NFG-217244-PP{i} (repaired).stl"
        create_mock_binary_stl(date_dir / stl_name, 20.0, 20.0, 5.0)

    scanner = DateScanner(tmp_path)
    result = scanner.scan_date("06.09.2026")

    # Incomplete parts must not be in valid_parts
    assert len(result.valid_parts) == 0
    assert len(result.quarantined_files) == 3
    assert "9847-M-0.25ct-NFG-217244" in result.incomplete_designs


def test_zero_byte_file_quarantine(tmp_path: Path):
    """Test that zero-byte STL files are safely quarantined."""
    date_dir = tmp_path / "September" / "06.09.2026" / "Agent"
    date_dir.mkdir(parents=True, exist_ok=True)

    bad_stl = date_dir / "corrupted_piece.stl"
    bad_stl.write_bytes(b"")  # 0 bytes

    good_stl = date_dir / "valid_piece (repaired).stl"
    create_mock_binary_stl(good_stl, 20.0, 20.0, 5.0)

    scanner = DateScanner(tmp_path)
    result = scanner.scan_date("06.09.2026")

    assert len(result.valid_parts) == 1
    assert result.valid_parts[0].file_path == good_stl
    assert len(result.quarantined_files) == 1
    assert result.quarantined_files[0] == bad_stl
