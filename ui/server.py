"""server.py — FastAPI REST backend for LP Agent Web UI.

Thin wrapper around the existing pipeline orchestrator.
Serves the static frontend and exposes JSON endpoints for scan, pack, and inspect.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.lp_config import MIN_CLEARANCE_MM, LPConfig, StrategyMode
from core.height_policy import (
    ASK,
    DEFAULT_UNATTENDED_POLICY,
    EXCEED,
    SPLIT,
    VALID_POLICIES,
    DecideFn,
    HeightCapExceeded,
    HeightDecision,
)
from core.native_packer import inspect_stl
from core.quantity import parse_quantity
from pipeline.orchestrator import LPOrchestrator
from utils.logger import Logger

# ── App ──────────────────────────────────────────────────────────
APP_VERSION = "1.1.0"

app = FastAPI(title="LP Agent", version=APP_VERSION)

# Serve static frontend assets
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared config & orchestrator (lightweight singletons)
_config = LPConfig()
_orchestrator = LPOrchestrator(_config)

# When this process loaded its code. Surfaced in /api/status so an operator can tell at a
# glance whether the server predates a change they are expecting to see.
_SERVER_STARTED_AT = datetime.now().isoformat(timespec="seconds")


# ── Pydantic models ──────────────────────────────────────────────
class PackRequest(BaseModel):
    """Request body for /api/pack.

    The spacing fields let the operator trade plate density against safety without
    editing config: `clearance` is part-to-part, `border_spacing_xy` is the keep-out
    margin at the platform edge.
    """

    date: str | None = None
    mode: str = "auto"
    clearance: float = 2.0
    border_spacing_xy: float | None = None
    border_spacing_z: float | None = None
    # Vertical gap between stacked tiers in 3D packing. Wider than the XY gap by default,
    # because support wax fills it and has to be washed back out.
    layer_gap_z: float | None = None
    # Preferred ceiling for a stacked plate. Taller plates are harder to clean, so the
    # operator sets this rather than the agent assuming the machine's 100 mm is acceptable.
    max_plate_height: float | None = None
    # "ask" (default) pauses for the operator; see core.height_policy.
    on_cap_exceeded: str | None = None
    directory: str | None = None
    wait: bool = False  # Block until the run finishes (used by scripts and tests)


class CapDecisionRequest(BaseModel):
    """Answer to a pending height-cap decision."""

    choice: str  # SPLIT or EXCEED


class OpenNetfabbRequest(BaseModel):
    """Request body for /api/open-netfabb."""

    fabbproject_path: str


# ── Pack job registry ────────────────────────────────────────────
#
# Packing a real batch takes minutes, so /api/pack cannot be a blocking request: the
# browser times out and the operator gets no feedback while Netfabb works. Runs are
# tracked here and polled through /api/pack/status/{job_id}.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_MAX_JOB_HISTORY = 20


def _format_duration(seconds: float | None) -> str:
    """Render a duration as H:MM:SS or M:SS, matching how the floor reads run times."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = round(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _new_job() -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        # Trim history so a long-lived server does not accumulate finished jobs forever.
        if len(_jobs) >= _MAX_JOB_HISTORY:
            for stale in sorted(_jobs, key=lambda k: _jobs[k]["started_monotonic"])[:5]:
                if _jobs[stale]["status"] != "running":
                    _jobs.pop(stale, None)
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "stage": "Starting",
            "progress": 0.0,
            "started_monotonic": time.monotonic(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "result": None,
            "error": None,
            # Set while the run is paused waiting for a height-cap answer.
            "decision": None,
            "_decision_event": None,
            "_decision_choice": None,
        }
    return job_id


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)


