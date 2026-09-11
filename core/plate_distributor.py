"""plate_distributor.py — Subagent Marshal: Plate Distributor & Overflow Architect.

Manages plate overflow: when the solver reports leftovers, Marshal iterates across
Plate 1, Plate 2, etc. until 100% of candidate parts are nested and allocated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from core.collision_checker import CollisionChecker, VerificationReport
from core.height_policy import (
    EXCEED,
    DecideFn,
    HeightDecision,
    PlanOption,
    needs_decision,
    resolve,
)
from core.native_packer import pack_shelf
from core.netfabb_runner import NetfabbRunner, NetfabbRunResult
from core.quantity import PartInstance, as_instances
from utils.logger import Logger

ProgressFn = Callable[[str, float], None]


@dataclass
class PlatePackage:
    """A fully generated and verified build plate."""

    plate_index: int
    plate_name: str
    plate_dir: Path
    mode: StrategyMode
    packed_files: list[PartInstance]
    netfabb_result: NetfabbRunResult
    verification_report: VerificationReport | None = None
    merged_plate_stl: Path | None = None
    fabbproject_path: Path | None = None

    @property
    def plate_built(self) -> bool:
        """True only when a real merged plate STL was produced for this plate."""
        return bool(self.netfabb_result and self.netfabb_result.plate_built)

    @property
    def builder(self) -> str:
        """Which engine produced this plate: 'netfabb', 'lp-native', or 'none'."""
        return self.netfabb_result.builder if self.netfabb_result else "none"


@dataclass
class DistributionResult:
    """Outcome of distributing all candidate parts across one or more plates."""

    total_parts_assigned: int
    total_plates_generated: int
    plates: list[PlatePackage] = field(default_factory=list)
    unassigned_files: list[PartInstance] = field(default_factory=list)


class PlateDistributor:
    """Subagent Marshal: Orchestrates multi-plate overflow allocation."""

    def __init__(self, config: LPConfig, runner: NetfabbRunner, verifier: CollisionChecker):
        self.config = config
        self.runner = runner
        self.verifier = verifier

    def _plan_height(
        self,
        pool: list[PartInstance],
        mode: StrategyMode,
        limit_mm: float,
    ) -> PlanOption:
        """Cost one plate against a Z ceiling, without writing anything.

        Pure bounding-box arithmetic -- milliseconds -- which is what makes it affordable
        to plan twice and put a real choice in front of the operator before merging.
        """
        plan = pack_shelf(self.config, pool, mode=mode, height_limit_mm=limit_mm)
        return PlanOption(
            parts_placed=len(plan.placed),
            parts_left=len(plan.leftover) + len(plan.oversize),
            tiers=plan.layers_used,
            height_mm=plan.plate_height_mm,
            height_limit_mm=limit_mm,
        )

    def _height_limit_for_plate(
        self,
        plate_name: str,
        pool: list[PartInstance],
        mode: StrategyMode,
        decide: DecideFn | None,
    ) -> float:
        """Decide this plate's Z ceiling, asking the operator when the cap costs parts."""
        if mode != StrategyMode.PACKING_3D:
            return self.config.platform_z

        cap = min(self.config.max_plate_height_mm, self.config.platform_z)
        machine = self.config.platform_z

        capped = self._plan_height(pool, mode, cap)
        if cap >= machine:
            return cap

        uncapped = self._plan_height(pool, mode, machine)
        if not needs_decision(capped, uncapped):
            # Lifting the cap buys nothing here, so there is nothing to weigh up.
            return cap

        decision = HeightDecision(plate_name=plate_name, capped=capped, uncapped=uncapped)
        Logger.info(f"[MARSHAL] Height cap decision for {decision.summary()}")

        choice = resolve(self.config.on_cap_exceeded, decision, ask=decide)
        if choice == EXCEED:
            Logger.warning(
                f"[MARSHAL] {plate_name} will exceed the {cap:.0f} mm cleaning cap "
                f"({uncapped.height_mm:.1f} mm) to hold "
                f"{decision.extra_parts_if_exceeded} more part(s)."
            )
            return machine

        Logger.info(
            f"[MARSHAL] {plate_name} respects the {cap:.0f} mm cap; "
            f"{decision.extra_parts_if_exceeded} part(s) move to the next plate."
        )
        return cap

    def distribute_parts(
        self,
        candidate_paths: list[Path] | list[PartInstance],
        output_base_dir: Path,
        mode: StrategyMode = StrategyMode.NESTING_2D,
        max_plates: int = 10,
        progress: ProgressFn | None = None,
        decide: DecideFn | None = None,
    ) -> DistributionResult:
        """Distribute candidate STL files across build plates until all are packed.

        Accepts paths or already-expanded `PartInstance`s. Reconciliation is by instance
        name, so the copies of a quantity-N file are tracked independently and a partly
        placed set correctly spills its remainder onto the next plate.
        """
        remaining_pool = as_instances(candidate_paths)
        generated_plates: list[PlatePackage] = []
        plate_idx = 1
        total_candidates = len(remaining_pool)

        Logger.info(
            f"[MARSHAL] Starting plate distribution for {len(remaining_pool)} parts "
            f"(Mode: {mode.value.upper()}) at {output_base_dir}"
        )

        while remaining_pool and plate_idx <= max_plates:
            plate_name = f"Plate_{plate_idx:02d}"
            plate_dir = output_base_dir / plate_name

            Logger.info(f"[MARSHAL] Allocating to {plate_name} ({len(remaining_pool)} candidate files remaining)...")

            assigned_before = total_candidates - len(remaining_pool)

            # Loop variables are bound as defaults so the callback reports this plate's
            # numbers even though the loop reassigns them before the next iteration.
            def plate_progress(
                stage: str,
                fraction: float,
                _base: int = assigned_before,
                _pool_size: int = len(remaining_pool),
                _name: str = plate_name,
            ) -> None:
                """Report plate-local progress as overall batch progress."""
                if progress is None:
                    return
                pool_share = _pool_size * max(0.0, min(1.0, fraction))
                overall = (_base + pool_share) / total_candidates if total_candidates else 0.0
                progress(f"{_name}: {stage}", min(0.99, overall))

            # Settle this plate's Z ceiling before packing it for real: the choice is made
            # on a cheap plan, so the operator is never asked to wait on a merge first.
            height_limit = self._height_limit_for_plate(plate_name, remaining_pool, mode, decide)

            run_result = self.runner.execute_nesting(
                stl_paths=remaining_pool,
                output_dir=output_base_dir,
                plate_index=plate_idx,
                mode=mode,
                progress=plate_progress if progress else None,
                height_limit_mm=height_limit,
            )

            # Match packed filenames back to Path objects.
            #
            # There is deliberately no "success implies everything packed" fallback here.
            # That shortcut is what made overflow invisible: a fallback run reported every
            # candidate as packed, so Plate 02 was never opened and the surplus parts were
            # recorded on a plate they physically did not fit on.
            packed_names_set = set(run_result.packed_files)
            packed_this_round: list[PartInstance] = [
                p for p in remaining_pool if p.name in packed_names_set
            ]

            packed_ids = {id(p) for p in packed_this_round}
            leftover_this_round = [p for p in remaining_pool if id(p) not in packed_ids]

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

            build_note = (
                f"built by {run_result.builder}, "
                f"{run_result.merged_stl_triangles:,} triangles"
                if run_result.plate_built
                else f"NOT BUILT ({run_result.failure_reason or 'no plate STL produced'})"
            )
            Logger.info(
                f"[MARSHAL] {plate_name} allocated: {len(packed_this_round)} packed, "
                f"{len(leftover_this_round)} leftover - {build_note}."
            )

            # Check if packing made progress
            if not packed_this_round:
                Logger.error(f"[MARSHAL] Zero parts packed for {plate_name}. Halting loop to prevent infinite cycle.")
                # This plate holds nothing; drop it so the manifest does not advertise an
                # empty plate, and surface the pool as unassigned instead.
                generated_plates.pop()
                break

            remaining_pool = leftover_this_round
            plate_idx += 1

        if remaining_pool:
            Logger.error(
                f"[MARSHAL] {len(remaining_pool)} part(s) could not be assigned to any plate "
                f"within the {max_plates}-plate limit: "
                + ", ".join(p.name for p in remaining_pool[:10])
                + (" ..." if len(remaining_pool) > 10 else "")
            )

        total_assigned = sum(len(p.packed_files) for p in generated_plates)
        return DistributionResult(
            total_parts_assigned=total_assigned,
            total_plates_generated=len(generated_plates),
            plates=generated_plates,
            unassigned_files=remaining_pool,
        )
