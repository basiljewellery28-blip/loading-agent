# Loading Prints Agent (LP Agent)

Automated 2D/3D Nesting & Build Plate Preparation Engine for Fine Jewelry Manufacturing on the **Flashforge WaxJet 51C**.

---

## Overview

**LP Agent** is the third automation stage in the Browns Jewellery CAD-to-Casting pipeline:
1. **jewellery-cad-agent**: Resolves SEA.Net job bags, identifies styles, finger sizes, flow gates (WFG vs NFG), and stages files to `Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\`.
2. **CAD Agent**: Automates MatrixGold/Rhino, stamps sprues with bag numbers, validates meshes, runs Netfabb Extended Repair, and outputs `* (repaired).stl`.
3. **LP Agent (This Project)**: Ingests repaired STLs from today's date folder, executes 2D Flat Nesting or 3D Packing via headless Autodesk Netfabb 2027 (`netfabb_console.exe`), ensures parts never touch or interlock, and outputs WaxJet 51C build plates (`merged_plate_XX.stl`, `.fabbproject`, and `plate_manifest.json`).

---

## Core Nesting Capabilities

- **2D Flat Nesting**: Parts are laid flat across the build platform ($Z = 0.5\text{ mm}$ base clearance). Keeps vertical build height low to minimize print time (e.g. 3–4 hours vs 16 hours) and prevent support wax entrapment.
- **3D Packing**: TrueShape 3D volumetric packing for mass repeat production (RPM) batches of identical or similar styles.
- **Collision Detection & Interference Checking**: Continuous mathematical boundary calculation preventing mesh intersections.
- **Clearance Buffer (Padding)**: Solid $2.0\text{ mm}$ safety zone around each model (minimum $1.5\text{ mm}$) to ensure clean post-print wax washing.
- **Interlocking & Entanglement Prevention**: Topological checking ensuring ring shanks and loops never hook through each other.
- **Multi-Plate Overflow Handling**: Leftover parts automatically populate sequential plates (Plate 1, Plate 2, ...).

---

## Hardware Target: Flashforge WaxJet 51C

- **Platform Size**: $235.00\text{ mm (X)} \times 138.00\text{ mm (Y)} \times 100.00\text{ mm (Z)}$
- **Print Material**: FFD-100 Real Purple Casting Wax
- **Support Material**: FFS-200 Dissolvable Support Wax
- **Layer Thickness**: $0.032\text{ mm}$ (32 microns)

---

## Quick Start

### Option 1: Web UI Dashboard (Recommended for PMs & Operators)
```bat
:: Launch the browser-based 3D control center
python agent_entry.py ui --port 4200
```
Opens an interactive 3D platform with real-time collision telemetry, Netfabb integration, and plate switching at `http://localhost:4200`.

### Option 2: Command Line (Production & Batch Scripting)
```bat
:: Daily production run (scans today's date folder on Q:)
python agent_entry.py load

:: Dry run (preview layout and plate counts without exporting files)
python agent_entry.py load --dry-run

:: Force 2D Flat Nesting
python agent_entry.py load --mode 2d

:: Force 3D Packing
python agent_entry.py load --mode 3d
```

---

## Documentation

- **[GUIDE.md](file:///c:/Users/21824341/Desktop/Dev%20Projects/LP%20Agent/GUIDE.md)** — **Complete User & Architecture Guide (How to use & how it works)**
- [AGENTS.md](file:///c:/Users/21824341/Desktop/Dev%20Projects/LP%20Agent/AGENTS.md) — Subagent Team Responsibilities (Scout, Tactician, Vulcan, Sentry, Marshal)
- [MASTER_PROMPT.md](file:///c:/Users/21824341/Desktop/Dev%20Projects/LP%20Agent/MASTER_PROMPT.md) — Master Architecture, Algorithmic Logic, and Context Engineering Directive
- [CLAUDE.md](file:///c:/Users/21824341/Desktop/Dev%20Projects/LP%20Agent/CLAUDE.md) — Operating Manual & Development Guardrails
