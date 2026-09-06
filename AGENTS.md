# AGENTS.md — Loading Prints Agent (LP Agent) Subagent Team

To keep the codebase modular, robust, and maintainable, responsibilities in `LP Agent` are divided among five specialized subagent modules:

---

## 1. The LP Agent Subagent Team

### 🔍 Scout — The Date Scanner & Provenance Ingestor
- **Module:** `core/date_scanner.py`
- **Responsibilities:**
  - Scans today's date folder: `Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\` (or configured local date folder).
  - Matches provenance: ensures only `* (repaired).stl` files are ingested when available.
  - Multi-part completeness check: for multi-component designs (`PP1..PPn`), verifies all parts are present before staging.
  - Quarantines unreadable or zero-byte files to `exceptions/`.

### 🧠 Tactician — The Strategy & Footprint Evaluator
- **Module:** `core/strategy_selector.py`
- **Responsibilities:**
  - Calculates Axis-Aligned Bounding Boxes (AABB) and approximate footprint areas for all candidate models.
  - Compares total footprint + padding against the WaxJet 51C platform area ($32,430\text{ mm}^2$).
  - Evaluates batch homogeneity (percentage of identical styles or designs).
  - Determines the nesting mode:
    - **2D Flat Nesting:** Default for mixed daily orders; minimizes Z-height and print time.
    - **3D Packing:** Activated for large homogeneous batches (RPM repeat orders) or high-density weekend runs.

### ⚙️ Vulcan — The Netfabb Lua Automation Runner
- **Module:** `core/netfabb_runner.py`
- **Responsibilities:**
  - Injects paths and parameters into the calibrated Netfabb Lua script template.
  - Spawns and monitors `netfabb_console.exe` in headless mode.
  - Enforces TrueShape packing parameters:
    - `avoid_interlocking = true`
    - `minimaldistance = 2.0`
    - `borderspacingxy = 3.0`
    - `borderspacingz = 0.5`
  - Monitors execution timeout (default 180s) and handles exit codes gracefully.

### 🛡️ Sentry — The Collision & Clearance Verifier
- **Module:** `core/collision_checker.py`
- **Responsibilities:**
  - Performs independent post-nesting verification of placed parts before approval.
  - Validates that no two meshes intersect ($\text{Mesh}_A \cap \text{Mesh}_B = \emptyset$).
  - Checks that minimum boundary clearance ($\ge 1.5\text{ mm}$, target $2.0\text{ mm}$) is preserved across all pairs.
  - Validates that all parts remain strictly within the WaxJet 51C build envelope ($235 \times 138 \times 100\text{ mm}$).

### 📦 Marshal — The Plate Distributor & Manifest Architect
- **Module:** `core/plate_distributor.py` & `manifest/manifest_writer.py`
- **Responsibilities:**
  - Manages plate overflow: if the solver reports leftovers (`isLeftover`), Marshal initiates Plate 2, Plate 3, etc. until 100% of parts are allocated.
  - Triggers mesh unification (`luamesh:merge`) to generate the unified build plate STL (`merged_plate_XX.stl`).
  - Saves the native Autodesk Netfabb project (`plate_XX.fabbproject`) for visual inspection on the workshop floor.
  - Writes the comprehensive `plate_manifest.json` linking bags, styles, coordinates, and plate assignments.
