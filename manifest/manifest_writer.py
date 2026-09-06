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
            plate_info = {
                "plate_index": plate.plate_index,
                "plate_name": plate.plate_name,
                "mode": plate.mode.value,
                "part_count": len(plate.packed_files),
                "packed_files": [p.name for p in plate.packed_files],
                "merged_plate_stl": str(plate.merged_plate_stl.relative_to(target_dir)) if plate.merged_plate_stl else None,
                "fabbproject_path": str(plate.fabbproject_path.relative_to(target_dir)) if plate.fabbproject_path else None,
                "verification": {
                    "passed": plate.verification_report.passed if plate.verification_report else True,
                    "envelope_violations": plate.verification_report.envelope_violations if plate.verification_report else [],
                    "clearance_violations_count": len(plate.verification_report.clearance_violations) if plate.verification_report else 0,
                },
            }
            plates_data.append(plate_info)

            # Write individual plate summary
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
                "voxel_size_mm": self.config.voxel_size,
                "avoid_interlocking": self.config.avoid_interlocking,
            },
            "strategy": {
                "mode": strategy_eval.selected_mode.value,
                "reason": strategy_eval.reason,
                "footprint_ratio": round(strategy_eval.footprint_utilization_ratio, 3),
                "batch_homogeneity": round(strategy_eval.batch_homogeneity, 3),
            },
            "totals": {
                "scanned_parts": len(scan_result.valid_parts) + len(scan_result.quarantined_files),
                "valid_parts": len(scan_result.valid_parts),
                "quarantined_files": len(scan_result.quarantined_files),
                "oversized_files": len(strategy_eval.oversized_parts),
                "total_plates_generated": dist_result.total_plates_generated,
                "total_parts_packed": dist_result.total_parts_assigned,
                "unassigned_leftovers": len(dist_result.unassigned_files),
            },
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
            f" Total Parts:  {totals.get('total_parts_packed')} packed / {totals.get('valid_parts')} valid",
            f" Total Plates: {totals.get('total_plates_generated')}",
            "-------------------------------------------------------------------",
        ]

        for p in plates:
            lines.append(f" [Plate {p['plate_index']:02d}: {p['plate_name']}]")
            lines.append(f"   - Parts Loaded: {p['part_count']}")
            lines.append(f"   - Merged STL:   {p.get('merged_plate_stl')}")
            lines.append(f"   - Netfabb Proj: {p.get('fabbproject_path')}")
            lines.append(f"   - Verified:     {'PASS' if p['verification']['passed'] else 'FAIL'}")

        if totals.get("quarantined_files", 0) > 0:
            lines.append("-------------------------------------------------------------------")
            lines.append(f" WARNING: {totals.get('quarantined_files')} file(s) quarantined to exceptions/")

        lines.append("===================================================================")
        return "\n".join(lines)
