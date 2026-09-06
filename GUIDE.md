# LP Agent — User & Architecture Guide

Comprehensive operating manual and architectural reference for the **Loading Prints Agent (LP Agent)** — automated 2D/3D build plate preparation for the **Flashforge WaxJet 51C** fine jewellery 3D printer.

---

## Table of Contents

1. [Overview & Role in the Pipeline](#1-overview--role-in-the-pipeline)
2. [How It Works: The 5-Subagent Architecture](#2-how-it-works-the-5-subagent-architecture)
   - [Scout — Date Scanner & Provenance Ingestor](#scout--date-scanner--provenance-ingestor)
   - [Tactician — Strategy & Footprint Selector](#tactician--strategy--footprint-selector)
   - [Vulcan — Headless Netfabb Lua Automation](#vulcan--headless-netfabb-lua-automation)
   - [Sentry — Collision & Clearance Verifier](#sentry--collision--clearance-verifier)
   - [Marshal — Plate Distributor & Manifest Architect](#marshal--plate-distributor--manifest-architect)
3. [How to Use the Agent](#3-how-to-use-the-agent)
   - [Method A: Web UI (Browser-Based Dashboard)](#method-a-web-ui-browser-based-dashboard)
   - [Method B: Command Line Interface (CLI)](#method-b-command-line-interface-cli)
4. [Understanding Generated Output Files](#4-understanding-generated-output-files)
5. [Hardware & Nesting Parameters Reference](#5-hardware--nesting-parameters-reference)
6. [Troubleshooting & FAQs](#6-troubleshooting--faqs)

---

## 1. Overview & Role in the Pipeline

In fine jewellery manufacturing at Browns The Diamond Store, custom CAD designs flow through an automated digital-to-physical pipeline:

```
┌─────────────────────────┐
│   jewellery-cad-agent   │  Resolves SEA.Net job bags, styles, finger sizes, sprues
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│        CAD Agent        │  MatrixGold/Rhino automation, stamps bag IDs, runs Netfabb repair
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  LP Agent (This Engine) │  Automated 2D/3D nesting, collision checking, plate generation
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│   Flashforge WaxJet 51C │  Multi-jet wax 3D printing (Purple casting wax + white support)
└─────────────────────────┘
```

### The Problem LP Agent Solves
Manual build plate preparation in jewellery 3D printing is slow (20–45 minutes per plate) and error-prone. Misplaced parts can touch, interlock through ring shanks, or breach safety buffers, causing expensive cast failures or wax breakage during post-print dissolution.

**LP Agent** completely automates this process:
- Ingests verified, repaired STLs from the day's production queue.
- Computes optimal 2D flat or 3D stacked nesting layouts.
- Automates Autodesk Netfabb headlessly via Lua scripting.
- Rigorously validates mathematical clearances ($2.0\text{ mm}$ buffer, zero collisions).
- Produces unified build plate STLs, editable Netfabb `.fabbproject` files, and JSON machine manifests.

---

## 2. How It Works: The 5-Subagent Architecture

LP Agent is divided into five specialized, decoupled subagents:

```
                ┌──────────────┐
                │  Date Queue  │ (Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\)
                └──────┬───────┘
                       │
                       ▼
            ┌──────────────────────┐
            │   🔍 1. SCOUT        │ core/date_scanner.py
            │   Provenance Filter  │ - Prefers *(repaired).stl
            │   Multi-Part Check   │ - Checks PP1..PPn completeness
            └──────────┬───────────┘
                       │ Validated STL list
                       ▼
            ┌──────────────────────┐
            │   🧠 2. TACTICIAN    │ core/strategy_selector.py
            │   AABB & Footprint   │ - 2D Flat: Daily mixed orders (fast print)
            │   Homogeneity Check  │ - 3D Pack: High-volume repeat orders (RPM)
            └──────────┬───────────┘
                       │ Chosen strategy & nesting parameters
                       ▼
            ┌──────────────────────┐
            │   ⚙️ 3. VULCAN       │ core/netfabb_runner.py
            │   Lua Script Engine  │ - Generates Netfabb Lua script
            │   Headless Runner    │ - Spawns netfabb_console.exe
            └──────────┬───────────┘
                       │ Placed mesh coordinates
                       ▼
            ┌──────────────────────┐
            │   🛡️ 4. SENTRY       │ core/collision_checker.py
            │   Collision Gate     │ - Mesh intersection: Mesh_A ∩ Mesh_B = ∅
            │   Clearance Check    │ - Distance >= 1.5mm (target 2.0mm)
            │   Envelope Check     │ - Fits within 235 × 138 × 100 mm
            └──────────┬───────────┘
                       │ Verified placements
                       ▼
            ┌──────────────────────┐
            │   📦 5. MARSHAL      │ core/plate_distributor.py & manifest/
            │   Plate Overflow     │ - Generates Plate 1, Plate 2...
            │   Mesh Merging       │ - merged_plate_XX.stl
            │   CAD Export         │ - plate_XX.fabbproject & manifest.json
            └──────────────────────┘
```

### Scout — Date Scanner & Provenance Ingestor
- **File:** `core/date_scanner.py`
- **What it does:** Scans the target date folder (e.g. `06.09.2026`).
- **Provenance Rules:**
  - If both `style_PP1.stl` and `style_PP1 (repaired).stl` exist, Scout strictly selects the `(repaired)` version.
  - Multi-component assemblies (e.g. `PP1`, `PP2`, `PP3`, `PP4` representing halo, shank, head, and collet) must be completely present. If any component is missing, the entire set is held to prevent orphaned casts.
  - Corrupt or 0-byte files are quarantined to `exceptions/`.

### Tactician — Strategy & Footprint Selector
- **File:** `core/strategy_selector.py`
- **What it does:** Calculates 3D Axis-Aligned Bounding Boxes (AABB) and surface footprints for every candidate model.
- **Decision Logic:**
  - Compares total footprint + padding against the WaxJet 51C platform ($32,430\text{ mm}^2$).
  - **2D Flat Nesting:** Default for mixed daily custom orders. Keeps all parts in a single layer at $Z = 0.5\text{ mm}$. Minimizes print time (${\sim}3\text{ hours}$ vs $14\text{+ hours}$) and eliminates support wax entrapment inside ring galleries.
  - **3D Packing:** Activated for large homogeneous batches (repeat store orders / RPM) or when weekend high-density runs are required.

### Vulcan — Headless Netfabb Lua Automation
- **File:** `core/netfabb_runner.py`
- **What it does:** Compiles placement instructions into an automated Autodesk Netfabb Lua script and runs `netfabb_console.exe` headlessly.
- **Nesting Parameters Enforced:**
  - `avoid_interlocking = true` (rings never loop through each other)
  - `minimaldistance = 2.0` ($2.0\text{ mm}$ inter-part clearance)
  - `borderspacingxy = 3.0` ($3.0\text{ mm}$ margin from platform edges)
  - `borderspacingz = 0.5` ($0.5\text{ mm}$ lift above the base)

### Sentry — Collision & Clearance Verifier
- **File:** `core/collision_checker.py`
- **What it does:** Performs an **independent, zero-trust quality audit** on placed meshes before any plate is cleared for production.
- **Checks Performed:**
  1. **Envelope Check:** All vertices must lie within $X \in [0, 235]$, $Y \in [0, 138]$, $Z \in [0, 100]\text{ mm}$.
  2. **Hard Collision Detection:** Mesh intersection test ensures no parts overlap ($\text{Mesh}_A \cap \text{Mesh}_B = \emptyset$).
  3. **Clearance Buffer Verification:** Bounding spheres and minimum distances must exceed the $1.5\text{ mm}$ safety threshold (target: $2.0\text{ mm}$).

### Marshal — Plate Distributor & Manifest Architect
- **Files:** `core/plate_distributor.py` & `manifest/manifest_writer.py`
- **What it does:**
  - Manages plate overflow: if parts exceed a single platform, Marshal allocates them across `Plate_01`, `Plate_02`, etc., until 100% of parts are staged.
  - Generates the unified build plate STL (`merged_plate_01.stl`) for the WaxJet slicer.
  - Saves the native Autodesk Netfabb project (`plate_01.fabbproject`) so technicians can inspect or fine-tune individual parts.
  - Writes the JSON manifest (`plate_manifest.json`) linking job bag numbers, CAD styles, coordinates, and Sentry test outcomes.

---

## 3. How to Use the Agent

LP Agent provides two complementary interfaces: a modern **Web UI** for visual inspection and PMs, and a high-performance **CLI** for headless scripting and cron scheduling.

---

### Method A: Web UI (Browser-Based Dashboard)

The Web UI provides a live 3D viewport, real-time telemetry, and one-click plate packing.

#### 1. Launch the UI
```bash
python agent_entry.py ui
```
*To specify a custom port if the default is in use:*
```bash
python agent_entry.py ui --port 4200
```

#### 2. Open in Browser
Navigate to **`http://localhost:4200`**.

#### 3. Step-by-Step UI Workflow

1. **Scan Date Folder (Scout)**
   - Enter a date in `DD.MM.YYYY` format (or click **Today**).
   - Click **⚡ Scan**.
   - Scout reads the directory, lists all verified `*(repaired).stl` parts with dimensions and triangle counts, and renders an initial 3D preview on the plate.

2. **Select Strategy (Tactician)**
   - **Auto:** Let Tactician determine whether 2D Flat or 3D Pack is optimal based on batch size and geometry.
   - **2D Flat:** Force single-layer layout (recommended for mixed daily orders).
   - **3D Pack:** Force volumetric stacking (recommended for repeat batches).

3. **Run Plate Pack**
   - Click **⚡ Run Plate Pack**.
   - Vulcan executes headless Netfabb TrueShape nesting.
   - Sentry verifies clearances and displays green telemetry indicators:
     - **Envelope:** `PASS`
     - **Collisions:** `0`
     - **Buffer:** `≥ 2.0 mm`
     - **Parts:** `X / X allocated`

4. **Inspect in 3D Viewport**
   - **Rotate:** Left-click + drag to orbit around the platform.
   - **Pan:** Right-click + drag (or Shift + left-click).
   - **Zoom:** Mouse wheel.
   - **Preset Angles:** Click **Iso**, **Top**, or **Front** in the top-right toolbar.
   - **Inspect Part:** Hover over any model to view its filename, dimensions, and $(X, Y, Z)$ coordinates.

5. **Export & Machine Dispatch**
   - **🖥️ Open in Netfabb:** Launches Autodesk Netfabb with the generated `.fabbproject` loaded.
   - **💾 Export STL:** Downloads the unified `merged_plate_XX.stl` for slicing.
   - **📋 Manifest:** Opens the full JSON manifest detailing part positions and Sentry audit results.

---

### Method B: Command Line Interface (CLI)

The CLI is ideal for production automation, scripts, and overnight batch runs.

#### Standard Run (Today's Date)
Scans today's date folder on the `Q:` drive (or configured path) and prepares plates:
```bash
python agent_entry.py load
```

#### Dry Run (Preview Mode)
Simulates nesting and clearance verification without writing output files:
```bash
python agent_entry.py load --dry-run
```

#### Specific Date Folder
Process a specific production date:
```bash
python agent_entry.py load --date 06.09.2026
```

#### Force Nesting Mode
Override Tactician's automatic selection:
```bash
# Force 2D Flat Nesting (single layer, minimum Z height)
python agent_entry.py load --mode 2d

# Force 3D Volumetric Packing (multi-layer stack)
python agent_entry.py load --mode 3d
```

#### Custom Clearance Buffer
Adjust the inter-part spacing (default is $2.0\text{ mm}$):
```bash
python agent_entry.py load --clearance 2.5
```

#### Complete CLI Options Reference
```
usage: agent_entry.py load [-h] [--date DATE] [--mode {auto,2d,3d}]
                           [--clearance CLEARANCE] [--dry-run]
                           [--open-netfabb]

options:
  --date DATE           Production date in DD.MM.YYYY format (default: today)
  --mode {auto,2d,3d}   Nesting strategy (default: auto)
  --clearance CLEARANCE Clearance buffer between parts in mm (default: 2.0)
  --dry-run             Simulate without writing files to disk
  --open-netfabb        Automatically launch Netfabb GUI after packing
```

---

## 4. Understanding Generated Output Files

When LP Agent finishes preparing a build plate, it writes the following files to `Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\Plates\Plate_XX\`:

| File Name | Description | Used By |
|-----------|-------------|---------|
| `merged_plate_01.stl` | Unified, single-mesh STL containing all packed models in their exact plate coordinates. | WaxJet Slicer software |
| `plate_01.fabbproject` | Native Autodesk Netfabb project containing each model as an individual component tree entry. | Floor technician inspection / manual adjustments |
| `plate_manifest.json` | Structured JSON log containing job bags, style names, $(X, Y, Z)$ positions, bounding boxes, and Sentry quality validation logs. | Production Tracking / ERP / SEA.Net |
| `exceptions/` | Contains any unreadable, 0-byte, or incomplete multi-part assemblies quarantined by Scout. | CAD technicians for re-export |

### Sample `plate_manifest.json` Structure
```json
{
  "manifest_version": "1.0",
  "generated_at": "2026-09-06T20:18:00Z",
  "machine": "Flashforge WaxJet 51C",
  "build_envelope_mm": { "x": 235.0, "y": 138.0, "z": 100.0 },
  "plate_index": 1,
  "strategy": "3D_PACK",
  "part_count": 196,
  "packed_files": [
    "9847-M-0.25ct-NFG-217244_PP1 (repaired).stl",
    "9847-M-0.25ct-NFG-217244_PP2 (repaired).stl"
  ],
  "verification": {
    "passed": true,
    "has_hard_collisions": false,
    "clearance_violations_count": 0,
    "min_clearance_found_mm": 2.0,
    "envelope_violations": []
  }
}
```

---

## 5. Hardware & Nesting Parameters Reference

### Flashforge WaxJet 51C Build Specifications

| Parameter | Calibrated Value | Notes |
|-----------|------------------|-------|
| **X Dimension (Width)** | $235.00\text{ mm}$ | Left-to-right platform span |
| **Y Dimension (Depth)** | $138.00\text{ mm}$ | Front-to-back platform span |
| **Z Dimension (Height)** | $100.00\text{ mm}$ | Maximum printable vertical height |
| **Total Printable Area** | $32,430\text{ mm}^2$ | Flat single-layer footprint |
| **Print Technology** | Multi-Jet Wax Printing | FFD-100 Purple Wax + FFS-200 Support |
| **Layer Thickness** | $0.032\text{ mm}$ ($32\ \mu\text{m}$) | Ultra-high resolution casting finish |

### Nesting Clearance Rules

- **Platform Edge Margins (`borderspacingxy`):** $3.0\text{ mm}$ margin around all platform edges to prevent wax edge distortion.
- **Base Offset (`borderspacingz`):** $0.5\text{ mm}$ elevation from platform floor to ensure solid support wax foundation.
- **Inter-Part Buffer (`minimaldistance`):** Strict $2.0\text{ mm}$ minimum clearance between any two adjacent models.
- **Interlocking Prevention (`avoid_interlocking`):** Enabled. Netfabb topological checker prevents rings from threading through one another.

---

## 6. Troubleshooting & FAQs

### Q: Why did Scout skip a file in the date folder?
- Check if a `(repaired)` version exists. Scout prefers `* (repaired).stl` over raw `.stl`.
- Check if the file is part of a multi-piece assembly (`_PP1`, `_PP2`). If piece `_PP3` is missing, Scout quarantines the set until all parts are ready.
- Check the file size: files under $1\text{ KB}$ are treated as corrupt and quarantined to `exceptions/`.

### Q: The Web UI says port 8000 (or 4200) is already taken.
Specify any free port with `--port`:
```bash
python agent_entry.py ui --port 4300
```

### Q: Netfabb console indicates it is not found.
LP Agent looks for `netfabb_console.exe` at:
- `C:\Program Files\Autodesk\Netfabb 2027\netfabb_console.exe`
- Or the path configured in `config/lp_config.py` under `NETFABB_CONSOLE_PATH`.
- If Netfabb is not installed on the workstation, LP Agent will run in simulation mode and Sentry will still perform full mathematical placement and collision verification.

### Q: How do I verify all automated tests?
Run the full test suite (31 unit and endpoint tests):
```bash
pytest -v
```
To check code formatting and linting:
```bash
ruff check .
```
