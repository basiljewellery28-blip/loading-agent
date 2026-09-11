"""height_policy.py — Deciding whether a stacked plate may exceed the cleaning height cap.

The machine reaches 100 mm, but a tall stack is punishing to clean: support wax has to be
washed out from between every tier. So plates are packed to a preferred ceiling
(`max_plate_height_mm`, 60 mm by default) even when the machine would allow more.

Respecting that ceiling sometimes costs an extra plate. That is a judgement call between
the cleaner's time and the machine's, so it is surfaced rather than decided silently --
per plate, because the right answer can differ between Plate_01 and Plate_02.

Packing is pure bounding-box arithmetic and takes milliseconds; merging the plate STL takes
minutes. Both options are therefore costed *before* any expensive work, and answering the
prompt costs nothing but the thinking time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# What to do when the cap costs an extra plate.
ASK = "ask"
SPLIT = "split"
EXCEED = "exceed"
FAIL = "fail"

VALID_POLICIES = (ASK, SPLIT, EXCEED, FAIL)

# Unattended runs default to respecting the cap. The cap exists to protect whoever cleans
# the prints, so a run with nobody watching must not quietly make that job harder.
DEFAULT_UNATTENDED_POLICY = SPLIT


@dataclass(frozen=True)
class PlanOption:
    """One costed way to pack a single plate."""

    parts_placed: int
    parts_left: int
    tiers: int
    height_mm: float
    height_limit_mm: float

    @property
    def within_cap(self) -> bool:
        return self.height_mm <= self.height_limit_mm + 1e-6

    def as_dict(self) -> dict:
        return {
            "parts_placed": self.parts_placed,
            "parts_left": self.parts_left,
            "tiers": self.tiers,
            "height_mm": round(self.height_mm, 2),
            "height_limit_mm": round(self.height_limit_mm, 2),
        }


@dataclass(frozen=True)
class HeightDecision:
    """A pending choice between a capped and an uncapped plan for one plate."""

    plate_name: str
    capped: PlanOption
    uncapped: PlanOption

    @property
    def extra_parts_if_exceeded(self) -> int:
        """How many more parts fit on this plate if the cap is lifted."""
        return max(0, self.uncapped.parts_placed - self.capped.parts_placed)

    @property
    def extra_height_if_exceeded(self) -> float:
        return max(0.0, self.uncapped.height_mm - self.capped.height_mm)

    def as_dict(self) -> dict:
        return {
            "plate_name": self.plate_name,
            "capped": self.capped.as_dict(),
            "uncapped": self.uncapped.as_dict(),
            "extra_parts_if_exceeded": self.extra_parts_if_exceeded,
            "extra_height_if_exceeded": round(self.extra_height_if_exceeded, 2),
        }

    def summary(self) -> str:
        """One-line description for logs and the CLI."""
        return (
            f"{self.plate_name}: respecting the {self.capped.height_limit_mm:.0f} mm cap "
            f"fits {self.capped.parts_placed} parts at {self.capped.height_mm:.1f} mm; "
            f"lifting it fits {self.uncapped.parts_placed} at "
            f"{self.uncapped.height_mm:.1f} mm"
        )


# A decider receives the pending choice and returns SPLIT or EXCEED.
DecideFn = Callable[[HeightDecision], str]


def needs_decision(capped: PlanOption, uncapped: PlanOption) -> bool:
    """True when the cap actually costs something on this plate.

    Only a difference in how many parts fit matters. A taller plan that holds no more parts
    is strictly worse and is never worth asking about.
    """
    return uncapped.parts_placed > capped.parts_placed


def resolve(policy: str, decision: HeightDecision, ask: DecideFn | None = None) -> str:
    """Apply `policy` to a pending decision, returning SPLIT or EXCEED.

    `ask` is only consulted for the ASK policy, and only when it is available; without an
    interactive caller the safe course is to respect the cap.
    """
    if policy == EXCEED:
        return EXCEED
    if policy == SPLIT:
        return SPLIT
    if policy == FAIL:
        raise HeightCapExceeded(decision)

    if ask is not None:
        choice = ask(decision)
        if choice in (SPLIT, EXCEED):
            return choice

    return DEFAULT_UNATTENDED_POLICY


class HeightCapExceeded(RuntimeError):
    """Raised under the FAIL policy when a plate cannot honour the height cap."""

    def __init__(self, decision: HeightDecision):
        self.decision = decision
        super().__init__(
            f"{decision.summary()}. Packing refused because --on-cap-exceeded=fail."
        )
