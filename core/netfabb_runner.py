"""netfabb_runner.py — Subagent Vulcan: Netfabb Lua Automation Runner.

Generates calibrated TrueShape nesting Lua scripts, executes headless Autodesk
Netfabb 2027 (netfabb_console.exe), and captures plate packing manifests.

When Netfabb cannot deliver a plate, this module falls back to `core.native_packer`,
which packs and merges the parts in-process and writes a real STL. It never publishes a
placeholder plate: see the module docstring in native_packer.py for the incident that
rule comes from.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.native_packer import (
    NativePackResult,
    inspect_stl,
    pack_shelf,
    write_merged_stl,
)
from core.quantity import PartInstance, as_instances
from utils.logger import Logger

ProgressFn = Callable[[str, float], None]

# Netfabb's Lua prints these so the Python side can report live progress instead of
# leaving the operator watching a spinner for ten minutes.
PROGRESS_TOKEN = "LPPROGRESS|"


class NetfabbExecutionError(RuntimeError):
    """Raised when Netfabb console fails or times out."""


@dataclass
class NetfabbRunResult:
    """Outcome of a headless Netfabb nesting run."""

    success: bool
    exit_code: int
    packed_files: list[str] = field(default_factory=list)
    leftover_files: list[str] = field(default_factory=list)
    placed_boxes: list[tuple[str, float, float, float, float, float, float]] = field(default_factory=list)
    merged_plate_stl: Path | None = None
    fabbproject_path: Path | None = None
    stdout: str = ""
    stderr: str = ""

    # Provenance and build evidence.
    #
    # `builder` says which engine actually produced the plate ("netfabb", "lp-native", or
    # "none"), and `plate_built` says whether the merged STL on disk really holds geometry.
    # Callers and the UI must key trust off these, not off `success` alone -- the old code
    # returned success=True for an empty stub plate and the manifest duly reported PASS.
    builder: str = "netfabb"
    plate_built: bool = False
    merged_stl_bytes: int = 0
    merged_stl_triangles: int = 0
    failure_reason: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    timeout_budget_seconds: float = 0.0

    # 3D packing outcome. Plate height governs WaxJet print time far more than part count,
    # so it is reported alongside the tier count rather than left to be derived.
    layers_used: int = 1
    plate_height_mm: float = 0.0


class NetfabbRunner:
    """Subagent Vulcan: Spawns and monitors netfabb_console.exe with dynamic Lua scripts."""

    LUA_TEMPLATE = """-- ==============================================================================
-- Autodesk Netfabb 2026/2027 Headless Nesting Script for Flashforge WaxJet 51C
-- Generated automatically by LP Agent Subagent Vulcan
-- ==============================================================================

system:setloggingtooglwindow(false)
system:logtofile([[{LOG_FILE_PATH}]])

print("==============================================================================")
print("LP AGENT VULCAN - Flashforge WaxJet 51C Nesting Routine")
print("==============================================================================")

local MACHINE_X = {PLATFORM_X}
local MACHINE_Y = {PLATFORM_Y}
local MACHINE_Z = {PLATFORM_Z}
local CLEARANCE = {CLEARANCE}
local BORDER_XY = {BORDER_XY}
local BORDER_Z  = {BORDER_Z}
local VOXEL_RES = {VOXEL_RES}
local ROT_STEP  = {ROTATION_Z}
local IS_2D     = {IS_2D_MODE}

-- Emit machine-readable progress so LP Agent can show real advancement, not a spinner.
local function lp_progress(stage, current, total)
    print(string.format("LPPROGRESS|%s|%d|%d", stage, current, total))
end

-- 1. Initialize Tray and Platform Root
local root = tray.root
tray.machinesize_x = MACHINE_X
tray.machinesize_y = MACHINE_Y
tray.machinesize_z = MACHINE_Z

-- 2. Ingest Repaired STL Files
local stl_files = {
{STL_FILES_ARRAY}
}