def _job_snapshot(job_id: str) -> dict | None:
    """Public view of a job, with elapsed and ETA derived at read time."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job = dict(job)

    # Internal synchronisation objects are not part of the public view.
    job.pop("_decision_event", None)
    job.pop("_decision_choice", None)

    elapsed = time.monotonic() - job.pop("started_monotonic")
    progress = job.get("progress") or 0.0

    # ETA is only meaningful once enough of the run has elapsed to extrapolate from.
    eta = None
    if job["status"] == "running" and progress > 0.03:
        eta = max(0.0, elapsed / progress - elapsed)
    elif job["status"] != "running":
        eta = 0.0

    job["elapsed_seconds"] = round(elapsed, 1)
    job["elapsed_human"] = _format_duration(elapsed)
    job["eta_seconds"] = round(eta, 1) if eta is not None else None
    job["eta_human"] = _format_duration(eta) if eta is not None else "—"
    job["progress_percent"] = round(progress * 100, 1)
    return job


# ── Helpers ──────────────────────────────────────────────────────
def _resolve_target_dir(target_str: str | None = None) -> Path:
    """Resolve either an explicit directory path or a date string to a directory Path."""
    if not target_str:
        printing_root = _config.resolve_printing_root()
        dt = datetime.now()
        return printing_root / dt.strftime("%B") / dt.strftime("%d.%m.%Y")

    s = target_str.strip()

    # Check if target_str is a filesystem path (contains slashes, colons, or exists)
    if s.startswith(("\\\\", "//")) or "\\" in s or "/" in s or ":" in s or Path(s).is_dir():
        # Check if path points to \\192.1.1.131\cad and map to Q:\ if available
        normalized = s.replace("/", "\\")
        if normalized.lower().startswith(r"\\192.1.1.131\cad"):
            rel = normalized[len(r"\\192.1.1.131\cad") :].lstrip("\\")
            q_candidate = Path("Q:\\") / rel
            if q_candidate.exists():
                return q_candidate
        return Path(s)

    # Otherwise, try parsing as a date string
    try:
        dt = datetime.strptime(s, "%d.%m.%Y")
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                400,
                f"Invalid date format or directory path: {s}. Use DD.MM.YYYY or a valid folder path.",
            )

    printing_root = _config.resolve_printing_root()
    month_name = dt.strftime("%B")
    date_str = dt.strftime("%d.%m.%Y")
    year_str = dt.strftime("%Y")

    candidates = [
        # Candidate 1: Standard Printing/<Month>/<Date>/Agent
        printing_root / month_name / date_str,
        # Candidate 2: MJP-2500W Printer Queue (Browns CAD Storage structure)
        printing_root / "Form2" / "MJP-2500W" / year_str / month_name / date_str,
        # Candidate 3: Year-prefixed Printing/<Year>/<Month>/<Date>
        printing_root / year_str / month_name / date_str,
        # Candidate 4: Alternate MJP root
        printing_root / "MJP-2500W" / year_str / month_name / date_str,
    ]

    from core.date_scanner import ensure_windows_share_online

    for candidate in candidates:
        if candidate.exists():
            return candidate
        ensure_windows_share_online(candidate)
        if candidate.exists():
            return candidate

    # Default fallback to candidate 1
    return candidates[0]


def _resolve_date_dir(date_str: str | None = None) -> Path:
    """Backward-compatible wrapper for date or directory resolution."""
    return _resolve_target_dir(date_str)


# ── Endpoints ────────────────────────────────────────────────────
@app.get("/")
def index():
    """Serve the main UI page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(500, "Frontend not found.")
    return FileResponse(str(index_path), media_type="text/html")


