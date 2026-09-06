"""conftest.py — Test fixtures and mock binary STL generator for LP Agent tests."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


def create_mock_binary_stl(
    file_path: Path,
    size_x: float = 20.0,
    size_y: float = 20.0,
    size_z: float = 5.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    origin_z: float = 0.0,
) -> Path:
    """Generate a valid binary STL file representing a rectangular box.

    Box has 8 vertices and 12 triangles.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    x0, x1 = origin_x, origin_x + size_x
    y0, y1 = origin_y, origin_y + size_y
    z0, z1 = origin_z, origin_z + size_z

    # 8 corner vertices
    v = [
        (x0, y0, z0),  # 0
        (x1, y0, z0),  # 1
        (x1, y1, z0),  # 2
        (x0, y1, z0),  # 3
        (x0, y0, z1),  # 4
        (x1, y0, z1),  # 5
        (x1, y1, z1),  # 6
        (x0, y1, z1),  # 7
    ]

    # 12 triangles (2 per face)
    faces = [
        # Bottom (-Z)
        (0, 2, 1), (0, 3, 2),
        # Top (+Z)
        (4, 5, 6), (4, 6, 7),
        # Front (-Y)
        (0, 1, 5), (0, 5, 4),
        # Back (+Y)
        (2, 3, 7), (2, 7, 6),
        # Left (-X)
        (0, 4, 7), (0, 7, 3),
        # Right (+X)
        (1, 2, 6), (1, 6, 5),
    ]

    with open(file_path, "wb") as f:
        # 80-byte header
        header = b"MOCK_BINARY_STL_FOR_LP_AGENT_TESTS".ljust(80, b"\x00")
        f.write(header)
        # 4-byte uint32 triangle count
        f.write(struct.pack("<I", len(faces)))

        # Write each triangle (50 bytes each)
        for tri in faces:
            v0 = v[tri[0]]
            v1 = v[tri[1]]
            v2 = v[tri[2]]
            # Normal vector (0, 0, 1) dummy
            normal = (0.0, 0.0, 1.0)
            data = struct.pack(
                "<12fH",
                normal[0], normal[1], normal[2],
                v0[0], v0[1], v0[2],
                v1[0], v1[1], v1[2],
                v2[0], v2[1], v2[2],
                0,  # 2-byte attribute byte count
            )
            f.write(data)

    return file_path


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory."""
    return tmp_path


@pytest.fixture
def mock_stl_factory(tmp_path: Path):
    """Factory fixture to create test STL files."""
    def _make_stl(name: str, size_x=20.0, size_y=20.0, size_z=5.0) -> Path:
        target = tmp_path / name
        return create_mock_binary_stl(target, size_x, size_y, size_z)
    return _make_stl