local loaded_count = 0
for i, file_path in ipairs(stl_files) do
    lp_progress("load", i, #stl_files)
    print(string.format("Loading [%d/%d]: %s", i, #stl_files, file_path))
    local mesh_file = system:loadstl(file_path)
    if mesh_file ~= nil then
        -- Keep the extension: LP Agent reconciles packed parts by filename, and a name
        -- stripped to its stem matches nothing on the way back.
        local part_name = string.match(file_path, "([^/\\\\]+)$") or ("part_" .. i)
        local tm = root:addmesh(mesh_file, part_name)
        loaded_count = loaded_count + 1
    else
        local err_msg = "ERROR: Failed to load mesh: " .. file_path
        print(err_msg)
        system:log(err_msg)
    end
end

local load_status = string.format("Loaded %d/%d parts into tray.", loaded_count, #stl_files)
print(load_status)
system:log(load_status)

if loaded_count == 0 then
    print("FATAL: No parts loaded. Exiting.")
    if os and os.exit then os.exit(2) else error("No parts loaded") end
end

-- 3. Initialize Packer
local packer = nil
if IS_2D then
    print("Configuring 2D TrueShape Flat Nesting packer...")
    packer = tray:createpacker(tray.packingid_2d)
    if packer ~= nil then
        packer.rastersize = VOXEL_RES
        -- Angle count is derived from the configured Z rotation step so the operator's
        -- setting actually reaches the packer (it used to be hardcoded to 4).
        packer.anglecount = math.max(1, math.floor(360 / ROT_STEP))
        packer.coarsening = 1
        packer.borderspacingxy = BORDER_XY
        -- Part-to-part clearance applies in 2D as well; without this the 2D branch nested
        -- prongs hard against each other regardless of the configured buffer.
        packer.minimaldistance = CLEARANCE
    end
else
    print("Configuring 3D Monte Carlo / Outbox packer...")
    packer = tray:createpacker(tray.packingid_montecarlo)
    if packer == nil then
        packer = tray:createpacker(tray.packingid_outbox)
    end
    if packer ~= nil then
        packer.borderspacingxy = BORDER_XY
        packer.borderspacingz = BORDER_Z
        packer.minimaldistance = CLEARANCE
    end
end

if packer == nil then
    packer = tray:createpacker(tray.packingid_outbox)
end

-- 4. Execute Packing Algorithm
print("Starting nesting calculation...")
lp_progress("pack", 0, 1)
system:log("Starting nesting calculation...")
local errorcode = packer:pack()
lp_progress("pack", 1, 1)
local pack_status = "Packing completed with return code: " .. tostring(errorcode)
print(pack_status)
system:log(pack_status)

-- 5. Audit Plate Allocation and Leftovers
--
-- The packer leaves anything it could not place outside the machine envelope, so
-- membership is decided by testing each part's outbox against the platform bounds.
-- The previous revision never populated leftover_parts at all, so overflow parts were
-- reported as packed and silently fell off the plate instead of starting Plate 02.
local packed_parts = {}
local leftover_parts = {}
local EPS = 0.001

for idx = 0, root.meshcount - 1 do
    local tm = root:getmesh(idx)
    local ob = tm.outbox
    local inside = (ob.minx >= -EPS) and (ob.miny >= -EPS) and (ob.minz >= -EPS)
               and (ob.maxx <= MACHINE_X + EPS)
               and (ob.maxy <= MACHINE_Y + EPS)
               and (ob.maxz <= MACHINE_Z + EPS)
    if inside then
        table.insert(packed_parts, tm)
    else
        table.insert(leftover_parts, tm)
        print("Leftover (outside envelope): " .. tostring(tm.name))
    end
end

local audit_status = string.format("Nesting Results: %d packed, %d leftover.", #packed_parts, #leftover_parts)
print(audit_status)
system:log(audit_status)

-- 6. Emit the packing audit on stdout, BEFORE exporting.
--
-- Netfabb's Lua is sandboxed and has no `io` library, so this script cannot write a file
-- of its own -- an earlier revision called io.open here and died with
-- "attempt to index global 'io' (a nil value)", losing the audit every single run.
-- Printing is the only channel available, and LP Agent already captures this stream, so
-- the results are emitted as tagged lines and the JSON is assembled on the Python side.
--
-- Emitted before the export because the export is the expensive, fragile stage: if
-- merging or saving a multi-hundred-megabyte plate ends the session, the packing result
-- is already safely across.
--
-- The part name goes LAST on each line so that a delimiter inside a filename cannot
-- break the field parsing.
for _, tm in ipairs(packed_parts) do
    local ob = tm.outbox
    print(string.format("LPBOX|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f|%s",
        ob.minx, ob.maxx, ob.miny, ob.maxy, ob.minz, ob.maxz, tm.name))
end
for _, tm in ipairs(leftover_parts) do
    print("LPLEFT|" .. tostring(tm.name))
end
print(string.format("LPRESULT|%d|%d|%d", errorcode, #packed_parts, #leftover_parts))

-- 7. Export Merged Plate STL and Netfabb Project
if #packed_parts > 0 then
    print("Generating merged build plate STL...")
    local master_mesh = nil
    for i, tm in ipairs(packed_parts) do
        lp_progress("merge", i, #packed_parts)
        local part_mesh = tm.mesh:dupe()
        part_mesh:applymatrix(tm.matrix)
        if master_mesh == nil then
            master_mesh = part_mesh
        else
            master_mesh:merge(part_mesh)
        end
    end

    if master_mesh ~= nil then
        -- Note: intentionally omitted master_mesh:unify(0.01) to prevent freezing on multi-million facet plates
        master_mesh:savetostl([[{MERGED_PLATE_STL_PATH}]])
        local stl_status = "Merged build plate STL successfully written to: " .. [[{MERGED_PLATE_STL_PATH}]]
        print(stl_status)
        system:log(stl_status)
    end

    if application and application.savefabbproject then
        application:savefabbproject([[{FABBPROJECT_PATH}]])
        local proj_status = "Netfabb project saved to: " .. [[{FABBPROJECT_PATH}]]
        print(proj_status)
        system:log(proj_status)
    end
end

lp_progress("done", 1, 1)
print("LP Agent Netfabb routine completed successfully.")
system:log("LP Agent Netfabb routine completed successfully.")
"""

    # Explicit-placement script for 3D plates.
    #
    # Netfabb's own 3D packer is a Monte Carlo search that picks its own positions, which
    # would discard the per-design columns. So LP Agent computes the layout and Netfabb is
    # told exactly where each part goes -- no packer:pack() call at all. Netfabb still earns
    # its place here by producing the merged mesh and the .fabbproject, which the native
    # writer cannot.
    #
    # Placement reads the part's box back *after* rotating and translates from there, so it
    # lands exactly on target regardless of where Netfabb rotates about or where the mesh's
    # own origin sits.
    LUA_PLACEMENT_TEMPLATE = """-- ==============================================================================
-- LP Agent - explicit 3D placement for Flashforge WaxJet 51C
-- Positions computed by LP Agent's column packer; Netfabb places, merges and exports.
-- ==============================================================================

system:setloggingtooglwindow(false)
system:logtofile([[{LOG_FILE_PATH}]])

local function lp_progress(stage, current, total)
    print(string.format("LPPROGRESS|%s|%d|%d", stage, current, total))
end

local MACHINE_X = {PLATFORM_X}
local MACHINE_Y = {PLATFORM_Y}
local MACHINE_Z = {PLATFORM_Z}

local root = tray.root
tray.machinesize_x = MACHINE_X
tray.machinesize_y = MACHINE_Y
tray.machinesize_z = MACHINE_Z

-- Each entry: source file, display name, quarter-turn flag, and the target min corner.
local PLACEMENTS = {
{PLACEMENTS_ARRAY}
}

local placed = {}
for i, p in ipairs(PLACEMENTS) do
    lp_progress("place", i, #PLACEMENTS)
    local mesh_file = system:loadstl(p.file)
    if mesh_file == nil then
        print("ERROR: Failed to load mesh: " .. p.file)
    else
        local tm = root:addmesh(mesh_file, p.name)

        -- Angle is in radians; a quarter turn about Z swaps the footprint exactly.
        if p.rot == 1 then
            tm:rotate(0, 0, 1, math.pi / 2)
        end

        local o = tm.outbox
        tm:translate(p.x - o.minx, p.y - o.miny, p.z - o.minz)
        table.insert(placed, tm)
    end
end

print(string.format("Placed %d/%d parts.", #placed, #PLACEMENTS))
system:log(string.format("Placed %d/%d parts.", #placed, #PLACEMENTS))

if #placed == 0 then
    print("FATAL: Nothing placed.")
    if os and os.exit then os.exit(2) else error("Nothing placed") end
end

-- Report where the parts actually ended up, so LP Agent verifies the real result rather
-- than the plan it asked for. Name last: a delimiter in a filename cannot shift the fields.
for _, tm in ipairs(placed) do
    local ob = tm.outbox
    print(string.format("LPBOX|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f|%s",
        ob.minx, ob.maxx, ob.miny, ob.maxy, ob.minz, ob.maxz, tm.name))
end
print(string.format("LPRESULT|0|%d|0", #placed))

print("Generating merged build plate STL...")
local master = nil
for i, tm in ipairs(placed) do
    lp_progress("merge", i, #placed)
    local part = tm.mesh:dupe()
    part:applymatrix(tm.matrix)
    if master == nil then master = part else master:merge(part) end
end

if master ~= nil then
    master:savetostl([[{MERGED_PLATE_STL_PATH}]])
    print("Merged build plate STL written to: " .. [[{MERGED_PLATE_STL_PATH}]])
    system:log("Merged build plate STL written.")
end

if application and application.savefabbproject then
    application:savefabbproject([[{FABBPROJECT_PATH}]])
    print("Netfabb project saved to: " .. [[{FABBPROJECT_PATH}]])
    system:log("Netfabb project saved.")
end

lp_progress("done", 1, 1)
print("LP Agent placement routine completed.")
"""

    def __init__(self, config: LPConfig):
        self.config = config

    # ── Public API ────────────────────────────────────────────────

    def execute_nesting(
        self,
        stl_paths: list[Path] | list[PartInstance],
        output_dir: Path,
        plate_index: int = 1,
        mode: StrategyMode = StrategyMode.NESTING_2D,
        progress: ProgressFn | None = None,
        height_limit_mm: float | None = None,
    ) -> NetfabbRunResult:
        """Run TrueShape packing on the given STLs, falling back to the native packer.

        Accepts paths or `PartInstance`s. Quantity expansion happens upstream, so a file
        asking for several copies arrives as several instances sharing one source.

        `height_limit_mm` caps how tall a 3D stack may grow on this plate. It is applied to
        whichever engine runs: the native packer takes it directly, and Netfabb receives it
        as the tray's Z size so its own packer respects the same ceiling.

        Whatever path is taken, the returned result carries `builder`, `plate_built` and
        `merged_stl_triangles` so the caller can tell a real plate from a failed one.
        """
        from core.date_scanner import ensure_windows_share_online

        started = time.monotonic()
        stl_paths = as_instances(stl_paths)
        ensure_windows_share_online(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        plate_name = f"Plate_{plate_index:02d}"
        plate_dir = output_dir / plate_name
        plate_dir.mkdir(parents=True, exist_ok=True)

        merged_stl_path = plate_dir / f"merged_plate_{plate_index:02d}.stl"
        fabbproject_path = plate_dir / f"plate_{plate_index:02d}.fabbproject"
        audit_json_path = plate_dir / f"audit_results_{plate_index:02d}.json"
        log_file_path = plate_dir / f"netfabb_{plate_index:02d}.log"

        if not self.config.dry_run:
            self._clear_previous_artifacts(plate_dir, plate_index)

        executable = Path(self.config.netfabb_executable)

        if self.config.dry_run:
            Logger.info(f"[VULCAN] Dry-run active: packing {len(stl_paths)} files without writing a plate.")
            return self._native_nesting(
                stl_paths, merged_stl_path, fabbproject_path, audit_json_path, mode,
                reason="Dry-run requested", progress=progress, started=started, write_files=False,
                height_limit_mm=height_limit_mm,
            )

        if mode == StrategyMode.PACKING_3D:
            # 3D layout is always decided here, never by Netfabb: a file's copies form one
            # column at the model's own angle, and Netfabb's Monte Carlo packer would
            # scatter them across a tier instead. Netfabb is still used to build the plate
            # from those positions, because it produces the .fabbproject.
            return self._column_nesting(
                stl_paths=stl_paths,
                plate_dir=plate_dir,
                plate_name=plate_name,
                plate_index=plate_index,
                merged_stl_path=merged_stl_path,
                fabbproject_path=fabbproject_path,
                audit_json_path=audit_json_path,
                log_file_path=log_file_path,
                executable=executable,
                progress=progress,
                started=started,
                height_limit_mm=height_limit_mm,
            )

        if not executable.is_file():
            Logger.warning(
                f"[VULCAN] Netfabb console not found at {executable}. "
                f"Packing {len(stl_paths)} files with the native packer instead."
            )
            return self._native_nesting(
                stl_paths, merged_stl_path, fabbproject_path, audit_json_path, mode,
                reason=f"Netfabb console not found at {executable}",
                progress=progress, started=started, height_limit_mm=height_limit_mm,
            )

        result = self._run_netfabb(
            stl_paths=stl_paths,
            plate_dir=plate_dir,
            plate_name=plate_name,
            plate_index=plate_index,
            merged_stl_path=merged_stl_path,
            fabbproject_path=fabbproject_path,
            audit_json_path=audit_json_path,
            log_file_path=log_file_path,
            executable=executable,
            mode=mode,
            progress=progress,
            started=started,
            height_limit_mm=height_limit_mm,
        )

        if result is not None:
            return result

        # Netfabb ran but produced nothing usable; pack natively rather than ship a stub.
        return self._native_nesting(
            stl_paths, merged_stl_path, fabbproject_path, audit_json_path, mode,
            reason=self._last_failure_reason, progress=progress, started=started,
            height_limit_mm=height_limit_mm,
        )

    # ── 3D column nesting ─────────────────────────────────────────

    def _column_nesting(
        self,
        stl_paths: list[PartInstance],
        plate_dir: Path,
        plate_name: str,
        plate_index: int,
        merged_stl_path: Path,
        fabbproject_path: Path,
        audit_json_path: Path,
        log_file_path: Path,
        executable: Path,
        progress: ProgressFn | None,
        started: float,
        height_limit_mm: float | None,
    ) -> NetfabbRunResult:
        """Pack columns natively, then have Netfabb build the plate from those positions.

        The layout is ours; the export is Netfabb's. Falls back to the native merger when
        Netfabb is absent or cannot deliver, so a 3D plate is always produced either way --
        only the .fabbproject depends on Netfabb.
        """
        mode = StrategyMode.PACKING_3D

        pack_result: NativePackResult = pack_shelf(
            self.config, stl_paths, mode=mode, progress=progress,
            height_limit_mm=height_limit_mm,
        )

        Logger.info(
            f"[VULCAN] {plate_name}: {len(pack_result.placed)} parts in "
            f"{pack_result.column_count} column(s), tallest {pack_result.layers_used} high, "
            f"{pack_result.plate_height_mm:.1f} mm."
        )

        if self.config.dry_run or not pack_result.placed:
            return self._finish_native(
                pack_result, merged_stl_path, fabbproject_path, audit_json_path, mode,
                reason="Dry-run requested" if self.config.dry_run else "Nothing could be placed",
                progress=progress, started=started,
                write_files=not self.config.dry_run,
            )

        if executable.is_file():
            result = self._run_netfabb_placement(
                pack_result=pack_result,
                plate_dir=plate_dir,
                plate_name=plate_name,
                plate_index=plate_index,
                merged_stl_path=merged_stl_path,
                fabbproject_path=fabbproject_path,
                audit_json_path=audit_json_path,
                log_file_path=log_file_path,
                executable=executable,
                progress=progress,
                started=started,
            )
            if result is not None:
                return result
            Logger.warning(
                f"[VULCAN] Netfabb could not build {plate_name} from the placements; "
                "merging natively instead (no .fabbproject)."
            )

        return self._finish_native(
            pack_result, merged_stl_path, fabbproject_path, audit_json_path, mode,
            reason=self._last_failure_reason if executable.is_file()
            else f"Netfabb console not found at {executable}",
            progress=progress, started=started,
        )

    def _run_netfabb_placement(
        self,
        pack_result: NativePackResult,
        plate_dir: Path,
        plate_name: str,
        plate_index: int,
        merged_stl_path: Path,
        fabbproject_path: Path,
        audit_json_path: Path,
        log_file_path: Path,
        executable: Path,
        progress: ProgressFn | None,
        started: float,
    ) -> NetfabbRunResult | None:
        """Drive Netfabb to place, merge and export an already-decided layout."""
        from core.date_scanner import ensure_windows_share_online

        temp_id = str(uuid.uuid4())[:8]
        stage_dir = Path(tempfile.gettempdir()) / f"lp_place_{plate_name}_{temp_id}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        # Stage each *source* once, however many copies reference it. A 40-up column batch
        # would otherwise copy the same file forty times.
        staged: dict[Path, Path] = {}
        total_bytes = 0
        for part in pack_result.placed:
            if part.source in staged:
                continue
            dst = stage_dir / part.source.name
            try:
                if not dst.exists() or dst.stat().st_size != part.source.stat().st_size:
                    shutil.copy2(part.source, dst)
                total_bytes += dst.stat().st_size
            except Exception as err:
                Logger.warning(f"[VULCAN] Could not stage {part.source.name}: {err}")
                dst = part.source
            staged[part.source] = dst

        local_merged = stage_dir / merged_stl_path.name
        local_fabb = stage_dir / fabbproject_path.name
        local_log = stage_dir / log_file_path.name
        console_log = stage_dir / f"console_{plate_index:02d}.log"

        lua = self._generate_placement_lua(
            pack_result.placed, staged, local_merged, local_fabb, local_log
        )
        temp_lua = Path(tempfile.gettempdir()) / f"lp_place_{plate_name}_{temp_id}.lua"
        temp_lua.write_text(lua, encoding="utf-8")

        # Placement skips the search entirely, so the cost is loading and merging meshes --
        # budget on mesh volume, with the part count standing in for load overhead.
        budget = self.config.compute_netfabb_timeout(len(pack_result.placed), total_bytes)
        Logger.info(
            f"[VULCAN] Netfabb placing {len(pack_result.placed)} parts for {plate_name} "
            f"({len(staged)} source mesh(es), {total_bytes / 1024 / 1024:.0f} MB, "
            f"{budget / 60:.1f} min budget)."
        )

        exit_code, stdout, timed_out = self._spawn_and_monitor(
            [str(executable), "-l", str(temp_lua)], console_log, budget, progress
        )

        try:
            if temp_lua.exists():
                temp_lua.unlink()
        except Exception:
            pass

        if timed_out:
            self._last_failure_reason = (
                f"Netfabb exceeded its {budget / 60:.1f} min budget while placing"
            )
            shutil.rmtree(stage_dir, ignore_errors=True)
            return None

        ensure_windows_share_online(plate_dir)
        plate_dir.mkdir(parents=True, exist_ok=True)
        try:
            if local_merged.exists():
                shutil.copy2(local_merged, merged_stl_path)
            if local_fabb.exists():
                shutil.copy2(local_fabb, fabbproject_path)
            if local_log.exists():
                shutil.copy2(local_log, log_file_path)
            if console_log.exists():
                shutil.copy2(console_log, plate_dir / f"netfabb_console_{plate_index:02d}.log")
        except Exception as err:
            Logger.error(f"[VULCAN] Error copying placed artifacts: {err}")

        # Verify against where the parts actually landed, not where we asked for them.
        packed_names, _, raw_boxes = self._parse_audit_from_stdout(stdout)
        stl_info = inspect_stl(merged_stl_path)
        shutil.rmtree(stage_dir, ignore_errors=True)

        if not stl_info["built"]:
            self._last_failure_reason = (
                f"Netfabb exited {exit_code} but produced no usable plate STL"
                + (f": {stl_info['reason']}" if stl_info["reason"] else "")
            )
            return None

        placed_names = {p.name for p in pack_result.placed}
        leftover = [i.name for i in pack_result.leftover] + [
            i.name for i in pack_result.oversize
        ]
        if not packed_names:
            packed_names = sorted(placed_names)

        boxes = [
            (b["name"], b["min_x"], b["max_x"], b["min_y"], b["max_y"], b["min_z"], b["max_z"])
            for b in raw_boxes
        ] or [
            (p.name, p.min_x, p.max_x, p.min_y, p.max_y, p.min_z, p.max_z)
            for p in pack_result.placed
        ]

        self._write_audit(
            audit_json_path,
            {
                "exit_code": exit_code,
                "builder": "netfabb-placed",
                "mode": StrategyMode.PACKING_3D.value,
                "packed_count": len(packed_names),
                "leftover_count": len(leftover),
                "packed_files": packed_names,
                "placed_boxes": [
                    {
                        "name": b[0],
                        "min_x": round(b[1], 4), "max_x": round(b[2], 4),
                        "min_y": round(b[3], 4), "max_y": round(b[4], 4),
                        "min_z": round(b[5], 4), "max_z": round(b[6], 4),
                    }
                    for b in boxes
                ],
                "leftover_files": leftover,
                "layers_used": pack_result.layers_used,
                "column_count": pack_result.column_count,
                "plate_height_mm": round(pack_result.plate_height_mm, 3),
                "layer_gap_z_mm": self.config.layer_gap_z,
                "clearance_xy_mm": self.config.clearance_buffer,
            },
        )

        return NetfabbRunResult(
            success=True,
            exit_code=exit_code,
            packed_files=list(packed_names),
            leftover_files=leftover,
            placed_boxes=boxes,
            merged_plate_stl=merged_stl_path,
            fabbproject_path=fabbproject_path if fabbproject_path.exists() else None,
            stdout=stdout,
            builder="netfabb-placed",
            plate_built=True,
            merged_stl_bytes=stl_info["size_bytes"],
            merged_stl_triangles=stl_info["triangles"],
            duration_seconds=time.monotonic() - started,
            timeout_budget_seconds=budget,
            layers_used=pack_result.layers_used,
            plate_height_mm=max((b[6] for b in boxes), default=0.0),
        )

    def _generate_placement_lua(
        self,
        placed: list,
        staged: dict[Path, Path],
        merged_stl_path: Path,
        fabbproject_path: Path,
        log_file_path: Path,
    ) -> str:
        """Render the explicit-placement Lua for an already-decided layout."""
        rows = []
        for part in placed:
            source = staged.get(part.source, part.source)
            # Lua long-bracket strings need no escaping, which keeps Windows paths and
            # parenthesised jewellery filenames intact.
            rot = 1 if part.rot_deg == 90 else 0
            rows.append(
                f"    {{file=[[{source.as_posix()}]], name=[[{part.name}]], rot={rot}, "
                f"x={part.min_x:.4f}, y={part.min_y:.4f}, z={part.min_z:.4f}}},"
            )

        lua = self.LUA_PLACEMENT_TEMPLATE
        lua = lua.replace("{PLATFORM_X}", f"{self.config.platform_x:.1f}")
        lua = lua.replace("{PLATFORM_Y}", f"{self.config.platform_y:.1f}")
        lua = lua.replace("{PLATFORM_Z}", f"{self.config.platform_z:.1f}")
        lua = lua.replace("{PLACEMENTS_ARRAY}", "\n".join(rows))
        lua = lua.replace("{LOG_FILE_PATH}", log_file_path.as_posix())
        lua = lua.replace("{MERGED_PLATE_STL_PATH}", merged_stl_path.as_posix())
        lua = lua.replace("{FABBPROJECT_PATH}", fabbproject_path.as_posix())
        return lua

    @staticmethod
    def _clear_previous_artifacts(plate_dir: Path, plate_index: int) -> None:
        """Delete the previous run's artifacts for this plate before packing again.

        Without this, a run that fails to produce an audit silently inherits the previous
        run's file. That is not hypothetical: a Netfabb run whose Lua died before writing
        its audit read back a *simulated* audit from an earlier run, so the plate was
        verified and drawn against 31 stale positions that had nothing to do with the
        geometry actually on it. Results from a previous run must never survive into this
        one -- an absent artifact is a detectable failure, a stale one is not.
        """
        stale = [
            plate_dir / f"merged_plate_{plate_index:02d}.stl",
            plate_dir / f"plate_{plate_index:02d}.fabbproject",
            plate_dir / f"plate_{plate_index:02d}.txt",
            plate_dir / f"audit_results_{plate_index:02d}.json",
            plate_dir / f"netfabb_{plate_index:02d}.log",
            plate_dir / f"netfabb_console_{plate_index:02d}.log",
            plate_dir / f"plate_{plate_index:02d}_summary.json",
        ]
        for path in stale:
            try:
                if path.exists():
                    path.unlink()
            except Exception as err:
                Logger.warning(f"[VULCAN] Could not clear stale artifact {path.name}: {err}")

    # ── Netfabb execution ─────────────────────────────────────────

    _last_failure_reason: str = ""

    def _run_netfabb(
        self,
        stl_paths: list[Path],
        plate_dir: Path,
        plate_name: str,
        plate_index: int,
        merged_stl_path: Path,
        fabbproject_path: Path,
        audit_json_path: Path,
        log_file_path: Path,
        executable: Path,
        mode: StrategyMode,
        progress: ProgressFn | None,
        started: float,
        height_limit_mm: float | None = None,
    ) -> NetfabbRunResult | None:
        """Attempt a headless Netfabb run. Returns None if the caller should fall back."""
        from core.date_scanner import ensure_windows_share_online

        temp_id = str(uuid.uuid4())[:8]
        local_stage_dir = Path(tempfile.gettempdir()) / f"lp_nest_stage_{plate_name}_{temp_id}"
        local_stage_dir.mkdir(parents=True, exist_ok=True)

        # Stage candidate STLs to local SSD to avoid slow SMB VPN reads inside Netfabb.
        #
        # Each instance is staged under its own unique name, so a quantity-2 file becomes
        # two real files on disk. That is what makes Netfabb load and nest it twice, and it
        # keeps the audit's part names matching the instance names used for reconciliation.
        Logger.info(f"[VULCAN] Staging {len(stl_paths)} candidate STLs to local SSD: {local_stage_dir}")
        local_stls: list[Path] = []
        total_bytes = 0
        for idx, inst in enumerate(stl_paths):
            src_path = inst.source
            if progress:
                progress(f"Staging {inst.name}", idx / max(1, len(stl_paths)))
            dst_path = local_stage_dir / inst.stage_name()
            try:
                if not dst_path.exists() or dst_path.stat().st_size != src_path.stat().st_size:
                    shutil.copy2(src_path, dst_path)
            except Exception as e:
                Logger.warning(f"[VULCAN] Failed to stage {inst.name} locally, using network path: {e}")
                # Only the original can be reused directly; a copy has nowhere else to live.
                if inst.is_copy:
                    Logger.error(f"[VULCAN] Cannot stage copy {inst.name}; it will not be nested.")
                    continue
                dst_path = src_path
            try:
                total_bytes += dst_path.stat().st_size
            except OSError:
                pass
            local_stls.append(dst_path)

        if not local_stls:
            # Nothing reached the staging directory, so there is nothing for Netfabb to
            # nest. Launching it anyway wastes the whole timeout budget and reports a
            # confusing "0 parts" failure instead of the staging problem that caused it.
            self._last_failure_reason = (
                f"None of the {len(stl_paths)} candidate STLs could be staged for Netfabb"
            )
            Logger.error(f"[VULCAN] {self._last_failure_reason}")
            shutil.rmtree(local_stage_dir, ignore_errors=True)
            return None

        local_merged_stl = local_stage_dir / f"merged_plate_{plate_index:02d}.stl"
        local_fabbproject = local_stage_dir / f"plate_{plate_index:02d}.fabbproject"
        local_log_file = local_stage_dir / f"netfabb_{plate_index:02d}.log"
        console_log = local_stage_dir / f"console_{plate_index:02d}.log"

        lua_script = self._generate_lua_script(
            stl_paths=local_stls,
            merged_stl_path=local_merged_stl,
            fabbproject_path=local_fabbproject,
            log_file_path=local_log_file,
            mode=mode,
            height_limit_mm=height_limit_mm,
        )

        temp_lua = Path(tempfile.gettempdir()) / f"lp_nest_{plate_name}_{temp_id}.lua"
        temp_lua.write_text(lua_script, encoding="utf-8")

        timeout_budget = self.config.compute_netfabb_timeout(len(local_stls), total_bytes)
        cmd = [str(executable), "-l", str(temp_lua)]
        Logger.info(
            f"[VULCAN] Launching Netfabb Console ({plate_name}) with a "
            f"{timeout_budget / 60:.1f} min budget for {len(local_stls)} parts "
            f"({total_bytes / 1024 / 1024:.0f} MB): {' '.join(cmd)}"
        )

        exit_code, stdout, timed_out = self._spawn_and_monitor(
            cmd, console_log, timeout_budget, progress
        )

        try:
            if temp_lua.exists():
                temp_lua.unlink()
        except Exception:
            pass

        if timed_out:
            Logger.warning(
                f"[VULCAN] Netfabb exceeded its {timeout_budget / 60:.1f} min budget for {plate_name}."
            )
            self._last_failure_reason = (
                f"Netfabb exceeded its {timeout_budget / 60:.1f} min budget "
                f"for {len(local_stls)} parts"
            )
            shutil.rmtree(local_stage_dir, ignore_errors=True)
            return None

        if exit_code != 0:
            Logger.warning(f"[VULCAN] Netfabb returned non-zero exit code {exit_code}.")

        ensure_windows_share_online(plate_dir)
        plate_dir.mkdir(parents=True, exist_ok=True)

        # Copy generated artifacts from local SSD to the final destination.
        try:
            if local_merged_stl.exists():
                shutil.copy2(local_merged_stl, merged_stl_path)
            if local_fabbproject.exists():
                shutil.copy2(local_fabbproject, fabbproject_path)
            if local_log_file.exists():
                shutil.copy2(local_log_file, log_file_path)
            if console_log.exists():
                shutil.copy2(console_log, plate_dir / f"netfabb_console_{plate_index:02d}.log")
        except Exception as err:
            Logger.error(f"[VULCAN] Error copying packed artifacts to destination: {err}")

        # The packing audit arrives on stdout, not as a file: Netfabb's Lua has no `io`
        # library and cannot write one. Parse it here and persist the JSON ourselves.
        packed_files, leftover_files, raw_placed_boxes = self._parse_audit_from_stdout(stdout)

        # Map Netfabb's part names back onto our instance names before anything downstream
        # tries to reconcile them. Netfabb has been seen to report a part stripped of its
        # extension, which matches no instance at all -- the plate then packs perfectly and
        # is thrown away as "0 parts packed".
        packed_files, leftover_files, raw_placed_boxes = self._reconcile_names(
            stl_paths, packed_files, leftover_files, raw_placed_boxes
        )

        if raw_placed_boxes or packed_files:
            self._write_audit(
                audit_json_path,
                {
                    "exit_code": exit_code,
                    "builder": "netfabb",
                    "mode": mode.value,
                    "packed_count": len(packed_files),
                    "leftover_count": len(leftover_files),
                    "packed_files": packed_files,
                    "placed_boxes": raw_placed_boxes,
                    "leftover_files": leftover_files,
                },
            )
        else:
            Logger.warning("[VULCAN] Netfabb emitted no packing audit on stdout.")

        stl_info = inspect_stl(merged_stl_path)
        shutil.rmtree(local_stage_dir, ignore_errors=True)

        # A plate is only real if the STL on disk actually holds geometry. Netfabb can exit
        # 0 having written nothing, which is precisely how empty plates reached the floor.
        if not stl_info["built"]:
            self._last_failure_reason = (
                f"Netfabb exited {exit_code} but produced no usable plate STL"
                + (f": {stl_info['reason']}" if stl_info["reason"] else "")
            )
            Logger.warning(f"[VULCAN] {self._last_failure_reason}")
            return None

        if not packed_files:
            leftover_set = set(leftover_files)
            packed_files = [i.name for i in stl_paths if i.name not in leftover_set]

        placed_boxes_list = [
            (b["name"], b["min_x"], b["max_x"], b["min_y"], b["max_y"], b["min_z"], b["max_z"])
            for b in raw_placed_boxes
            if "min_x" in b
        ]

        # Netfabb does not report tiers, so derive the same figures the native packer
        # reports: plate height (which drives print time) and how many distinct Z bands
        # the parts occupy.
        size_bytes = stl_info["size_bytes"]
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > getattr(self.config, "max_recommended_plate_mb", 600.0):
            Logger.warning(
                f"[VULCAN] ⚠️ PLATE SIZE EXCEEDS BUDGET: {merged_stl_path.name} is {size_mb:.1f} MB "
                f"(> {getattr(self.config, 'max_recommended_plate_mb', 600.0):.0f} MB limit!). "
                "A full jewelry build plate should typically be between 150 MB and 600 MB, not multi-gigabytes. "
                "Run 70%-90% Mesh Reduction / Decimation on jewelry pieces in MatrixGold/Rhino or Netfabb before merging. "
                "Un-decimated plates (>1GB - 3.9GB) cause Flashforge WaxJetPrint to consume 45+ GB RAM, freeze, "
                "and crash with Out of Memory."
            )

        return NetfabbRunResult(
            success=True,
            exit_code=exit_code,
            packed_files=packed_files,
            leftover_files=leftover_files,
            placed_boxes=placed_boxes_list,
            merged_plate_stl=merged_stl_path,
            fabbproject_path=fabbproject_path if fabbproject_path.exists() else None,
            stdout=stdout,
            stderr="",
            builder="netfabb",
            plate_built=True,
            merged_stl_bytes=stl_info["size_bytes"],
            merged_stl_triangles=stl_info["triangles"],
            duration_seconds=time.monotonic() - started,
            timeout_budget_seconds=timeout_budget,
            layers_used=tiers,
            plate_height_mm=plate_height,
        )

    def _spawn_and_monitor(
        self,
        cmd: list[str],
        console_log: Path,
        timeout_budget: float,
        progress: ProgressFn | None,
    ) -> tuple[int, str, bool]:
        """Run Netfabb, streaming its progress, and enforce the timeout for real.

        stdout goes to a file rather than a pipe on purpose. With `capture_output=True`,
        `subprocess.run` kills the process on timeout and then calls `communicate()` again,
        which blocks until every child holding the inherited pipe exits -- a 180s budget
        took 395s to return in production. Writing to a file removes the pipe entirely, and
        the timeout is enforced by polling, so the budget is the real wall-clock limit.
        """
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        started = time.monotonic()
        timed_out = False

        with open(console_log, "wb") as log_handle:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception as err:
                self._last_failure_reason = f"Could not launch Netfabb console: {err}"
                Logger.error(f"[VULCAN] {self._last_failure_reason}")
                return -1, "", False

            read_pos = 0
            while True:
                exit_code = proc.poll()
                elapsed = time.monotonic() - started

                read_pos = self._drain_progress(
                    console_log, read_pos, elapsed, timeout_budget, progress
                )

                if exit_code is not None:
                    break

                if elapsed > timeout_budget:
                    timed_out = True
                    self._terminate_tree(proc)
                    exit_code = -1
                    break

                time.sleep(1.0)

        try:
            stdout = console_log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            stdout = ""

        return (exit_code if exit_code is not None else -1), stdout, timed_out

    @staticmethod
    def _reconcile_names(
        instances: list[PartInstance],
        packed: list[str],
        leftover: list[str],
        boxes: list[dict],
    ) -> tuple[list[str], list[str], list[dict]]:
        """Translate names reported by Netfabb back into LP Agent instance names.

        Netfabb labels a part from the staged filename and may drop the extension, so the
        returned names cannot be assumed to match ours verbatim. Matching is tried exactly
        first, then on the stem; anything still unmatched is passed through untouched so
        it stays visible rather than being silently discarded.
        """
        exact: dict[str, str] = {}
        by_stem: dict[str, str] = {}
        for inst in instances:
            staged = inst.stage_name()
            exact[staged] = inst.name
            by_stem.setdefault(Path(staged).stem, inst.name)

        unmatched: list[str] = []

        def resolve(name: str) -> str:
            if name in exact:
                return exact[name]
            stem_hit = by_stem.get(Path(name).stem)
            if stem_hit is not None:
                return stem_hit
            unmatched.append(name)
            return name

        packed_out = [resolve(n) for n in packed]
        leftover_out = [resolve(n) for n in leftover]
        boxes_out = [{**b, "name": resolve(b["name"])} for b in boxes]

        if unmatched:
            Logger.warning(
                f"[VULCAN] {len(unmatched)} part name(s) from Netfabb matched no staged "
                f"instance: {', '.join(unmatched[:5])}"
                + (" ..." if len(unmatched) > 5 else "")
            )

        return packed_out, leftover_out, boxes_out

    @staticmethod
    def _parse_audit_from_stdout(
        stdout: str,
    ) -> tuple[list[str], list[str], list[dict]]:
        """Recover the packing audit from Netfabb's console output.

        The Lua emits one `LPBOX|minx|maxx|miny|maxy|minz|maxz|name` line per packed part
        and `LPLEFT|name` per leftover. The name is last on the line, so a `|` inside a
        filename cannot corrupt the numeric fields.

        Returns (packed_names, leftover_names, placed_boxes).
        """
        packed: list[str] = []
        leftover: list[str] = []
        boxes: list[dict] = []

        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("LPBOX|"):
                fields = line[len("LPBOX|"):].split("|", 6)
                if len(fields) != 7:
                    continue
                try:
                    min_x, max_x, min_y, max_y, min_z, max_z = (float(v) for v in fields[:6])
                except ValueError:
                    continue
                name = fields[6]
                packed.append(name)
                boxes.append({
                    "name": name,
                    "min_x": min_x, "max_x": max_x,
                    "min_y": min_y, "max_y": max_y,
                    "min_z": min_z, "max_z": max_z,
                })
            elif line.startswith("LPLEFT|"):
                leftover.append(line[len("LPLEFT|"):])

        return packed, leftover, boxes

    def _drain_progress(
        self,
        console_log: Path,
        read_pos: int,
        elapsed: float,
        timeout_budget: float,
        progress: ProgressFn | None,
    ) -> int:
        """Read new console output and forward any LPPROGRESS markers to the caller."""
        if progress is None:
            return read_pos

        try:
            with open(console_log, "r", encoding="utf-8", errors="replace") as f:
                f.seek(read_pos)
                chunk = f.read()
                read_pos = f.tell()
        except Exception:
            return read_pos

        stage_label = {
            "load": "Loading parts into Netfabb",
            "pack": "Nesting on the plate",
            "merge": "Merging plate geometry",
            "done": "Netfabb finished",
        }

        latest: tuple[str, float] | None = None
        for line in chunk.splitlines():
            if PROGRESS_TOKEN not in line:
                continue
            try:
                _, stage, current, total = line.strip().split("|")
                cur_f, tot_f = float(current), float(total)
            except ValueError:
                continue
            fraction = cur_f / tot_f if tot_f else 0.0
            latest = (stage_label.get(stage, stage), fraction)

        if latest:
            progress(latest[0], latest[1])
        elif timeout_budget > 0:
            # No marker yet (older Netfabb builds): at least show the budget burning down.
            progress("Netfabb nesting", min(0.95, elapsed / timeout_budget))

        return read_pos

    @staticmethod
    def _terminate_tree(proc: subprocess.Popen) -> None:
        """Kill Netfabb and every child it spawned.

        Killing only the parent leaves worker processes holding the plate files, so the
        next run fails to overwrite them.
        """
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except Exception as err:
                Logger.warning(f"[VULCAN] taskkill failed for PID {proc.pid}: {err}")

        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            pass

    def _generate_lua_script(
        self,
        stl_paths: list[Path],
        merged_stl_path: Path,
        fabbproject_path: Path,
        log_file_path: Path,
        mode: StrategyMode,
        height_limit_mm: float | None = None,
    ) -> str:
        """Substitute parameters into the Master Netfabb Lua template."""
        is_2d_str = "true" if mode == StrategyMode.NESTING_2D else "false"

        # Netfabb has no separate "preferred height" concept, so the cap is imposed as the
        # tray's Z size. Its packer then keeps the stack under the cleaning limit itself,
        # and anything that will not fit comes back as leftover for the next plate.
        machine_z = self.config.platform_z
        if mode == StrategyMode.PACKING_3D and height_limit_mm is not None:
            machine_z = min(max(0.0, height_limit_mm), self.config.platform_z)

        # Format array of STL file paths (using posix forward slashes for Lua)
        stl_entries = [f'    "{p.as_posix()}",' for p in stl_paths]
        stl_array_str = "\n".join(stl_entries)

        lua_code = self.LUA_TEMPLATE
        lua_code = lua_code.replace("{PLATFORM_X}", f"{self.config.platform_x:.1f}")
        lua_code = lua_code.replace("{PLATFORM_Y}", f"{self.config.platform_y:.1f}")
        lua_code = lua_code.replace("{PLATFORM_Z}", f"{machine_z:.1f}")
        lua_code = lua_code.replace("{CLEARANCE}", f"{self.config.clearance_buffer:.2f}")
        lua_code = lua_code.replace("{BORDER_XY}", f"{self.config.border_spacing_xy:.1f}")
        lua_code = lua_code.replace("{BORDER_Z}", f"{self.config.border_spacing_z:.1f}")
        lua_code = lua_code.replace("{VOXEL_RES}", f"{self.config.voxel_size:.2f}")
        lua_code = lua_code.replace("{ROTATION_Z}", str(self.config.rotation_step_z_2d))
        lua_code = lua_code.replace("{IS_2D_MODE}", is_2d_str)
        lua_code = lua_code.replace("{STL_FILES_ARRAY}", stl_array_str)
        lua_code = lua_code.replace("{LOG_FILE_PATH}", log_file_path.as_posix())
        lua_code = lua_code.replace("{MERGED_PLATE_STL_PATH}", merged_stl_path.as_posix())
        lua_code = lua_code.replace("{FABBPROJECT_PATH}", fabbproject_path.as_posix())

        return lua_code

    # ── Native fallback ───────────────────────────────────────────

    def _native_nesting(
        self,
        stl_paths: list[PartInstance],
        merged_stl_path: Path,
        fabbproject_path: Path,
        audit_json_path: Path,
        mode: StrategyMode,
        reason: str = "",
        progress: ProgressFn | None = None,
        started: float | None = None,
        write_files: bool = True,
        height_limit_mm: float | None = None,
    ) -> NetfabbRunResult:
        """Pack and merge the plate in-process, writing a real STL.

        This is the path that used to emit a 49-byte placeholder. It now shelf-packs
        against the true envelope, reports honest leftovers so Marshal opens Plate 02, and
        merges the actual geometry into the plate STL.
        """
        started = started if started is not None else time.monotonic()

        if reason:
            Logger.warning(f"[VULCAN] Falling back to the native packer: {reason}")

        pack_result: NativePackResult = pack_shelf(
            self.config, stl_paths, mode=mode, progress=progress,
            height_limit_mm=height_limit_mm,
        )

        return self._finish_native(
            pack_result, merged_stl_path, fabbproject_path, audit_json_path, mode,
            reason=reason, progress=progress, started=started, write_files=write_files,
        )

    def _finish_native(
        self,
        pack_result: NativePackResult,
        merged_stl_path: Path,
        fabbproject_path: Path,
        audit_json_path: Path,
        mode: StrategyMode,
        reason: str = "",
        progress: ProgressFn | None = None,
        started: float | None = None,
        write_files: bool = True,
    ) -> NetfabbRunResult:
        """Write the plate for an already-computed layout: audit, merged STL, result.

        Split out from `_native_nesting` so the 3D path can reuse it after Netfabb has
        declined, without packing the plate a second time.
        """
        started = started if started is not None else time.monotonic()

        leftover_names = [p.name for p in pack_result.leftover]
        oversize_names = [p.name for p in pack_result.oversize]

        audit_data = {
            "exit_code": 0,
            "builder": "lp-native",
            "fallback_reason": reason,
            "mode": mode.value,
            "packed_count": len(pack_result.placed),
            "leftover_count": len(leftover_names),
            "oversize_count": len(oversize_names),
            "packed_files": pack_result.packed_names,
            "placed_boxes": [p.as_audit_dict() for p in pack_result.placed],
            "leftover_files": leftover_names,
            "oversize_files": oversize_names,
            "layers_used": pack_result.layers_used,
            "plate_height_mm": round(pack_result.plate_height_mm, 3),
            "layer_gap_z_mm": self.config.layer_gap_z,
            "clearance_xy_mm": self.config.clearance_buffer,
        }

        if not write_files:
            # Dry run: report the plan, touch nothing on disk.
            return NetfabbRunResult(
                success=bool(pack_result.placed),
                exit_code=0,
                packed_files=pack_result.packed_names,
                leftover_files=leftover_names,
                placed_boxes=[
                    (p.name, p.min_x, p.max_x, p.min_y, p.max_y, p.min_z, p.max_z)
                    for p in pack_result.placed
                ],
                builder="lp-native",
                plate_built=False,
                failure_reason="Dry run: no plate written",
                duration_seconds=time.monotonic() - started,
                # A dry run exists to preview the plan, so it must still report the
                # stacking outcome even though nothing is written.
                layers_used=pack_result.layers_used,
                plate_height_mm=pack_result.plate_height_mm,
                stdout="DRY_RUN",
            )

        if not pack_result.placed:
            failure = reason or "No parts could be placed on the plate"
            Logger.error(f"[VULCAN] {failure}")
            self._write_audit(audit_json_path, audit_data)
            return NetfabbRunResult(
                success=False,
                exit_code=1,
                packed_files=[],
                leftover_files=leftover_names + oversize_names,
                builder="lp-native",
                plate_built=False,
                failure_reason=failure,
                duration_seconds=time.monotonic() - started,
            )

        stl_bytes = 0
        triangles = 0
        build_error = ""
        try:
            stl_bytes, triangles = write_merged_stl(
                pack_result.placed, merged_stl_path, progress=progress
            )
        except Exception as err:
            build_error = f"Failed to write merged plate STL: {err}"
            Logger.error(f"[VULCAN] {build_error}")

        audit_data["merged_stl_bytes"] = stl_bytes
        audit_data["merged_stl_triangles"] = triangles
        self._write_audit(audit_json_path, audit_data)

        if build_error:
            return NetfabbRunResult(
                success=False,
                exit_code=1,
                packed_files=pack_result.packed_names,
                leftover_files=leftover_names + oversize_names,
                placed_boxes=[
                    (p.name, p.min_x, p.max_x, p.min_y, p.max_y, p.min_z, p.max_z)
                    for p in pack_result.placed
                ],
                builder="lp-native",
                plate_built=False,
                failure_reason=build_error,
                duration_seconds=time.monotonic() - started,
            )

        # There is no native .fabbproject writer, so point Netfabb at the merged STL and
        # say so, rather than dropping a 21-byte file named like a project.
        fabb_written = self._write_fallback_project_note(fabbproject_path, merged_stl_path, reason)

        return NetfabbRunResult(
            success=True,
            exit_code=0,
            packed_files=pack_result.packed_names,
            leftover_files=leftover_names + oversize_names,
            placed_boxes=[
                (p.name, p.min_x, p.max_x, p.min_y, p.max_y, p.min_z, p.max_z)
                for p in pack_result.placed
            ],
            merged_plate_stl=merged_stl_path,
            fabbproject_path=fabb_written,
            builder="lp-native",
            plate_built=True,
            merged_stl_bytes=stl_bytes,
            merged_stl_triangles=triangles,
            failure_reason=reason,
            duration_seconds=time.monotonic() - started,
            layers_used=pack_result.layers_used,
            plate_height_mm=pack_result.plate_height_mm,
            stdout="NATIVE_PACK",
        )

    @staticmethod
    def _write_audit(audit_json_path: Path, audit_data: dict) -> None:
        try:
            audit_json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(audit_json_path, "w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=2)
        except Exception as err:
            Logger.error(f"[VULCAN] Could not write audit JSON {audit_json_path}: {err}")

    @staticmethod
    def _write_fallback_project_note(
        fabbproject_path: Path, merged_stl_path: Path, reason: str
    ) -> Path | None:
        """Leave a readable note where the .fabbproject would be, pointing at the STL."""
        # Clear any stub project left by an earlier run. Left in place it would still be
        # offered to "Open in Netfabb" and fail there instead of here.
        try:
            if fabbproject_path.exists() and fabbproject_path.stat().st_size < 1024:
                Logger.info(
                    f"[VULCAN] Removing stale placeholder project {fabbproject_path.name} "
                    f"({fabbproject_path.stat().st_size} bytes)."
                )
                fabbproject_path.unlink()
        except Exception as err:
            Logger.warning(f"[VULCAN] Could not remove stale project file: {err}")

        note_path = fabbproject_path.with_suffix(".txt")
        try:
            note_path.write_text(
                "LP Agent native packer\n"
                "======================\n"
                f"No Netfabb project was produced ({reason or 'Netfabb unavailable'}).\n"
                f"The build plate is the merged STL: {merged_stl_path.name}\n"
                "Open that STL directly in Netfabb or the WaxJet slicer.\n",
                encoding="utf-8",
            )
        except Exception as err:
            Logger.error(f"[VULCAN] Could not write fallback project note: {err}")
            return None
        return None
