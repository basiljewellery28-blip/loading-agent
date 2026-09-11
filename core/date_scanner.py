"""date_scanner.py — Subagent Scout: Date Scanner & Provenance Ingestor.

Scans the factory date folder, enforces provenance matching (preferring *(repaired).stl),
verifies multi-part completeness (PP1..PPn), and quarantines broken or zero-byte files.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import Logger
from utils.path_guards import resolve_date_folder


@dataclass
class ScannedPart:
    """Represents an ingested STL part."""

    file_path: Path
    stem: str
    is_repaired: bool
    is_multipart: bool
    component_index: int | None
    base_design_key: str
    file_size_bytes: int


@dataclass
class ScanResult:
    """Outcome of scanning a date staging folder."""

    target_directory: Path
    valid_parts: list[ScannedPart] = field(default_factory=list)
    quarantined_files: list[Path] = field(default_factory=list)
    incomplete_designs: dict[str, list[Path]] = field(default_factory=dict)

    @property
    def valid_file_paths(self) -> list[Path]:
        """Return list of valid file paths."""
        return [p.file_path for p in self.valid_parts]


def ensure_windows_share_online(path: Path | str) -> None:
    """Ensure Windows CSC Offline Files transitions online if network share path appears offline."""
    p_str = str(path)
    if not (p_str.lower().startswith("q:") or r"192.1.1.131" in p_str):
        return
    try:
        import subprocess
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Invoke-CimMethod -Namespace 'root\\cimv2' -ClassName 'Win32_OfflineFilesCache' "
            "-MethodName 'TransitionOnline' -Arguments @{ Path = '\\\\192.1.1.131\\cad\\Printing\\Form2\\MJP-2500W\\2026'; Flags = [uint32]0 } "
            "-ErrorAction SilentlyContinue | Out-Null"
        ]
        subprocess.run(cmd, capture_output=True, timeout=5)
    except Exception:
        pass


class DateScanner:
    """Subagent Scout: Ingests and audits candidate STL files from date staging."""

    REPAIRED_SUFFIX = " (repaired)"
    MULTIPART_PATTERN = re.compile(r"^(.*?)-PP(\d+)(?:\s*\(repaired\))*\s*\.stl$", re.IGNORECASE)

    def __init__(self, printing_root: Path | str):
        self.printing_root = Path(printing_root)

    def scan_directory(self, target_dir: Path) -> ScanResult:
        """Scan any arbitrary directory for STL files, enforcing provenance and multi-part completeness.

        Args:
            target_dir: Directory path containing STL files.
        """
        target_dir = Path(target_dir)
        result = ScanResult(target_directory=target_dir)

        if not target_dir.exists():
            ensure_windows_share_online(target_dir)

        if not target_dir.exists():
            Logger.warning(f"[SCOUT] Target directory does not exist: {target_dir}")
            return result

        Logger.info(f"[SCOUT] Scanning directory: {target_dir}")
        stl_files = list(target_dir.glob("*.stl"))
        exceptions_dir = target_dir / "exceptions"

        # 1. Filter out zero-byte or corrupted files
        candidates: list[Path] = []
        file_sizes: dict[Path, int] = {}
        for file_path in stl_files:
            # Skip any files already in an exceptions or plate subdirectory
            if "exceptions" in file_path.parts or any(p.startswith("Plate_") for p in file_path.parts):
                continue

            try:
                size = file_path.stat().st_size
                if size <= 84:  # STL header alone is 80 bytes + 4 bytes triangle count
                    Logger.warning(f"[SCOUT] Quarantining zero-byte or invalid STL: {file_path.name}")
                    self._quarantine_file(file_path, exceptions_dir / "corrupted")
                    result.quarantined_files.append(file_path)
                    continue
                file_sizes[file_path] = size
                candidates.append(file_path)
            except OSError as err:
                Logger.error(f"[SCOUT] Could not stat file {file_path.name}: {err}")
                result.quarantined_files.append(file_path)

        # 2. Provenance Resolution: Prefer *(repaired).stl over raw .stl
        resolved_by_stem: dict[str, Path] = {}
        for file_path in candidates:
            filename = file_path.name
            is_repaired = "(repaired)" in filename.lower()

            # Normalize base stem by removing any trailing (repaired) tags and whitespace
            clean_stem = re.sub(r"(?:\s*\(repaired\))+\s*$", "", file_path.stem, flags=re.IGNORECASE).strip()

            existing = resolved_by_stem.get(clean_stem)
            if existing is None:
                resolved_by_stem[clean_stem] = file_path
            else:
                # If existing is raw but current is repaired, replace it!
                existing_is_repaired = "(repaired)" in existing.name.lower()
                if is_repaired and not existing_is_repaired:
                    Logger.info(f"[SCOUT] Provenance match: replacing raw {existing.name} with repaired {file_path.name}")
                    resolved_by_stem[clean_stem] = file_path

        # 3. Multi-part completeness check (PP1..PPn)
        multipart_groups: dict[str, list[tuple[int, Path]]] = {}
        standalone_parts: list[ScannedPart] = []

        for clean_stem, file_path in resolved_by_stem.items():
            is_repaired = "(repaired)" in file_path.name.lower()
            match = self.MULTIPART_PATTERN.match(file_path.name)

            if match:
                base_design = match.group(1).strip()
                comp_idx = int(match.group(2))
                multipart_groups.setdefault(base_design, []).append((comp_idx, file_path))
            else:
                standalone_parts.append(
                    ScannedPart(
                        file_path=file_path,
                        stem=clean_stem,
                        is_repaired=is_repaired,
                        is_multipart=False,
                        component_index=None,
                        base_design_key=clean_stem,
                        file_size_bytes=file_sizes.get(file_path, 0),
                    )
                )

        # Audit multi-part components: Ensure full consecutive sequence 1..N
        for design_key, items in multipart_groups.items():
            items.sort(key=lambda x: x[0])
            indices = [x[0] for x in items]
            expected_indices = list(range(1, max(indices) + 1))

            if indices != expected_indices or len(indices) < 2:
                # Missing components! Incomplete design set
                Logger.warning(
                    f"[SCOUT] Incomplete multi-part design '{design_key}': present indices {indices} != expected {expected_indices}"
                )
                missing_paths = [x[1] for x in items]
                result.incomplete_designs[design_key] = missing_paths
                for _, path in items:
                    self._quarantine_file(path, exceptions_dir / "incomplete_sets")
                    result.quarantined_files.append(path)
            else:
                for comp_idx, path in items:
                    is_rep = "(repaired)" in path.name.lower()
                    result.valid_parts.append(
                        ScannedPart(
                            file_path=path,
                            stem=path.stem,
                            is_repaired=is_rep,
                            is_multipart=True,
                            component_index=comp_idx,
                            base_design_key=design_key,
                            file_size_bytes=file_sizes.get(path, 0),
                        )
                    )

        result.valid_parts.extend(standalone_parts)
        Logger.info(
            f"[SCOUT] Scan complete: {len(result.valid_parts)} valid parts staged, "
            f"{len(result.quarantined_files)} quarantined."
        )
        return result

    def scan_date(self, date_str: str | None = None, auto_fallback_parent: bool = True) -> ScanResult:
        """Scan target date folder for STL files.

        Args:
            date_str: Optional date string ('DD.MM.YYYY' or 'YYYY-MM-DD'). Defaults to today.
            auto_fallback_parent: If 'Agent/' subfolder is empty/missing, inspects parent date folder.
        """
        agent_folder = resolve_date_folder(self.printing_root, date_str)
        scan_dir = agent_folder

        if not scan_dir.exists() or not any(scan_dir.glob("*.stl")):
            parent_date_dir = agent_folder.parent
            if auto_fallback_parent and parent_date_dir.exists() and any(parent_date_dir.glob("*.stl")):
                Logger.info(f"[SCOUT] 'Agent/' folder empty/missing at {agent_folder}. Falling back to {parent_date_dir}")
                scan_dir = parent_date_dir
            else:
                Logger.warning(f"[SCOUT] Target date folder does not exist or contains no STLs: {scan_dir}")
                return ScanResult(target_directory=scan_dir)

        return self.scan_directory(scan_dir)

    def _quarantine_file(self, file_path: Path, target_dir: Path) -> None:
        """Safely copy or note quarantined files to exceptions directory."""
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / file_path.name
            if not dest.exists():
                shutil.copy2(file_path, dest)
        except Exception as err:
            Logger.error(f"[SCOUT] Failed to quarantine {file_path.name}: {err}")
