"""orchestrator.py — Pipeline Coordinator for LP Agent.

Coordinates the 5 subagents (Scout, Tactician, Vulcan, Sentry, Marshal) in sequence:
Intake -> Geometric Strategy -> Nesting -> Verification -> Overflow Distribution -> Manifest.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config.lp_config import LPConfig
from core.collision_checker import CollisionChecker, PlacedPartBox
from core.date_scanner import DateScanner
from core.netfabb_runner import NetfabbRunner
from core.plate_distributor import PlateDistributor
from core.strategy_selector import StrategySelector
from manifest.manifest_writer import ManifestWriter
from pipeline.stage_result import PipelineExecutionSummary
from utils.logger import Logger


class LPOrchestrator:
    """Master pipeline runner for Loading Prints Agent."""

    def __init__(self, config: LPConfig | None = None):
        self.config = config or LPConfig()
        self.printing_root = self.config.resolve_printing_root()

        # Initialize subagent team
        self.scout = DateScanner(self.printing_root)
        self.tactician = StrategySelector(self.config)
        self.vulcan = NetfabbRunner(self.config)
        self.sentry = CollisionChecker(self.config)
        self.marshal = PlateDistributor(self.config, self.vulcan, self.sentry)
        self.manifest_writer = ManifestWriter(self.config)

    def run(
        self,
        date_str: str | None = None,
        target_directory_override: Path | None = None,
    ) -> PipelineExecutionSummary:
        """Execute end-to-end build plate preparation."""
        started_at = datetime.now()
        effective_date = date_str or datetime.now().strftime("%d.%m.%Y")

        Logger.info(f"=== [LP AGENT] Starting Build Plate Preparation for Date: {effective_date} ===")

        # 1. SCOUT: Ingest & Audit STLs
        if target_directory_override:
            Logger.info(f"[SCOUT] Target folder overridden: {target_directory_override}")
            scan_dir = Path(target_directory_override)
            # Use custom scan target
            custom_scout = DateScanner(scan_dir.parent.parent.parent)
            # Scan directory directly
            scan_result = custom_scout.scan_date(date_str)
            # If path doesn't match default convention, scan override directly
            if not scan_result.valid_parts and scan_dir.exists():
                stl_paths = list(scan_dir.glob("*.stl"))
                scan_result.target_directory = scan_dir
                for p in stl_paths:
                    if p.stat().st_size > 84 and not any(part.startswith("Plate_") for part in p.parts):
                        is_rep = "(repaired)" in p.name.lower()
                        from core.date_scanner import ScannedPart
                        scan_result.valid_parts.append(
                            ScannedPart(
                                file_path=p,
                                stem=p.stem,
                                is_repaired=is_rep,
                                is_multipart=False,
                                component_index=None,
                                base_design_key=p.stem,
                                file_size_bytes=p.stat().st_size,
                            )
                        )
        else:
            scan_result = self.scout.scan_date(date_str)

        target_dir = scan_result.target_directory

        # Zero valid files: clean exit, no crash
        if not scan_result.valid_parts:
            Logger.warning(f"[LP AGENT] Zero valid parts found in {target_dir}. Exiting cleanly.")
            return PipelineExecutionSummary(
                success=True,
                date_str=effective_date,
                target_directory=target_dir,
                mode_selected="none",
                total_scanned_parts=len(scan_result.quarantined_files),
                total_valid_parts=0,
                total_quarantined=len(scan_result.quarantined_files),
                total_plates_generated=0,
                error_message="No valid STLs found in date folder",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        # 2. TACTICIAN: Strategy & Geometry Evaluation
        strategy_eval = self.tactician.evaluate_batch(scan_result.valid_parts)
        valid_paths = [g.part.file_path for g in strategy_eval.valid_geometries]

        if not valid_paths:
            Logger.error("[LP AGENT] All candidate parts were oversized or unparseable.")
            return PipelineExecutionSummary(
                success=False,
                date_str=effective_date,
                target_directory=target_dir,
                mode_selected=strategy_eval.selected_mode.value,
                total_scanned_parts=len(scan_result.valid_parts),
                total_valid_parts=0,
                total_quarantined=len(scan_result.quarantined_files) + len(strategy_eval.oversized_parts),
                total_plates_generated=0,
                strategy_eval=strategy_eval,
                error_message="All files failed geometric bounds checks",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        # 3. MARSHAL: Plate Distribution & Netfabb Execution
        dist_result = self.marshal.distribute_parts(
            candidate_paths=valid_paths,
            output_base_dir=target_dir,
            mode=strategy_eval.selected_mode,
        )

        # 4. SENTRY: Verify each generated plate
        from core.strategy_selector import BoundingBox

        geom_map = {g.part.file_path.name: g.bbox for g in strategy_eval.valid_geometries}
        for plate in dist_result.plates:
            placed_boxes = []
            if plate.netfabb_result and plate.netfabb_result.placed_boxes:
                for b in plate.netfabb_result.placed_boxes:
                    box = BoundingBox(min_x=b[1], max_x=b[2], min_y=b[3], max_y=b[4], min_z=b[5], max_z=b[6])
                    placed_boxes.append(PlacedPartBox(name=b[0], bbox=box))
            else:
                for p in plate.packed_files:
                    bbox = geom_map.get(p.name)
                    if bbox:
                        placed_boxes.append(PlacedPartBox(name=p.name, bbox=bbox))

            plate.verification_report = self.sentry.verify_placed_boxes(placed_boxes)

        # 5. MARSHAL: Compile Manifest & Work Order
        manifest_path = None
        if not self.config.dry_run:
            manifest_path = self.manifest_writer.write_manifest(
                target_dir=target_dir,
                scan_result=scan_result,
                strategy_eval=strategy_eval,
                dist_result=dist_result,
            )
            # Print human-readable work order to log
            summary_text = self.manifest_writer.format_work_order_summary(manifest_path)
            Logger.info(f"\n{summary_text}")
        else:
            Logger.info("[LP AGENT] Dry-run mode: skipping disk writes of master manifest.")

        completed_at = datetime.now()
        Logger.info(
            f"=== [LP AGENT] Completed: {dist_result.total_parts_assigned} parts allocated across "
            f"{dist_result.total_plates_generated} plates in {(completed_at - started_at).total_seconds():.1f}s ==="
        )

        return PipelineExecutionSummary(
            success=True,
            date_str=effective_date,
            target_directory=target_dir,
            mode_selected=strategy_eval.selected_mode.value,
            total_scanned_parts=len(scan_result.valid_parts) + len(scan_result.quarantined_files),
            total_valid_parts=len(scan_result.valid_parts),
            total_quarantined=len(scan_result.quarantined_files),
            total_plates_generated=dist_result.total_plates_generated,
            plates=dist_result.plates,
            strategy_eval=strategy_eval,
            manifest_path=manifest_path,
            started_at=started_at,
            completed_at=completed_at,
        )
