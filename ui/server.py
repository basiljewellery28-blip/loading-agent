"""server.py — FastAPI REST backend for LP Agent Web UI.

Thin wrapper around the existing pipeline orchestrator.
Serves the static frontend and exposes JSON endpoints for scan, pack, and inspect.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.lp_config import LPConfig, StrategyMode
from pipeline.orchestrator import LPOrchestrator

# ── App ──────────────────────────────────────────────────────────
app = FastAPI(title="LP Agent", version="1.0.0")

# Serve static frontend assets
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared config & orchestrator (lightweight singletons)
_config = LPConfig()
_orchestrator = LPOrchestrator(_config)


# ── Pydantic models ──────────────────────────────────────────────
class PackRequest(BaseModel):
    """Request body for /api/pack."""

    date: str | None = None
    mode: str = "auto"
    clearance: float = 2.0
    directory: str | None = None


class OpenNetfabbRequest(BaseModel):
    """Request body for /api/open-netfabb."""

    fabbproject_path: str


# ── Helpers ──────────────────────────────────────────────────────
def _resolve_date_dir(date_str: str | None = None) -> Path:
    """Resolve the printing date directory."""
    printing_root = _config.resolve_printing_root()
    if date_str:
        # Try DD.MM.YYYY format
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"Invalid date format: {date_str}. Use DD.MM.YYYY or YYYY-MM-DD.")
    else:
        dt = datetime.now()

    month_name = dt.strftime("%B")
    date_folder = dt.strftime("%d.%m.%Y")
    return printing_root / month_name / date_folder


# ── Endpoints ────────────────────────────────────────────────────
@app.get("/")
async def index():
    """Serve the main UI page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(500, "Frontend not found.")
    return FileResponse(str(index_path), media_type="text/html")


@app.get("/api/status")
async def get_status():
    """Return machine config, environment health, and default paths."""
    printing_root = _config.resolve_printing_root()
    netfabb_bin = Path(_config.netfabb_executable)
    netfabb_gui = netfabb_bin.parent / "netfabb.exe"

    return {
        "machine_name": _config.machine_name,
        "envelope_mm": {
            "x": _config.platform_x,
            "y": _config.platform_y,
            "z": _config.platform_z,
        },
        "parameters": {
            "clearance_buffer_mm": _config.clearance_buffer,
            "border_spacing_xy_mm": _config.border_spacing_xy,
            "border_spacing_z_mm": _config.border_spacing_z,
            "avoid_interlocking": _config.avoid_interlocking,
        },
        "printing_root": str(printing_root),
        "printing_root_accessible": printing_root.exists(),
        "netfabb_console_path": str(netfabb_bin),
        "netfabb_console_available": netfabb_bin.is_file(),
        "netfabb_gui_path": str(netfabb_gui),
        "netfabb_gui_available": netfabb_gui.is_file(),
        "today": datetime.now().strftime("%d.%m.%Y"),
        "python_version": sys.version,
    }


@app.get("/api/scan")
async def scan_date(date: str | None = Query(None, description="Date string (DD.MM.YYYY)")):
    """Execute Scout (DateScanner) on the target date folder."""
    date_dir = _resolve_date_dir(date)

    if not date_dir.exists():
        return {
            "date": date or datetime.now().strftime("%d.%m.%Y"),
            "directory": str(date_dir),
            "exists": False,
            "parts": [],
            "quarantined": [],
        }

    # Use the orchestrator's scanner
    scan_result = _orchestrator.scout.scan_date(date)

    parts = []
    for p in scan_result.valid_parts:
        # Get geometry if available
        try:
            bbox, tri_count = _orchestrator.tactician.calculate_stl_aabb(p.file_path)
            geom = {
                "size_x": round(bbox.size_x, 2),
                "size_y": round(bbox.size_y, 2),
                "size_z": round(bbox.size_z, 2),
                "triangles": tri_count,
                "footprint_mm2": round(bbox.footprint_area_2d, 1),
            }
        except Exception:
            geom = None

        parts.append({
            "filename": p.file_path.name,
            "stem": p.stem,
            "is_repaired": p.is_repaired,
            "is_multipart": p.is_multipart,
            "component_index": p.component_index,
            "base_design_key": p.base_design_key,
            "file_size_bytes": p.file_size_bytes,
            "geometry": geom,
        })

    return {
        "date": date or datetime.now().strftime("%d.%m.%Y"),
        "directory": str(scan_result.target_directory),
        "exists": True,
        "parts": parts,
        "quarantined": [f.name for f in scan_result.quarantined_files],
        "incomplete_designs": {k: [f.name for f in v] for k, v in scan_result.incomplete_designs.items()},
    }


