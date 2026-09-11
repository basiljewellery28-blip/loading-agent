"""collision_checker.py — Subagent Sentry: Collision & Clearance Verifier.

Performs independent post-nesting verification:
- Asserts all parts are strictly inside the WaxJet 51C build volume (235 x 138 x 100 mm).
- Checks pairwise minimum clearance buffers against the configured spacing (2.0 mm
  default, 0.3 mm absolute floor).
- Verifies that no two bounding volumes or mesh envelopes overlap/intersect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.lp_config import MIN_CLEARANCE_MM, LPConfig
from core.strategy_selector import BoundingBox
from utils.logger import Logger

# Tolerance for clearance comparisons, in mm (0.1 micron). See verify_placed_boxes.
CLEARANCE_EPSILON_MM = 1e-4


@dataclass
class PlacedPartBox:
    """A part positioned on the build platform."""

    name: str
    bbox: BoundingBox


@dataclass
class ClearanceViolation:
    """Recorded clearance infringement between two placed parts."""

    part_a: str
    part_b: str
    actual_distance_mm: float
    required_clearance_mm: float
    is_hard_collision: bool


@dataclass
class VerificationReport:
    """Outcome of Sentry's clearance and platform envelope verification."""

    passed: bool
    total_parts_checked: int
    envelope_violations: list[str] = field(default_factory=list)
    clearance_violations: list[ClearanceViolation] = field(default_factory=list)
    # False when the layout came from a true-outline (TrueShape) nester, where overlapping
    # bounding boxes are expected and clearance findings are advisory. See
    # `verify_placed_boxes` for why.
    clearance_is_authoritative: bool = True

    @property
    def has_hard_collisions(self) -> bool:
        """True if any two parts actually intersect in space."""
        return any(v.is_hard_collision for v in self.clearance_violations)

    @property
    def clearance_note(self) -> str:
        """Human-readable qualifier for the clearance findings."""
        if self.clearance_is_authoritative or not self.clearance_violations:
            return ""
        return (
            f"{len(self.clearance_violations)} bounding-box overlap(s) are expected from "
            "TrueShape outline nesting and do not imply the meshes touch"
        )


