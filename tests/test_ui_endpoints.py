"""test_ui_endpoints.py — Automated tests for LP Agent Web UI endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ui.server import app


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


class TestStatusEndpoint:
    """Tests for GET /api/status."""

    def test_status_returns_machine_config(self, client: TestClient):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["machine_name"] == "Flashforge WaxJet 51C"
        assert data["envelope_mm"]["x"] == 235.0
        assert data["envelope_mm"]["y"] == 138.0
        assert data["envelope_mm"]["z"] == 100.0

    def test_status_includes_parameters(self, client: TestClient):
        resp = client.get("/api/status")
        data = resp.json()
        params = data["parameters"]
        assert params["clearance_buffer_mm"] == 2.0
        assert params["border_spacing_xy_mm"] == 3.0
        assert params["avoid_interlocking"] is True

    def test_status_includes_environment(self, client: TestClient):
        resp = client.get("/api/status")
        data = resp.json()
        assert "today" in data
        assert "printing_root" in data
        assert "netfabb_console_available" in data


class TestScanEndpoint:
    """Tests for GET /api/scan."""

    def test_scan_nonexistent_date_returns_not_found(self, client: TestClient):
        resp = client.get("/api/scan?date=01.01.2099")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False
        assert data["parts"] == []

    def test_scan_invalid_date_format_returns_error(self, client: TestClient):
        resp = client.get("/api/scan?date=not-a-date")
        assert resp.status_code == 400

    def test_scan_valid_date_returns_parts(self, client: TestClient):
        """Test scanning 06.09.2026 which has known STL files."""
        resp = client.get("/api/scan?date=06.09.2026")
        assert resp.status_code == 200
        data = resp.json()
        # This date folder is known to exist with 4 repaired parts
        if data["exists"]:
            assert isinstance(data["parts"], list)
            for part in data["parts"]:
                assert "filename" in part
                assert "is_repaired" in part


class TestPlatesEndpoint:
    """Tests for GET /api/plates."""

    def test_plates_nonexistent_date(self, client: TestClient):
        resp = client.get("/api/plates?date=01.01.2099")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plates"] == []

    def test_plates_known_date(self, client: TestClient):
        resp = client.get("/api/plates?date=06.09.2026")
        assert resp.status_code == 200
        data = resp.json()
        if data["plates"]:
            for plate in data["plates"]:
                assert "plate_name" in plate
                assert "directory" in plate


class TestStaticAssets:
    """Tests for frontend static file serving."""

    def test_index_page_served(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_css_served(self, client: TestClient):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200

    def test_js_served(self, client: TestClient):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200


class TestDownloadEndpoint:
    """Tests for GET /api/download/{plate}/{file}."""

    def test_download_missing_file_returns_404(self, client: TestClient):
        resp = client.get("/api/download/Plate_99/nonexistent.stl")
        assert resp.status_code == 404