@app.get("/api/status")
def get_status():
    """Return machine config, environment health, and default paths."""
    printing_root = _config.resolve_printing_root()
    netfabb_bin = Path(_config.netfabb_executable)
    netfabb_gui = netfabb_bin.parent / "netfabb.exe"

    return {
        # Version and boot time of the *running* process. A server started before a code
        # change keeps serving the old modules (uvicorn runs without --reload), and the
        # symptom is indistinguishable from "the fix did not work" -- so the UI shows this.
        "app_version": APP_VERSION,
        "server_started_at": _SERVER_STARTED_AT,
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
            "layer_gap_z_mm": _config.layer_gap_z,
            "max_plate_height_mm": _config.max_plate_height_mm,
            "machine_max_height_mm": _config.platform_z,
            "min_clearance_mm": MIN_CLEARANCE_MM,
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
def scan_date(
    date: str | None = Query(None, description="Date string (DD.MM.YYYY) or directory path"),
    directory: str | None = Query(None, description="Explicit directory path"),
):
    """Execute Scout (DateScanner) on the target date folder or arbitrary directory."""
    target_str = directory or date
    date_dir = _resolve_target_dir(target_str)

    if not date_dir.exists():
        return {
            "date": date or datetime.now().strftime("%d.%m.%Y"),
            "directory": str(date_dir),
            "exists": False,
            "parts": [],
            "quarantined": [],
            "incomplete_designs": {},
        }

    # If date_dir has an Agent subfolder, scan it; otherwise scan date_dir directly
    scan_target = date_dir / "Agent" if (date_dir / "Agent").is_dir() else date_dir
    scan_result = _orchestrator.scout.scan_directory(scan_target)

    parts = []
    for p in scan_result.valid_parts:
        geom = None
        est_triangles = max(0, (p.file_size_bytes - 84) // 50) if p.file_size_bytes > 84 else 0

        # Only provide detailed vertex bounds if already cached to keep scan instantaneous.
        # The key includes mtime, so an edited file falls back to "not measured yet"
        # rather than reporting the previous version's dimensions.
        try:
            mtime = int(p.file_path.stat().st_mtime_ns)
        except OSError:
            mtime = 0
        cache_key = (p.file_path.name, p.file_size_bytes, mtime)
        _orchestrator.tactician._ensure_cache_loaded()
        if cache_key in _orchestrator.tactician._AABB_CACHE:
            bbox, tri_count = _orchestrator.tactician._AABB_CACHE[cache_key]
            geom = {
                "size_x": round(bbox.size_x, 2),
                "size_y": round(bbox.size_y, 2),
                "size_z": round(bbox.size_z, 2),
                "triangles": tri_count,
                "footprint_mm2": round(bbox.footprint_area_2d, 1),
            }
        else:
            geom = {
                "size_x": "—",
                "size_y": "—",
                "size_z": "—",
                "triangles": est_triangles,
                "footprint_mm2": 0,
            }

        parts.append({
            "filename": p.file_path.name,
            "stem": p.stem,
            "is_repaired": p.is_repaired,
            "is_multipart": p.is_multipart,
            "component_index": p.component_index,
            "base_design_key": p.base_design_key,
            "file_size_bytes": p.file_size_bytes,
            # How many copies the trailing xN suffix asks for (1 when absent).
            "quantity": parse_quantity(p.file_path.name),
            "geometry": geom,
        })

    total_instances = sum(p["quantity"] for p in parts)

    return {
        "date": date or datetime.now().strftime("%d.%m.%Y"),
        "directory": str(scan_result.target_directory),
        "exists": True,
        "parts": parts,
        "total_files": len(parts),
        "total_instances": total_instances,
        "quarantined": [f.name for f in scan_result.quarantined_files],
        "incomplete_designs": {k: [f.name for f in v] for k, v in scan_result.incomplete_designs.items()},
    }


def _summary_to_payload(summary) -> dict:
    """Serialise a PipelineExecutionSummary for the UI, including build evidence."""
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
                # TrueShape nests on outlines, so its box overlaps are advisory. The UI
                # must not show them as collisions.
                "clearance_is_authoritative": vr.clearance_is_authoritative,
                "clearance_note": vr.clearance_note,
            }

        run = plate.netfabb_result
        stl_info = inspect_stl(plate.merged_plate_stl) if plate.merged_plate_stl else None

        plates_data.append({
            "plate_index": plate.plate_index,
            "plate_name": plate.plate_name,
            "mode": plate.mode.value,
            "part_count": len(plate.packed_files),
            "packed_files": [p.name for p in plate.packed_files],
            "merged_stl": str(plate.merged_plate_stl) if plate.merged_plate_stl else None,
            "fabbproject": str(plate.fabbproject_path) if plate.fabbproject_path else None,
            "verification": verification,
            # Is the plate actually built? Kept separate from `verification`, which only
            # judges the layout and once reported PASS for an empty placeholder file.
            "build": {
                "plate_built": bool(run and run.plate_built),
                "builder": run.builder if run else "none",
                "merged_stl_bytes": stl_info["size_bytes"] if stl_info else 0,
                "merged_stl_triangles": stl_info["triangles"] if stl_info else 0,
                "stl_format": stl_info["format"] if stl_info else "missing",
                "duration_seconds": round(run.duration_seconds, 2) if run else 0.0,
                "duration_human": _format_duration(run.duration_seconds if run else 0.0),
                "timed_out": bool(run and run.timed_out),
                "failure_reason": (run.failure_reason if run else "") or (
                    stl_info["reason"] if stl_info else ""
                ),
                # 3D outcome: tiers and plate height. Height is the number that predicts
                # WaxJet print time, so the operator sees it before sending the plate.
                "layers_used": run.layers_used if run else 1,
                "plate_height_mm": round(run.plate_height_mm, 2) if run else 0.0,
            },
            # Real packed positions, so the viewport can draw where parts actually are
            # instead of re-inventing its own grid.
            "placed_boxes": [
                {
                    "name": b[0],
                    "min_x": b[1], "max_x": b[2],
                    "min_y": b[3], "max_y": b[4],
                    "min_z": b[5], "max_z": b[6],
                }
                for b in (run.placed_boxes if run else [])
            ],
        })

    strategy_info = None
    if summary.strategy_eval:
        se = summary.strategy_eval
        strategy_info = {
            "mode": se.selected_mode.value,
            "reason": se.reason,
            "footprint_utilization": round(se.footprint_utilization_ratio, 4),
            "batch_homogeneity": round(se.batch_homogeneity, 4),
            "repeat_ratio": round(se.repeat_ratio, 4),
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
        "duration_human": _format_duration(summary.duration_seconds),
        "error_message": summary.error_message,
    }


# How long a paused run waits for a height-cap answer before taking the safe course.
# Without a bound, an operator who closes the tab leaves a worker thread parked forever.
_DECISION_TIMEOUT_SECONDS = 15 * 60


def _make_decider(job_id: str) -> DecideFn:
    """Build a decider that parks the run until the operator answers.

    The choice is made on a costed plan, before any merging, so pausing here delays only
    the cheap part of the work.
    """

    def ask(decision: HeightDecision) -> str:
        event = threading.Event()
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return DEFAULT_UNATTENDED_POLICY
            job["_decision_event"] = event
            job["_decision_choice"] = None
            job["decision"] = decision.as_dict()
            job["status"] = "awaiting_decision"
            job["stage"] = f"{decision.plate_name}: waiting on height decision"

        answered = event.wait(timeout=_DECISION_TIMEOUT_SECONDS)

        with _jobs_lock:
            job = _jobs.get(job_id)
            choice = (job or {}).get("_decision_choice")
            if job is not None:
                job["decision"] = None
                job["_decision_event"] = None
                job["status"] = "running"
                job["stage"] = f"{decision.plate_name}: packing"

        if not answered or choice not in (SPLIT, EXCEED):
            Logger.warning(
                f"[LP UI] No height-cap answer for {decision.plate_name} within "
                f"{_DECISION_TIMEOUT_SECONDS // 60} min; respecting the cap."
            )
            return DEFAULT_UNATTENDED_POLICY
        return choice

    return ask


def _run_pack_job(job_id: str, req: PackRequest, scan_target: Path) -> None:
    """Execute one packing run on a worker thread, publishing progress as it goes."""
    # A per-job config and orchestrator: the module singletons are shared across requests,
    # so mutating them here would let one operator's spacing change leak into another's run.
    cfg = LPConfig()
    cfg.mode = StrategyMode(req.mode)
    cfg.clearance_buffer = max(MIN_CLEARANCE_MM, req.clearance)
    if req.border_spacing_xy is not None:
        cfg.border_spacing_xy = max(0.0, req.border_spacing_xy)
    if req.border_spacing_z is not None:
        cfg.border_spacing_z = max(0.0, req.border_spacing_z)
    if req.layer_gap_z is not None:
        cfg.layer_gap_z = max(MIN_CLEARANCE_MM, req.layer_gap_z)
    if req.max_plate_height is not None:
        cfg.max_plate_height_mm = min(
            max(MIN_CLEARANCE_MM, req.max_plate_height), cfg.platform_z
        )
    if req.on_cap_exceeded in VALID_POLICIES:
        cfg.on_cap_exceeded = req.on_cap_exceeded

    orchestrator = LPOrchestrator(cfg)

    def on_progress(stage: str, fraction: float) -> None:
        _update_job(job_id, stage=stage, progress=max(0.0, min(1.0, fraction)))

    # Only the interactive path can prompt; a blocking `wait=true` caller has no way to
    # answer mid-request, so it falls back to the unattended policy.
    decider = _make_decider(job_id) if (cfg.on_cap_exceeded == ASK and not req.wait) else None

    try:
        summary = orchestrator.run(
            date_str=req.date,
            target_directory_override=scan_target,
            progress=on_progress,
            decide=decider,
        )
    except HeightCapExceeded as err:
        _update_job(job_id, status="failed", error=str(err), progress=1.0,
                    stage="Height cap exceeded")
        return
    except Exception as err:
        # Never let a worker thread die silently: the operator is polling for this job and
        # would otherwise watch it sit at whatever percentage it reached.
        _update_job(job_id, status="error", error=f"Pipeline error: {err}", progress=1.0,
                    stage="Failed")
        return

    payload = _summary_to_payload(summary)
    _update_job(
        job_id,
        status="done" if summary.success else "failed",
        stage="Complete" if summary.success else "Finished with problems",
        progress=1.0,
        result=payload,
        error=summary.error_message,
    )


@app.post("/api/pack")
def pack_plate(req: PackRequest):
    """Start a packing run (Scout → Tactician → Vulcan → Sentry → Marshal).

    Returns a job handle immediately; poll /api/pack/status/{job_id} for progress. Pass
    `wait: true` to block until the run finishes and get the result inline.
    """
    target_str = req.directory or req.date
    date_dir = _resolve_target_dir(target_str)
    scan_target = date_dir / "Agent" if (date_dir / "Agent").is_dir() else date_dir

    job_id = _new_job()

    if req.wait:
        _run_pack_job(job_id, req, scan_target)
        snapshot = _job_snapshot(job_id) or {}
        if snapshot.get("status") == "error":
            raise HTTPException(500, snapshot.get("error") or "Pipeline error")
        return snapshot.get("result") or snapshot

    worker = threading.Thread(
        target=_run_pack_job, args=(job_id, req, scan_target), daemon=True,
        name=f"lp-pack-{job_id}",
    )
    worker.start()

    return {
        "job_id": job_id,
        "status": "running",
        "directory": str(scan_target),
        "poll_url": f"/api/pack/status/{job_id}",
    }


@app.get("/api/pack/status/{job_id}")
def pack_status(job_id: str):
    """Progress, elapsed time and ETA for a packing run.

    When `status` is "awaiting_decision", `decision` holds the two costed plans and the run
    is paused until /api/pack/decide is called.
    """
    snapshot = _job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(404, f"Unknown pack job: {job_id}")
    return snapshot


@app.post("/api/pack/decide/{job_id}")
def pack_decide(job_id: str, req: CapDecisionRequest):
    """Answer a pending height-cap decision and let the run continue."""
    if req.choice not in (SPLIT, EXCEED):
        raise HTTPException(400, f"choice must be '{SPLIT}' or '{EXCEED}'")

    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"Unknown pack job: {job_id}")
        event = job.get("_decision_event")
        if event is None:
            raise HTTPException(409, "This run is not waiting for a height decision")
        job["_decision_choice"] = req.choice

    event.set()
    return {"job_id": job_id, "choice": req.choice, "status": "resumed"}


