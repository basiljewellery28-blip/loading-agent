"""quantity.py — Filename quantity suffixes and part instance expansion.

A trailing `xN` on a print file means "put N of this on the plate":

    6104-I-1.50mm-NFG-219956-PP x2 (repaired).stl   -> 2 copies
    MC9841-0.50ct Vintage Cushion-219962-PP1 X2.stl -> 2 copies
    9621-(7x5mm)-v2-(10119 GA)-PP1 (repaired) X29   -> 29 copies

Only the multiplier at the *end* of the name counts. Jewellery filenames are full of
`x` characters that are stone dimensions, not quantities, and multiplying by one of those
would flood a plate with parts nobody ordered:

    9455-R-3.30mmx7 Protea Eternity-WFG--219933-PP (repaired)  -> 1  (7 stones, not x7)
    6104-Q-(14X1.5mm)-v1-219936-PP (repaired)                  -> 1  (14 stones of 1.5mm)
    MC9461-10x7mm-Tanzanite-215288-PP1 (repaired)              -> 1  (a 10x7mm stone)

The multiplier may sit before or after the `(repaired)` tags, with or without a space,
so repair tags are stripped before the trailing match is attempted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from utils.logger import Logger

# Tags a repair tool appends. Stripped before looking for the trailing multiplier so that
# both "PP x2 (repaired)" and "PP (repaired) x2" resolve to the same quantity.
_TRAILING_TAGS = re.compile(
    r"\(\s*(?:repaired|self-intersections\s+removed)\s*\)",
    re.IGNORECASE,
)

# A trailing multiplier not glued to a letter. The negative lookbehind is what rejects
# "...Matrix2" and mid-name dimensions, because those have a letter immediately before x.
_QTY_GENERAL = re.compile(r"(?<![A-Za-z])[xX]\s*(\d{1,3})\s*$")

# The one place a letter may precede the multiplier: directly after the PP part marker,
# as in "Star of David-20mm-PPx2". Anchored to PP so it cannot match ordinary words.
_QTY_AFTER_PP = re.compile(r"PP\s*\d*\s*[xX]\s*(\d{1,3})\s*$", re.IGNORECASE)

# Guard against a malformed name multiplying a plate into oblivion. Set well above real
# repeat-production runs (x120 solitaire heads is an ordinary overnight batch) because the
# true limit is plate capacity, which the packer already enforces by spilling to the next
# plate. This only catches a typo.
MAX_QUANTITY = 500


def parse_quantity(filename: str) -> int:
    """Return how many copies `filename` asks for. Defaults to 1.

    Accepts a bare name, a stem, or a full path string.
    """
    stem = Path(filename).stem if filename.lower().endswith(".stl") else filename
    cleaned = _TRAILING_TAGS.sub(" ", stem).strip()

    match = _QTY_GENERAL.search(cleaned) or _QTY_AFTER_PP.search(cleaned)
    if not match:
        return 1

    qty = int(match.group(1))
    if qty < 1:
        return 1
    if qty > MAX_QUANTITY:
        Logger.warning(
            f"[SCOUT] '{stem}' requests {qty} copies, above the {MAX_QUANTITY} ceiling. "
            f"Capping to {MAX_QUANTITY}."
        )
        return MAX_QUANTITY
    return qty


@dataclass(frozen=True)
class PartInstance:
    """One copy of a source STL to be placed on a plate.

    A quantity-2 file yields two instances sharing a `source` but carrying distinct
    `name`s. The distinct name matters: packed/leftover reconciliation is done by name, so
    identical names would make two copies indistinguishable and a partially-placed pair
    would be miscounted as fully packed.
    """

    source: Path
    copy_index: int = 1
    copy_total: int = 1

    @property
    def name(self) -> str:
        """Unique display name; identical to the filename for single-copy parts.

        This name is used as a real filename when copies are staged to disk for Netfabb,
        so it must contain no path separators or other characters Windows rejects. An
        earlier "[1/2]" form silently turned the stage path into a missing subdirectory
        and every copy failed to stage with WinError 3.
        """
        if self.copy_total <= 1:
            return self.source.name
        return f"{self.source.stem} [{self.copy_index} of {self.copy_total}]{self.source.suffix}"

    @property
    def source_name(self) -> str:
        """The original filename on disk, shared by every copy."""
        return self.source.name

    @property
    def is_copy(self) -> bool:
        return self.copy_total > 1

    def stage_name(self) -> str:
        """`name` made safe to use as a filename on disk.

        Belt and braces around `name`: any character Windows rejects in a filename is
        replaced rather than allowed to corrupt a staging path.
        """
        safe = self.name
        for bad in '<>:"/\\|?*':
            safe = safe.replace(bad, "_")
        return safe.strip() or self.source.name

    def __fspath__(self) -> str:
        """Allow an instance to be used wherever the source path is expected."""
        return str(self.source)


def expand_instances(paths: list[Path]) -> list[PartInstance]:
    """Expand each path into as many instances as its filename requests."""
    instances: list[PartInstance] = []
    multiplied: list[str] = []

    for path in paths:
        qty = parse_quantity(path.name)
        if qty > 1:
            multiplied.append(f"{path.name} x{qty}")
        for i in range(1, qty + 1):
            instances.append(PartInstance(source=path, copy_index=i, copy_total=qty))

    if multiplied:
        Logger.info(
            f"[SCOUT] Quantity suffixes expanded {len(multiplied)} file(s) into "
            f"{len(instances)} parts: " + "; ".join(multiplied[:8])
            + (" ..." if len(multiplied) > 8 else "")
        )

    return instances


def as_instances(items: list) -> list[PartInstance]:
    """Normalise a mixed list of Paths and PartInstances to PartInstances.

    Paths are taken at face value here (one instance each) rather than re-expanded, so a
    caller that has already expanded quantities cannot accidentally multiply twice.
    """
    normalised: list[PartInstance] = []
    for item in items:
        if isinstance(item, PartInstance):
            normalised.append(item)
        else:
            normalised.append(PartInstance(source=Path(item)))
    return normalised
