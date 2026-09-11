"""manifest_writer.py — Subagent Marshal: Manifest Architect & Factory Work-Order Writer.

Generates structured JSON production manifests (plate_manifest.json, plate_XX_summary.json)
and human-readable work orders for Flashforge WaxJet 51C operators.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from config.lp_config import LPConfig
from core.date_scanner import ScanResult
from core.plate_distributor import DistributionResult
from core.strategy_selector import StrategyEvaluation
from utils.logger import Logger


class ManifestWriter:
    """Subagent Marshal: Writes structured manifests and factory job reports."""

    def __init__(self, config: LPConfig):
        self.config = config

    def write_manifest(
        self,
        target_dir: Path,
        scan_result: ScanResult,
        strategy_eval: StrategyEvaluation,
        dist_result: DistributionResult,
    ) -> Path:
        """Write master plate_manifest.json to the target date staging folder."""
        manifest_path = target_dir / "plate_manifest.json"

        plates_data = []
        for plate in dist_result.plates:
            run = plate.netfabb_result
            plate_info = {
                "plate_index": plate.plate_index,
                "plate_name": plate.plate_name,
                "mode": plate.mode.value,
                "part_count": len(plate.packed_files),
                "packed_files": [p.name for p in plate.packed_files],
                "merged_plate_stl": str(plate.merged_plate_stl.relative_to(target_dir)) if plate.merged_plate_stl else None,
                "fabbproject_path": str(plate.fabbproject_path.relative_to(target_dir)) if plate.fabbproject_path else None,
                # Build evidence. `verification.passed` only says the layout is legal; it
                # said PASS for an empty stub plate. `build.plate_built` is the field that
                # answers "did this plate actually get made?".
                "build": {
                    "plate_built": bool(run and run.plate_built),
                    "builder": run.builder if run else "none",
                    "merged_stl_bytes": run.merged_stl_bytes if run else 0,
                    "merged_stl_triangles": run.merged_stl_triangles if run else 0,
                    "duration_seconds": round(run.duration_seconds, 2) if run else 0.0,
                    "timed_out": bool(run and run.timed_out),
                    "failure_reason": run.failure_reason if run else "",
                    # 3D outcome. Plate height governs WaxJet print time far more than
                    # part count, so it belongs on the work order.
                    "layers_used": run.layers_used if run else 1,
                    "plate_height_mm": round(run.plate_height_mm, 2) if run else 0.0,
                },
                "placed_boxes": [
                    {
                        "name": b[0],
                        "min_x": round(b[1], 3), "max_x": round(b[2], 3),
                        "min_y": round(b[3], 3), "max_y": round(b[4], 3),
                        "min_z": round(b[5], 3), "max_z": round(b[6], 3),
                    }
                    for b in (run.placed_boxes if run else [])
                ],
                "verification": {
                    "passed": plate.verification_report.passed if plate.verification_report else True,
                    "envelope_violations": plate.verification_report.envelope_violations if plate.verification_report else [],
                    "clearance_violations_count": len(plate.verification_report.clearance_violations) if plate.verification_report else 0,
                    # On a TrueShape plate the clearance findings are box overlaps, not
                    # proof the meshes touch; record which reading applies.
                    "clearance_is_authoritative": (
                        plate.verification_report.clearance_is_authoritative
                        if plate.verification_report else True
                    ),
                    "clearance_note": (
                        plate.verification_report.clearance_note if plate.verification_report else ""
                    ),
                },
            }
            plates_data.append(plate_info)

            # Write individual plate summary
            from core.date_scanner import ensure_windows_share_online

            ensure_windows_share_online(plate.plate_dir)
            plate.plate_dir.mkdir(parents=True, exist_ok=True)
            summary_path = plate.plate_dir / f"plate_{plate.plate_index:02d}_summary.json"
            try:
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(plate_info, f, indent=2)
            except Exception as err:
                Logger.error(f"[MARSHAL] Failed to write {summary_path.name}: {err}")

        # Write exceptions report if any files were quarantined or oversized
        exceptions_dir = target_dir / "exceptions"
        if scan_result.quarantined_files or strategy_eval.oversized_parts:
            exceptions_dir.mkdir(parents=True, exist_ok=True)
            exc_report_path = exceptions_dir / "exception_report.json"
            exc_data = {
                "quarantined_files": [str(p.name) for p in scan_result.quarantined_files],
                "incomplete_sets": {k: [p.name for p in v] for k, v in scan_result.incomplete_designs.items()},
                "oversized_parts": [
                    {
                        "file": p.part.file_path.name,
                        "size_x": round(p.bbox.size_x, 2),
                        "size_y": round(p.bbox.size_y, 2),
                        "size_z": round(p.bbox.size_z, 2),
                    }
                    for p in strategy_eval.oversized_parts
                ],
            }
            try:
                with open(exc_report_path, "w", encoding="utf-8") as f:
                    json.dump(exc_data, f, indent=2)
            except Exception as err:
                Logger.error(f"[MARSHAL] Failed to write {exc_report_path.name}: {err}")

        # Master manifest payload
        master_data = {
            "version": "1.0.0",
            "machine_name": self.config.machine_name,
            "envelope_mm": {
                "x": self.config.platform_x,
                "y": self.config.platform_y,
                "z": self.config.platform_z,
            },
            "parameters": {
                "clearance_buffer_mm": self.config.clearance_buffer,
                "border_spacing_xy_mm": self.config.border_spacing_xy,
                "border_spacing_z_mm": self.config.border_spacing_z,
                "layer_gap_z_mm": self.config.layer_gap_z,
                "voxel_size_mm": self.config.voxel_size,
                "avoid_interlocking": self.config.avoid_interlocking,
            },
            "strategy": {
                "mode": strategy_eval.selected_mode.value,
                "reason": strategy_eval.reason,
                "footprint_ratio": round(strategy_eval.footprint_utilization_ratio, 3),
                "batch_homogeneity": round(strategy_eval.batch_homogeneity, 3),
                "repeat_ratio": round(strategy_eval.repeat_ratio, 3),
            },
            "totals": {
                "scanned_parts": len(scan_result.valid_parts) + len(scan_result.quarantined_files),
                "valid_parts": len(scan_result.valid_parts),
                "quarantined_files": len(scan_result.quarantined_files),
                "oversized_files": len(strategy_eval.oversized_parts),
                "total_plates_generated": dist_result.total_plates_generated,
                "total_parts_packed": dist_result.total_parts_assigned,
                "unassigned_leftovers": len(dist_result.unassigned_files),
                # Parts placed vs source files: they differ when filenames carry an xN
                # quantity suffix, so record both rather than leaving the gap unexplained.
                "quantity_expanded_parts": dist_result.total_parts_assigned
                + len(dist_result.unassigned_files),
            },
            "unassigned_files": [p.name for p in dist_result.unassigned_files],
            "plates": plates_data,
            "generated_at": datetime.now().isoformat(),
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(master_data, f, indent=2)

        Logger.info(f"[MARSHAL] Master plate manifest written to: {manifest_path}")
        return manifest_path

    @staticmethod
    def format_work_order_summary(manifest_path: Path) -> str:
        """Generate a human-readable text summary of the generated plates."""
        if not manifest_path.exists():
            return "Manifest not found."

        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        totals = data.get("totals", {})
        strat = data.get("strategy", {})
        plates = data.get("plates", [])

        lines = [
            "===================================================================",
            "   FLASHFORGE WAXJET 51C - BUILD PLATE WORK ORDER",
            "===================================================================",
            f" Generated:    {data.get('generated_at')}",
            f" Strategy:     {strat.get('mode', '').upper()} ({strat.get('reason', '')})",
            f" Source Files: {totals.get('valid_parts')} valid",
            f" Total Parts:  {totals.get('total_parts_packed')} placed"
            + (
                f" (quantity suffixes expanded {totals.get('valid_parts')} files)"
                if (totals.get("total_parts_packed") or 0) > (totals.get("valid_parts") or 0)
                else ""
            ),
            f" Total Plates: {totals.get('total_plates_generated')}",
            "-------------------------------------------------------------------",
        ]

        for p in plates:
            build = p.get("build", {})
            built = build.get("plate_built")
            if built:
                build_line = (
                    f"BUILT by {build.get('builder', '?')} - "
                    f"{build.get('merged_stl_triangles', 0):,} triangles, "
                    f"{build.get('merged_stl_bytes', 0) / 1024 / 1024:.1f} MB"
                )
            else:
                build_line = f"NOT BUILT - {build.get('failure_reason') or 'no plate STL produced'}"

            lines.append(f" [Plate {p['plate_index']:02d}: {p['plate_name']}]")
            lines.append(f"   - Parts Loaded: {p['part_count']}")
            if (build.get("layers_used") or 1) > 1:
                # Print time on the WaxJet tracks plate height, so say it plainly.
                lines.append(
                    f"   - Stacking:     {build['layers_used']} tiers, "
                    f"{build.get('plate_height_mm', 0):.1f} mm tall"
                )
            lines.append(f"   - Merged STL:   {p.get('merged_plate_stl')}")
            lines.append(f"   - Netfabb Proj: {p.get('fabbproject_path')}")
            v = p["verification"]
            lines.append(f"   - Plate Status: {build_line}")
            lines.append(f"   - Verified:     {'PASS' if v['passed'] else 'FAIL'}")
            if v.get("clearance_note"):
                lines.append(f"                   ({v['clearance_note']})")

        if totals.get("unassigned_leftovers", 0) > 0:
            lines.append("-------------------------------------------------------------------")
            lines.append(
                f" WARNING: {totals.get('unassigned_leftovers')} part(s) did NOT fit on any plate"
            )

        if totals.get("quarantined_files", 0) > 0:
            lines.append("-------------------------------------------------------------------")
            lines.append(f" WARNING: {totals.get('quarantined_files')} file(s) quarantined to exceptions/")

        lines.append("===================================================================")
        return "\n".join(lines)