@app.get("/api/plates")
def list_plates(
    date: str | None = Query(None),
    directory: str | None = Query(None),
):
    """List generated plate directories and their manifests for a date or directory."""
    date_dir = _resolve_target_dir(directory or date)
    if not date_dir.exists():
        return {"date": date, "directory": str(date_dir), "plates": []}

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

            # Inspect the STL on disk rather than trusting the manifest: this is what
            # exposes a placeholder plate left behind by an older run.
            stl_info = inspect_stl(merged_stls[0]) if merged_stls else {
                "built": False, "size_bytes": 0, "triangles": 0,
                "format": "missing", "reason": "No merged plate STL in folder",
            }

            plates.append({
                "plate_name": child.name,
                "directory": str(child),
                "has_manifest": manifest_data is not None,
                "manifest": manifest_data,
                "fabbproject": str(fabbprojects[0]) if fabbprojects else None,
                "merged_stl": str(merged_stls[0]) if merged_stls else None,
                "files": [f.name for f in child.iterdir() if f.is_file()],
                "build": {
                    "plate_built": stl_info["built"],
                    "merged_stl_bytes": stl_info["size_bytes"],
                    "merged_stl_triangles": stl_info["triangles"],
                    "stl_format": stl_info["format"],
                    "failure_reason": stl_info["reason"],
                },
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


@app.get("/api/plate-layout")
def plate_layout(
    plate: str = Query(..., description="Plate folder name, e.g. Plate_01"),
    date: str | None = Query(None),
    directory: str | None = Query(None),
):
    """Real packed positions and build status for one plate on disk.

    This is the source of truth for the viewport. Reading the audit JSON means the UI
    draws where parts were actually placed, rather than re-deriving a layout in the
    browser that nothing on the plate has to agree with.
    """
    date_dir = _resolve_target_dir(directory or date)
    plate_dir = date_dir / plate

    if not plate_dir.is_dir():
        raise HTTPException(404, f"Plate folder not found: {plate_dir}")

    # Prefer the audit written by whichever packer ran; fall back to the plate summary.
    placed_boxes: list[dict] = []
    packed_files: list[str] = []
    leftover_files: list[str] = []
    builder = "unknown"
    fallback_reason = ""

    audit_files = sorted(plate_dir.glob("audit_results_*.json"))
    summary_files = sorted(plate_dir.glob("plate_*_summary.json"))

    for source in audit_files + summary_files:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except Exception as err:
            # A malformed audit is not fatal; try the next source before giving up.
            print(f"[LP UI] Skipping unreadable layout source {source.name}: {err}")
            continue
        if data.get("placed_boxes"):
            placed_boxes = data["placed_boxes"]
            packed_files = data.get("packed_files", [])
            leftover_files = data.get("leftover_files", [])
            builder = data.get("builder") or ("lp-native" if data.get("simulated") else "netfabb")
            fallback_reason = data.get("fallback_reason", "")
            break

    merged_stls = sorted(plate_dir.glob("merged_*.stl"))
    merged_stl = merged_stls[0] if merged_stls else None
    stl_info = inspect_stl(merged_stl) if merged_stl else {
        "exists": False, "built": False, "size_bytes": 0,
        "triangles": 0, "format": "missing", "reason": "No merged plate STL in folder",
    }

    fabbprojects = sorted(plate_dir.glob("*.fabbproject"))

    return {
        "plate_name": plate,
        "directory": str(plate_dir),
        "envelope_mm": {
            "x": _config.platform_x,
            "y": _config.platform_y,
            "z": _config.platform_z,
        },
        "builder": builder,
        "fallback_reason": fallback_reason,
        "packed_files": packed_files,
        "leftover_files": leftover_files,
        "placed_boxes": placed_boxes,
        "merged_stl": str(merged_stl) if merged_stl else None,
        "fabbproject": str(fabbprojects[0]) if fabbprojects else None,
        "build": {
            "plate_built": stl_info["built"],
            "merged_stl_bytes": stl_info["size_bytes"],
            "merged_stl_triangles": stl_info["triangles"],
            "stl_format": stl_info["format"],
            "failure_reason": stl_info["reason"],
        },
    }


@app.post("/api/open-netfabb")
def open_netfabb(req: OpenNetfabbRequest):
    """Open a .fabbproject in the native Netfabb GUI."""
    fabb_path = Path(req.fabbproject_path)
    if not fabb_path.exists():
        raise HTTPException(404, f"File not found: {fabb_path}")

    netfabb_gui = Path(_config.netfabb_executable).parent / "netfabb.exe"
    if not netfabb_gui.is_file():
        raise HTTPException(503, f"Netfabb GUI not found: {netfabb_gui}")

    try:
        subprocess.Popen([str(netfabb_gui), str(fabb_path)], shell=False)
    except Exception as err:
        raise HTTPException(500, f"Failed to launch Netfabb: {err}")

    return {"status": "launched", "path": str(fabb_path)}


@app.get("/api/download/{plate_name}/{filename}")
def download_file(
    plate_name: str,
    filename: str,
    date: str | None = Query(None),
    directory: str | None = Query(None),
):
    """Stream a file from a plate directory."""
    date_dir = _resolve_target_dir(directory or date)
    target = date_dir / plate_name / filename

    if not target.exists():
        raise HTTPException(404, f"File not found: {plate_name}/{filename}")

    media_type = "application/octet-stream"
    if filename.endswith(".json"):
        media_type = "application/json"
    elif filename.endswith(".stl"):
        media_type = "model/stl"

    return FileResponse(str(target), media_type=media_type, filename=filename)