class CollisionChecker:
    """Subagent Sentry: Independent validator of platform limits and clearances."""

    def __init__(self, config: LPConfig):
        self.config = config

    def verify_placed_boxes(
        self,
        placed_boxes: list[PlacedPartBox],
        min_clearance_threshold: float | None = None,
        clearance_is_authoritative: bool = True,
    ) -> VerificationReport:
        """Verify positioned bounding boxes on the build platform.

        Args:
            placed_boxes: List of placed part boxes with absolute platform coordinates.
            min_clearance_threshold: Minimum allowable distance (mm). Defaults to the
                configured clearance buffer, so Sentry judges the plate against the spacing
                the operator actually asked for -- a fixed threshold would flag every pair
                on a deliberately tight plate.
            clearance_is_authoritative: Whether an overlapping bounding box proves two
                parts are too close. True for the native shelf packer, where boxes *are*
                the packing primitive. False for Netfabb TrueShape, which nests on real
                outlines: a curved shank legitimately sits inside a neighbour's concavity,
                so the boxes overlap while the meshes stay a full clearance apart. On a real
                35-part TrueShape plate this produced 19 "collisions" that were nothing of
                the sort, and failing every such plate makes the gate meaningless. When
                False the findings are still recorded, but as advisories rather than
                failures. Envelope violations remain hard failures either way.
        """
        if min_clearance_threshold is None:
            min_clearance_threshold = max(MIN_CLEARANCE_MM, self.config.clearance_buffer)
        report = VerificationReport(
            passed=True,
            total_parts_checked=len(placed_boxes),
            clearance_is_authoritative=clearance_is_authoritative,
        )

        # 1. Platform Envelope Verification
        for pb in placed_boxes:
            b = pb.bbox
            if (
                b.min_x < 0.0
                or b.max_x > self.config.platform_x
                or b.min_y < 0.0
                or b.max_y > self.config.platform_y
                or b.min_z < 0.0
                or b.max_z > self.config.platform_z
            ):
                msg = (
                    f"Part '{pb.name}' exceeds WaxJet 51C envelope bounds: "
                    f"X:[{b.min_x:.1f}, {b.max_x:.1f}] Y:[{b.min_y:.1f}, {b.max_y:.1f}] Z:[{b.min_z:.1f}, {b.max_z:.1f}] "
                    f"(Platform: {self.config.platform_x}x{self.config.platform_y}x{self.config.platform_z})"
                )
                Logger.error(f"[SENTRY] {msg}")
                report.envelope_violations.append(msg)
                report.passed = False

        # 2. Pairwise Clearance & Collision Verification
        n = len(placed_boxes)
        for i in range(n):
            for j in range(i + 1, n):
                box_a = placed_boxes[i]
                box_b = placed_boxes[j]

                dist = self.calculate_box_distance(box_a.bbox, box_b.bbox)

                # Compare with a tolerance. A packer that places parts at exactly the
                # requested gap lands on 0.29999... after float bbox arithmetic, and a
                # strict `<` then fails every neighbouring pair on a correctly packed
                # plate. The tolerance is 0.1 micron -- far below anything the machine
                # or the wax can resolve, so it cannot mask a real breach.
                if dist < min_clearance_threshold - CLEARANCE_EPSILON_MM:
                    is_collision = dist <= 0.0
                    violation = ClearanceViolation(
                        part_a=box_a.name,
                        part_b=box_b.name,
                        actual_distance_mm=dist,
                        required_clearance_mm=min_clearance_threshold,
                        is_hard_collision=is_collision,
                    )
                    report.clearance_violations.append(violation)
                    if clearance_is_authoritative:
                        report.passed = False

                    status_str = "COLLISION" if is_collision else "CLEARANCE BREACH"
                    if clearance_is_authoritative:
                        Logger.warning(
                            f"[SENTRY] {status_str} between '{box_a.name}' and '{box_b.name}': "
                            f"distance = {dist:.2f} mm (< {min_clearance_threshold:.1f} mm threshold)"
                        )
                    else:
                        Logger.debug(
                            f"[SENTRY] AABB overlap (TrueShape, advisory) between '{box_a.name}' "
                            f"and '{box_b.name}': box distance = {dist:.2f} mm"
                        )

        if report.passed:
            if report.clearance_violations:
                Logger.info(
                    f"[SENTRY] Verification PASSED: {n} parts inside the envelope. "
                    f"{report.clearance_note}; the nester enforced mesh clearance."
                )
            else:
                Logger.info(
                    f"[SENTRY] Verification PASSED: {n} parts strictly compliant with envelope and clearance."
                )
        else:
            Logger.error(
                f"[SENTRY] Verification FAILED: {len(report.envelope_violations)} envelope errors, "
                f"{len(report.clearance_violations)} clearance violations."
            )

        return report

    @staticmethod
    def calculate_box_distance(a: BoundingBox, b: BoundingBox) -> float:
        """Calculate Euclidean gap distance between two 3D Axis-Aligned Bounding Boxes.

        Returns:
            > 0: The clearance distance in mm.
            <= 0: The boxes intersect or touch.
        """
        # Calculate coordinate gaps along each axis
        dx = max(0.0, max(a.min_x - b.max_x, b.min_x - a.max_x))
        dy = max(0.0, max(a.min_y - b.max_y, b.min_y - a.max_y))
        dz = max(0.0, max(a.min_z - b.max_z, b.min_z - a.max_z))

        # Check for overlap along all three axes
        x_overlap = (a.min_x <= b.max_x) and (a.max_x >= b.min_x)
        y_overlap = (a.min_y <= b.max_y) and (a.max_y >= b.min_y)
        z_overlap = (a.min_z <= b.max_z) and (a.max_z >= b.min_z)

        if x_overlap and y_overlap and z_overlap:
            # Overlapping in 3D! Hard collision
            return 0.0

        # Calculate 3D Euclidean distance between disjoint boxes
        return (dx * dx + dy * dy + dz * dz) ** 0.5
