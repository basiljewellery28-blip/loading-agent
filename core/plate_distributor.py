"""plate_distributor.py — Subagent Marshal: Plate Distributor & Overflow Architect.

Manages plate overflow: when the solver reports leftovers, Marshal iterates across
Plate 1, Plate 2, etc. until 100% of candidate parts are nested and allocated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.collision_checker import CollisionChecker, VerificationReport
from core.netfabb_runner import NetfabbRunner, NetfabbRunResult
from utils.logger import Logger


@dataclass
class PlatePackage:
    """A fully generated and verified build plate."""

    plate_index: int
    plate_name: str
    plate_dir: Path
    mode: StrategyMode
    packed_files: list[Path]
    netfabb_result: NetfabbRunResult
    verification_report: VerificationReport | None = None
    merged_plate_stl: Path | None = None
    fabbproject_path: Path | None = None


@dataclass
class DistributionResult:
    """Outcome of distributing all candidate parts across one or more plates."""

    total_parts_assigned: int
    total_plates_generated: int
    plates: list[PlatePackage] = field(default_factory=list)
    unassigned_files: list[Path] = field(default_factory=list)


class PlateDistributor:
    """Subagent Marshal: Orchestrates multi-plate overflow allocation."""

    def __init__(self, config: LPConfig, runner: NetfabbRunner, verifier: CollisionChecker):
        self.config = config
        self.runner = runner
        self.verifier = verifier

    def distribute_parts(
        self,
        candidate_paths: list[Path],
        output_base_dir: Path,
        mode: StrategyMode = StrategyMode.NESTING_2D,
        max_plates: int = 10,
    ) -> DistributionResult:
        """Distribute candidate STL files across build plates until all are packed."""
        remaining_pool = list(candidate_paths)
        generated_plates: list[PlatePackage] = []
        plate_idx = 1

        Logger.info(
            f"[MARSHAL] Starting plate distribution for {len(remaining_pool)} parts "
            f"(Mode: {mode.value.upper()}) at {output_base_dir}"
        )

        while remaining_pool and plate_idx <= max_plates:
            plate_name = f"Plate_{plate_idx:02d}"
            plate_dir = output_base_dir / plate_name

            Logger.info(f"[MARSHAL] Allocating to {plate_name} ({len(remaining_pool)} candidate files remaining)...")

            run_result = self.runner.execute_nesting(
                stl_paths=remaining_pool,
                output_dir=output_base_dir,
                plate_index=plate_idx,
                mode=mode,
            )

            # Match packed filenames back to Path objects
            packed_names_set = set(run_result.packed_files)
            packed_this_round: list[Path] = [p for p in remaining_pool if p.name in packed_names_set]

            # In simulation / fallback where packed_files is empty but success is true
            if not packed_this_round and run_result.success:
                packed_this_round = list(remaining_pool)

            leftover_this_round = [p for p in remaining_pool if p not in packed_this_round]

            pkg = PlatePackage(
                plate_index=plate_idx,
                plate_name=plate_name,
                plate_dir=plate_dir,
                mode=mode,
                packed_files=packed_this_round,
                netfabb_result=run_result,
                verification_report=None,  # Populated during verification stage
                merged_plate_stl=run_result.merged_plate_stl,
                fabbproject_path=run_result.fabbproject_path,
            )
            generated_plates.append(pkg)

            Logger.info(
                f"[MARSHAL] {plate_name} allocated: {len(packed_this_round)} packed, "
                f"{len(leftover_this_round)} leftover."
            )

            # Check if packing made progress
            if not packed_this_round:
                Logger.error(f"[MARSHAL] Zero parts packed for {plate_name}. Halting loop to prevent infinite cycle.")
                break

            remaining_pool = leftover_this_round
            plate_idx += 1

        total_assigned = sum(len(p.packed_files) for p in generated_plates)
        return DistributionResult(
            total_parts_assigned=total_assigned,
            total_plates_generated=len(generated_plates),
            plates=generated_plates,
            unassigned_files=remaining_pool,
        )
