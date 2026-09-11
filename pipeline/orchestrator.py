"""orchestrator.py — Pipeline Coordinator for LP Agent.

Coordinates the 5 subagents (Scout, Tactician, Vulcan, Sentry, Marshal) in sequence:
Intake -> Geometric Strategy -> Nesting -> Verification -> Overflow Distribution -> Manifest.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from config.lp_config import LPConfig
from core.collision_checker import CollisionChecker, PlacedPartBox
from core.date_scanner import DateScanner
from core.height_policy import DecideFn
from core.netfabb_runner import NetfabbRunner
from core.plate_distributor import PlateDistributor
from core.strategy_selector import StrategySelector
from manifest.manifest_writer import ManifestWriter
from pipeline.stage_result import PipelineExecutionSummary
from utils.logger import Logger

ProgressFn = Callable[[str, float], None]

# Share of the overall progress bar each stage owns, as (start, end) fractions.
# Packing dominates the wall clock on a real batch, so it gets most of the bar --
# a bar that sprints to 90% and then sits there for ten minutes is worse than none.
_STAGE_SPAN = {
    "scan": (0.00, 0.05),
    "geometry": (0.05, 0.20),
    "pack": (0.20, 0.90),
    "verify": (0.90, 0.96),
    "manifest": (0.96, 1.00),
}


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
        progress: ProgressFn | None = None,
        decide: DecideFn | None = None,
    ) -> PipelineExecutionSummary:
        """Execute end-to-end build plate preparation.

        `progress` is called as (stage_label, fraction) with fraction in [0, 1] across the
        whole run, so a caller can drive a progress bar without knowing the stage layout.
        """
        started_at = datetime.now()
        effective_date = date_str or datetime.now().strftime("%d.%m.%Y")

        def report(stage: str, local_fraction: float, label: str) -> None:
            """Map a stage-local fraction onto the overall progress bar."""
            if progress is None:
                return
            start, end = _STAGE_SPAN[stage]
            clamped = max(0.0, min(1.0, local_fraction))
            progress(label, start + (end - start) * clamped)

        Logger.info(f"=== [LP AGENT] Starting Build Plate Preparation for Date: {effective_date} ===")
        report("scan", 0.0, "Scanning folder for STLs")

        # 1. SCOUT: Ingest & Audit STLs
        if target_directory_override:
            target_dir = Path(target_directory_override)
            Logger.info(f"[SCOUT] Target folder overridden: {target_dir}")
            scan_result = self.scout.scan_directory(target_dir)
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
        report("geometry", 0.0, f"Measuring {len(scan_result.valid_parts)} parts")
        strategy_eval = self.tactician.evaluate_batch(
            scan_result.valid_parts,
            progress=(lambda label, frac: report("geometry", frac, label)) if progress else None,
        )
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
        #
        # Expand filename quantity suffixes first ("...-PP x2" -> two parts on the plate).
        # This is the only place expansion happens; everything downstream works in
        # instances so the copies are packed, verified and reported individually.
        from core.quantity import expand_instances

        instances = expand_instances(valid_paths)
        if len(instances) != len(valid_paths):
            report("pack", 0.0, f"Expanded {len(valid_paths)} files to {len(instances)} parts")

        report("pack", 0.0, "Packing plates")
        dist_result = self.marshal.distribute_parts(
            candidate_paths=instances,
            output_base_dir=target_dir,
            mode=strategy_eval.selected_mode,
            progress=(lambda label, frac: report("pack", frac, label)) if progress else None,
            decide=decide,
        )

        # 4. SENTRY: Verify each generated plate
        report("verify", 0.0, "Verifying clearances")
        from core.strategy_selector import BoundingBox

        geom_map = {g.part.file_path.name: g.bbox for g in strategy_eval.valid_geometries}
        for plate in dist_result.plates:
            placed_boxes = []
            if plate.netfabb_result and plate.netfabb_result.placed_boxes:
                for b in plate.netfabb_result.placed_boxes:
                    box = BoundingBox(min_x=b[1], max_x=b[2], min_y=b[3], max_y=b[4], min_z=b[5], max_z=b[6])
                    placed_boxes.append(PlacedPartBox(name=b[0], bbox=box))
            else:
                # Geometry is keyed by the file on disk; copies of one source share it.
                for p in plate.packed_files:
                    bbox = geom_map.get(p.source_name)
                    if bbox:
                        placed_boxes.append(PlacedPartBox(name=p.name, bbox=bbox))

            # Bounding-box clearance is only inconclusive when Netfabb's own TrueShape
            # packer chose the layout: it nests on real outlines, so overlapping boxes are
            # expected there rather than a defect. Layouts LP Agent decided are
            # bounding-box layouts, including "netfabb-placed" ones where Netfabb only
            # built the plate -- for those an overlap is a genuine collision.
            box_clearance_is_conclusive = plate.builder != "netfabb"
            plate.verification_report = self.sentry.verify_placed_boxes(
                placed_boxes,
                clearance_is_authoritative=box_clearance_is_conclusive,
            )

        # 5. MARSHAL: Compile Manifest & Work Order
        report("manifest", 0.0, "Writing manifest")
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
        report("manifest", 1.0, "Complete")

        # A run only succeeds if every generated plate actually holds geometry. Reporting
        # success for an unbuilt plate is the failure mode this pipeline is guarding against.
        # A dry run writes nothing by design, so it is exempt.
        unbuilt = (
            [] if self.config.dry_run
            else [p.plate_name for p in dist_result.plates if not p.plate_built]
        )
        if unbuilt:
            Logger.error(f"[LP AGENT] Plates with no usable STL: {', '.join(unbuilt)}")

        Logger.info(
            f"=== [LP AGENT] Completed: {dist_result.total_parts_assigned} parts allocated across "
            f"{dist_result.total_plates_generated} plates in {(completed_at - started_at).total_seconds():.1f}s ==="
        )

        error_bits = []
        if unbuilt:
            error_bits.append(f"{len(unbuilt)} plate(s) were not built: {', '.join(unbuilt)}")
        if dist_result.unassigned_files:
            error_bits.append(f"{len(dist_result.unassigned_files)} part(s) could not be placed")

        return PipelineExecutionSummary(
            success=not unbuilt,
            error_message="; ".join(error_bits) or None,
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
