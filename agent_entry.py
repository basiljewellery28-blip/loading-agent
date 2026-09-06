"""agent_entry.py — Root CLI entry point for Loading Prints Agent (LP Agent).

Usage:
    python agent_entry.py load [--date DD.MM.YYYY] [--mode auto|2d|3d] [--dry-run]
    python agent_entry.py verify --plate <path_to_merged_stl>
    python agent_entry.py status
    python agent_entry.py ui [--port 8000] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.lp_config import LPConfig, StrategyMode
from pipeline.orchestrator import LPOrchestrator
from utils.logger import Logger


def main() -> int:
    """Main CLI handler."""
    parser = argparse.ArgumentParser(
        description="LP Agent — Automated 2D/3D Nesting & Build Plate Preparation for Flashforge WaxJet 51C.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: load
    load_parser = subparsers.add_parser("load", help="Scan date folder and nest parts onto build plates")
    load_parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to stage (DD.MM.YYYY or YYYY-MM-DD). Defaults to today.",
    )
    load_parser.add_argument(
        "--mode",
        choices=["auto", "2d", "3d"],
        default="auto",
        help="Nesting strategy mode (default: auto)",
    )
    load_parser.add_argument(
        "--clearance",
        type=float,
        default=2.0,
        help="Minimum clearance buffer between parts in mm (default: 2.0)",
    )
    load_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate nesting and plate distribution without writing merged STLs or manifests",
    )
    load_parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Explicitly override target directory to scan",
    )

    # Command: verify
    verify_parser = subparsers.add_parser("verify", help="Verify build plate clearance and limits")
    verify_parser.add_argument(
        "--plate",
        type=str,
        required=True,
        help="Path to merged build plate STL or plate directory",
    )

    # Command: status
    subparsers.add_parser("status", help="Display machine configuration and environment status")

    # Command: ui
    ui_parser = subparsers.add_parser("ui", help="Launch the web-based visual plate staging UI")
    ui_parser.add_argument(
        "--port",
        type=int,
        default=4200,
        help="Server port (default: 4200)",
    )
    ui_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Server host address (default: 127.0.0.1)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    config = LPConfig()

    if args.command == "ui":
        import webbrowser

        import uvicorn

        from ui.server import app as ui_app

        host = args.host
        port = args.port
        url = f"http://{host}:{port}"
        print(f"\n  LP Agent UI starting at {url}")
        print("  Press Ctrl+C to stop.\n")
        webbrowser.open(url)
        uvicorn.run(ui_app, host=host, port=port, log_level="info")
        return 0

    if args.command == "status":
        printing_root = config.resolve_printing_root()
        netfabb_bin = Path(config.netfabb_executable)
        print("===================================================================")
        print(f" LP AGENT — {config.machine_name} Configuration Status")
        print("===================================================================")
        print(f" Build Platform Envelope: {config.platform_x} x {config.platform_y} x {config.platform_z} mm")
        print(f" Usable Area (with border): {config.platform_area:.0f} mm^2")
        print(f" Clearance Buffer:        {config.clearance_buffer} mm")
        print(f" Border Spacing (XY/Z):   {config.border_spacing_xy} mm / {config.border_spacing_z} mm")
        print(f" Avoid Interlocking:      {config.avoid_interlocking}")
        print(f" Printing Network Root:   {printing_root} (Exists: {printing_root.exists()})")
        print(f" Netfabb 2027 Console:    {netfabb_bin} (Exists: {netfabb_bin.is_file()})")
        print("===================================================================")
        return 0

    if args.command == "verify":
        plate_path = Path(args.plate)
        if not plate_path.exists():
            Logger.error(f"Target plate path not found: {plate_path}")
            return 1

        orchestrator = LPOrchestrator(config)
        try:
            bbox, tri_count = orchestrator.tactician.calculate_stl_aabb(plate_path)
            fits = bbox.fits_in_platform(config.platform_x, config.platform_y, config.platform_z)
            status_str = "PASS" if fits else "FAIL"
            print(f"Plate Verification [{status_str}]:")
            print(f"  - Dimensions: {bbox.size_x:.2f} x {bbox.size_y:.2f} x {bbox.size_z:.2f} mm")
            print(f"  - Envelope Limit: {config.platform_x} x {config.platform_y} x {config.platform_z} mm")
            print(f"  - Triangles: {tri_count:,}")
            return 0 if fits else 1
        except Exception as err:
            Logger.error(f"Verification failed with error: {err}")
            return 1

    if args.command == "load":
        # Apply CLI overrides
        config.mode = StrategyMode(args.mode)
        config.clearance_buffer = max(1.5, args.clearance)
        config.dry_run = args.dry_run

        orchestrator = LPOrchestrator(config)
        override_dir = Path(args.dir) if args.dir else None
        summary = orchestrator.run(date_str=args.date, target_directory_override=override_dir)

        return 0 if summary.success else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