@app.post("/api/pack")
async def pack_plate(req: PackRequest):
    """Trigger the full pipeline orchestrator (Scout → Tactician → Vulcan → Sentry → Marshal)."""
    # Apply overrides
    _config.mode = StrategyMode(req.mode)
    _config.clearance_buffer = max(1.5, req.clearance)

    override_dir = Path(req.directory) if req.directory else None

    try:
        summary = _orchestrator.run(date_str=req.date, target_directory_override=override_dir)
    except Exception as err:
        raise HTTPException(500, f"Pipeline error: {err}")

    plates_data = []
    for plate in summary.plates:
        verification = None
        if plate.verification_report:
            vr = plate.verification_report
            verification = {
                "passed": vr.passed,
                "total_parts_checked": vr.total_parts_checked,
                "envelope_violations": vr.envelope_violations,
                "clearance_violations_count": len(vr.clearance_violations),
                "has_hard_collisions": vr.has_hard_collisions,
            }

        plates_data.append({
            "plate_index": plate.plate_index,
            "plate_name": plate.plate_name,
            "mode": plate.mode.value,
            "part_count": len(plate.packed_files),
            "packed_files": [p.name for p in plate.packed_files],
            "merged_stl": str(plate.merged_plate_stl) if plate.merged_plate_stl else None,
            "fabbproject": str(plate.fabbproject_path) if plate.fabbproject_path else None,
            "verification": verification,
        })

    strategy_info = None
    if summary.strategy_eval:
        se = summary.strategy_eval
        strategy_info = {
            "mode": se.selected_mode.value,
            "reason": se.reason,
            "footprint_utilization": round(se.footprint_utilization_ratio, 4),
            "batch_homogeneity": round(se.batch_homogeneity, 4),
            "estimated_plates": se.estimated_plates_required,
        }

    return {
        "success": summary.success,
        "date": summary.date_str,
        "directory": str(summary.target_directory),
        "mode_selected": summary.mode_selected,
        "total_scanned_parts": summary.total_scanned_parts,
        "total_valid_parts": summary.total_valid_parts,
        "total_quarantined": summary.total_quarantined,
        "total_plates_generated": summary.total_plates_generated,
        "plates": plates_data,
        "strategy": strategy_info,
        "manifest_path": str(summary.manifest_path) if summary.manifest_path else None,
        "duration_seconds": round(summary.duration_seconds, 2),
    }


@app.get("/api/plates")
async def list_plates(date: str | None = Query(None)):
    """List generated plate directories and their manifests for a date."""
    date_dir = _resolve_date_dir(date)
    if not date_dir.exists():
        return {"date": date, "plates": []}

    plates = []
    for child in sorted(date_dir.iterdir()):
        if child.is_dir() and child.name.startswith("Plate_"):
            manifest_file = child / "plate_manifest.json"
            summary_files = list(child.glob("plate_*_summary.json"))
            fabbprojects = list(child.glob("*.fabbproject"))
            merged_stls = list(child.glob("merged_*.stl"))

            manifest_data = None
            if manifest_file.exists():
                try:
                    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            elif summary_files:
                try:
                    manifest_data = json.loads(summary_files[0].read_text(encoding="utf-8"))
                except Exception:
                    pass

            plates.append({
                "plate_name": child.name,
                "directory": str(child),
                "has_manifest": manifest_data is not None,
                "manifest": manifest_data,
                "fabbproject": str(fabbprojects[0]) if fabbprojects else None,
                "merged_stl": str(merged_stls[0]) if merged_stls else None,
                "files": [f.name for f in child.iterdir() if f.is_file()],
            })

    # Also check root-level manifest
    root_manifest = date_dir / "plate_manifest.json"
    root_manifest_data = None
    if root_manifest.exists():
        try:
            root_manifest_data = json.loads(root_manifest.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "date": date or datetime.now().strftime("%d.%m.%Y"),
        "directory": str(date_dir),
        "root_manifest": root_manifest_data,
        "plates": plates,
    }


@app.post("/api/open-netfabb")
async def open_netfabb(req: OpenNetfabbRequest):
    """Open a .fabbproject in the native Netfabb GUI."""
    fabb_path = Path(req.fabbproject_path)
    if not fabb_path.exists():
        raise HTTPException(404, f"File not found: {fabb_path}")

    netfabb_gui = Path(_config.netfabb_executable).parent / "netfabb.exe"
    if not netfabb_gui.is_file():
        raise HTTPException(503, f"Netfabb GUI not found: {netfabb_gui}")

    try:
        # Use subprocess.Popen in a non-blocking way for GUI launch
        # This is intentionally synchronous — we just fire-and-forget the GUI process.
        subprocess.Popen([str(netfabb_gui), str(fabb_path)], shell=False)  # noqa: ASYNC220
    except Exception as err:
        raise HTTPException(500, f"Failed to launch Netfabb: {err}")

    return {"status": "launched", "path": str(fabb_path)}


@app.get("/api/download/{plate_name}/{filename}")
async def download_file(plate_name: str, filename: str, date: str | None = Query(None)):
    """Stream a file from a plate directory."""
    date_dir = _resolve_date_dir(date)
    target = date_dir / plate_name / filename

    if not target.exists():
        raise HTTPException(404, f"File not found: {plate_name}/{filename}")

    media_type = "application/octet-stream"
    if filename.endswith(".json"):
        media_type = "application/json"
    elif filename.endswith(".stl"):
        media_type = "model/stl"

    return FileResponse(str(target), media_type=media_type, filename=filename)
