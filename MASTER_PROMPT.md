# MASTER ARCHITECTURE & CONTEXT DIRECTIVE: Loading Prints Agent (LP Agent)
**Project Name:** `LP Agent` (Loading Prints Agent)  
**Workspace Location:** `C:\Users\21824341\Desktop\Dev Projects\LP Agent`  
**Author / PM:** Ntobeko Basil Mthethwa | Lead Production CAD Automation Architect  
**Hardware Target:** Flashforge WaxJet 51C (Multi-Jet Wax 3D Production Printer)  
**Primary Engine:** Autodesk Netfabb 2027 Headless Console (`netfabb_console.exe` + Lua Automation)  
**Upstream Ecosystem Collaborators:**
- `jewellery-cad-agent` (`C:\Users\21824341\Desktop\Dev Projects\jewellery-cad-agent`) — Order & Library Brain
- `CAD Agent` (`C:\Users\21824341\Desktop\Dev Projects\CAD Agent`) — CAD Conditioning & Netfabb Repair Hands

---

## 0. AGENT SYSTEM PROMPT (Context Engineering Layer)

> **MANDATORY DIRECTIVE: Copy this entire block verbatim as the root system prompt when delegating to any AI coding agent, subagent, or CLI session working in this repository.**

```xml
<AGENT_DIRECTIVE>
  <ROLE>
    You are the Lead Automation Engineer and Spatial Systems Architect for the Loading Prints Agent ("LP Agent").
    You specialize in computational geometry, 2D/3D nesting algorithms, collision detection, and headless Autodesk Netfabb 2027 automation for fine jewelry manufacturing on the Flashforge WaxJet 51C.
  </ROLE>

  <PRIMARY_MISSION>
    Automate the intake of repaired production STL files from today's factory printing date folder, execute mathematically verified 2D nesting (flat layouts) or 3D packing (high-density volumetric packing) using headless Netfabb, ensure parts NEVER touch or interlock, and output ready-to-print build plates with complete traceability.
  </PRIMARY_MISSION>

  <CORE_TRIUMVIRATE_COLLABORATION>
    The production line consists of three specialized agents working in sequence:
    1. jewellery-cad-agent (The Brain): Resolves SEA.Net job bags, identifies style codes, finger sizes, metal alloys, flow gates (WFG vs NFG), and stages files to Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\.
    2. CAD Agent (The Hands): Automates Rhino/MatrixGold, places bag numbers and style text on sprues, validates mesh topology, runs Netfabb Extended Repair, and exports watertight "* (repaired).stl" files.
    3. LP Agent (The Loader - YOU): Ingests the repaired STLs from the date folder, calculates optimal 2D/3D nesting, enforces collision buffers and entanglement prevention, splits overflow into numbered plates, and compiles final build plates (merged plate STLs and .fabbproject files) for the WaxJet 51C.
  </CORE_TRIUMVIRATE_COLLABORATION>

  <HARDWARE_SPECIFICATIONS_WAXJET_51C>
    - Technology: Multi-Jet Printing (MJP) with 100% Real Wax (Part Wax + Soluble Support Wax).
    - Usable Build Envelope: 235.00 mm (X - Length) x 138.00 mm (Y - Width) x 100.00 mm (Z - Height).
    - Resolution / Layer Thickness: 0.032 mm (32 microns).
    - Mechanical Principle: Print time is governed strictly by the Z-height. A 15 mm tall plate prints in ~3.5 hours; a 90 mm tall plate takes ~16 hours regardless of XY part count.
  </HARDWARE_SPECIFICATIONS_WAXJET_51C>

  <NESTING_DISCIPLINE_RULES>
    1. 2D NESTING (Flat Platform Layout):
       - Default strategy for mixed daily production bags (rings, earrings, pendants, multi-part components PP1..PPn).
       - Parts are laid flat across the build plate with Z-axis fixed at the base platform clearance (Z = 0.5 mm).
       - Minimizes vertical build height (drastically cutting WaxJet print time) and prevents support wax entrapment inside ring shanks.
    2. 3D NESTING / PACKING (Volumetric Space Optimization):
       - Activated when loading batches of identical designs, high volumes of repeat styles (RPM), or when daily order volume exceeds 2D plate area.
       - Arranges models throughout the 3D volume (X, Y, Z) while maintaining strict part-to-part clearance.
    3. CLEARANCE & COLLISION PREVENTIONS (Non-Negotiable Guardrails):
       - Collision Detection (Interference Checking): Continuous mathematical verification that no two meshes intersect.
       - Clearance Buffer (Padding / Outset): Mandatory 2.0 mm (minimum 1.5 mm) safety envelope around each part, treated as a solid obstacle during nesting.
       - Interlocking / Entanglement Prevention: Mandatory topological checking to prevent ring shanks, loops, or bails from hooking through each other, even when surfaces do not touch.
       - Bounding Box Culling: Hierarchical Axis-Aligned Bounding Box (AABB) and Oriented Bounding Box (OBB) calculations for high-speed spatial partitioning before exact mesh intersection checks.
    4. BUILD PLATE SIZE BUDGET & MESH OPTIMIZATION (Safe Production Guideline):
       - Target merged build plate STL size should typically be between 150 MB and 600 MB (avoid un-optimized multi-gigabyte plates like 3.9 GB).
       - Note: This is an ADVISORY SAFE GUIDELINE to protect WaxJetPrint from out-of-memory and OpenGL driver crashes; it does NOT restrict agents from processing valid plates or compromise fine jewelry quality.
       - In MatrixGold / Rhino or Netfabb: Run mesh reduction / decimation on non-critical geometry (smooth shanks, sprues, planar surfaces) aiming for 70% to 90% facet reduction.
       - Crucial: Never degrade micro-prongs, milgrain, or fine stone settings. Keeping typical plates under 500 MB preserves 100% casting quality while reducing WaxJetPrint RAM usage from 45 GB down to ~6–8 GB.
  </NESTING_DISCIPLINE_RULES>

  <ENGINEERING_CONSTRAINTS>
    - Do NOT over-engineer: Prefer straightforward, maintainable Python 3 standard library, robust subprocess control of Netfabb Console, and clean JSON manifests over heavyweight external dependencies.
    - Legacy System Resilience: The system must run unattended, recover cleanly from malformed meshes or missing directories, quarantine damaged files without halting the entire plate, and support deterministic dry-runs.
    - Idempotency & Immutability: Re-running the agent on the same date folder must produce identical, deterministic results without overwriting master archives or creating duplicate plates.
  </ENGINEERING_CONSTRAINTS>
</AGENT_DIRECTIVE>
```

---

## 1. THE PRODUCTION ECOSYSTEM & 3-AGENT ARCHITECTURE

The Browns Jewellery manufacturing automation ecosystem is structured into three autonomous yet tightly integrated agents:

```
+---------------------------------------------------------------------------------------+
|                                    SEA.Net ERP                                        |
|                          (Production Job Bags / Barcodes)                             |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
|                             AGENT 1: jewellery-cad-agent                              |
|                              (Order & Library Brain)                                  |
|  - Ingests job bags (PDF / Barcode tokens)                                            |
|  - Queries Q:\2026 library and cad_index.sqlite (189,626 CAD files)                   |
|  - Resolves Design, Metal (WFG vs NFG), Finger Size (D-Z), Multi-parts (PP1..PPn)     |
|  - Emits stage manifests & activates CAD Agent via REST API or Q: queue               |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
|                                  AGENT 2: CAD Agent                                   |
|                          (CAD Conditioning & Repair Engine)                           |
|  - Opens 3DM master files in Rhino 7/8 + MatrixGold 3                                 |
|  - Dynamic text: stamps Bag # and Style # onto casting sprues                         |
|  - Applies object-space rotation for optimal WaxJet build orientation                 |
|  - Exports binary STL & runs Netfabb 2027 Extended Repair                             |
|  - Writes watertight files: Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\* (repaired).stl    |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+=======================================================================================+
|                             AGENT 3: LP Agent (THIS SYSTEM)                           |
|                             (Loading Prints / Plate Nesting)                          |
|  - Scans today's date folder: Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\                 |
|  - Filters for valid, watertight "* (repaired).stl" files                             |
|  - Analyzes geometry: decides 2D Flat Nesting vs 3D Packing                           |
|  - Executes headless Autodesk Netfabb 2027 via Lua automation                         |
|  - Enforces: Collision Detection, 2.0mm Clearance Buffer, Entanglement Prevention     |
|  - Splits overflow across multiple plates (Plate 1, Plate 2, ...)                     |
|  - Generates final merged plate STLs, .fabbproject files, and plate_manifest.json     |
+=======================================================================================+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
|                               Flashforge WaxJet 51C                                   |
|                             (Production 3D Print Run)                                 |
+---------------------------------------------------------------------------------------+
```

---

## 2. HARDWARE DEEP DIVE: FLASHFORGE WAXJET 51C

### 2.1 Technical Specifications & Environment
- **Machine Model:** Flashforge WaxJet 51C
- **Build Platform Envelope:** $235.00\text{ mm (X)} \times 138.00\text{ mm (Y)} \times 100.00\text{ mm (Z)}$
- **Platform Area:** $32,430\text{ mm}^2$ ($324.3\text{ cm}^2$)
- **Material System:**
  - Part Material: FFD-100 Purple Investment Casting Wax (100% meltable, zero ash content).
  - Support Material: FFS-200 White Soluble Wax (dissolves completely in heated solvent bath).
- **Print Resolution:**
  - XY Resolution: $1200 \times 1200\text{ DPI}$
  - Z Layer Thickness: $0.032\text{ mm}$ (32 microns)

### 2.2 Factory Operating Reality & Economics
1. **Print Time is Z-Height Dominant:**  
   The print carriage traverses the full XY platform on every pass. Adding 50 more rings across an existing XY layer adds almost **zero** additional print time. However, increasing the Z-height from $15\text{ mm}$ to $60\text{ mm}$ quadruples the print duration (from $\sim 3.5\text{ hours}$ to $\sim 14\text{ hours}$).
2. **Support Dissolution & Post-Processing:**  
   When parts are nested in 3D (stacked vertically), support wax completely encases lower pieces. In fine jewelry with delicate prongs, pavé collets, and micro-claw baskets, trapped support wax increases washing time and risks prong breakage during ultrasonic cleanout.
3. **Daily Mixed Orders vs. Repeat Production (RPM):**  
   - **Daily Mixed Orders (Bespoke / DCR / Collection):** Vary in height, weight, and geometry. These **must be 2D nested flat** on the build platform to finish within a standard shift ($\le 4\text{ hours}$).
   - **Repeat Production Batches (RPM):** Hundreds of identical settings, eternity bands, or solitaire heads. These are packed using **3D Packing** to maximize platform yield on overnight weekend runs.

---

## 3. MATHEMATICAL & ALGORITHMIC NESTING CORE

### 3.1 Terminology & Code Logic

```
   +---------------------------------------------------------------------+
   |                      Clearance Buffer (2.0 mm)                      |
   |        +---------------------------------------------------+        |
   |        |             Bounding Box (AABB / OBB)             |        |
   |        |        +---------------------------------+        |        |
   |        |        |                                 |        |        |
   |        |        |       Exact Jewelry Mesh        |        |        |
   |        |        |        (Ring / Setting)         |        |        |
   |        |        |                                 |        |        |
   |        |        +---------------------------------+        |        |
   |        +---------------------------------------------------+        |
   +---------------------------------------------------------------------+
```

#### 1. Collision Detection (Interference Checking)
- **Definition:** The continuous algorithmic calculation to verify that no two polygonal meshes intersect in space:
  $$\text{Mesh}_A \cap \text{Mesh}_B = \emptyset$$
- **Netfabb Implementation:** Calculated via voxelization and octree ray-intersection. The solver rejects any candidate position where voxel indices overlap.

#### 2. Clearance Buffer (Padding / Outset)
- **Definition:** An intentional invisible 3D safety offset added symmetrically around each model geometry:
  $$\Omega_{\text{buffered}} = \{ p \in \mathbb{R}^3 \mid \min_{q \in \text{Mesh}} \|p - q\| \le d_{\text{clearance}} \}$$
- **Default Production Setting:** $d_{\text{clearance}} = 2.0\text{ mm}$ (Absolute minimum: $1.5\text{ mm}$).
- **Why It Matters:** Wax patterns swell slightly during curing and require physical separation to allow solvent circulation during support wash. If pieces are closer than $1.5\text{ mm}$, support wax bridges the gap, fusing pieces together.

#### 3. Interlocking / Entanglement Prevention
- **Definition:** Algorithmic topological checking that prevents concave or toroidal geometries (such as ring shanks, hoop earrings, pendant bails, or chain links) from being nested through the hole or cavity of another part.
- **The Jewelry Trap:** Two independent ring bands can be placed such that their mesh surfaces are $2.0\text{ mm}$ apart, but Band 1 loops through the center hole of Band 2 (like links in a chain). Once printed in wax and cast in gold, they are permanently entangled and ruined.
- **Enforcement:** In Netfabb Lua, `packer.avoid_interlocking = true` enforces topological exclusion zones inside internal ring voids.

#### 4. Bounding Boxes (AABB vs. OBB)
- **AABB (Axis-Aligned Bounding Box):** Min/max bounds aligned strictly to the global world axes ($X, Y, Z$). Extremely fast to compute ($O(1)$ overlap checks), used for initial spatial culling.
- **OBB (Oriented Bounding Box):** Tightest enclosing box rotated along the principal inertia axes of the jewelry piece. Reduces false-positive bounding volume by up to $60\%$ for angled ring shanks.

---

### 3.2 Strategy Decision Engine: 2D Nesting vs. 3D Packing

The LP Agent must automatically evaluate the batch composition and select the correct nesting mode:

```
                                [INCOMING STL BATCH]
                                         |
                                         v
                         Total Bounding Box Footprint <=
                         Platform Usable Area (with buffer)?
                                   /           \
                                 YES            NO
                                 /                \
                       [Force 2D Nesting]     Is Batch Homogeneous
                       (Z-height minimized)   (>= 70% same style)?
                                                 /           \
                                               YES            NO
                                               /                \
                                    [Enable 3D Packing]    [Split into Multiple]
                                    (Volumetric stacking)  [2D Plates (Plate 1, 2)]
```

| Parameter | 2D Flat Nesting | 3D Packing |
|---|---|---|
| **Z-Axis Freedom** | Locked at base ($Z = \text{base offset}$) | Free ($0 \le Z \le 100\text{ mm}$) |
| **Rotations Allowed** | Z-axis rotation only ($0^\circ, 45^\circ, 90^\circ, 180^\circ$) | Full compound 3D rotation ($X, Y, Z$) |
| **Part Spacing (XY)** | $\ge 2.0\text{ mm}$ | $\ge 2.0\text{ mm}$ |
| **Part Spacing (Z)** | N/A (single tier) | $\ge 3.0\text{ mm}$ (vertical clearance) |
| **Platform Margin** | $3.0\text{ mm}$ from platform edges | $3.0\text{ mm}$ from platform edges |
| **Primary Use Case** | Daily mixed orders (Bespoke / DCR / Collection) | Mass repeat production (RPM / Style batches) |
| **Print Duration** | Fast ($2.5\text{ to }4.5\text{ hours}$) | Long ($8\text{ to }16\text{ hours}$) |

---

## 4. AUTODESK NETFABB 2027 HEADLESS AUTOMATION PROTOCOL

### 4.1 CLI Execution Architecture
Headless Netfabb is driven via `netfabb_console.exe`:
```bat
"C:\Program Files\Autodesk\Netfabb 2027\netfabb_console.exe" -l "C:\path\to\generated_nesting_script.lua"
```

### 4.2 Master Production Lua Script Template (`waxjet_nest.lua`)

The LP Agent dynamically generates this self-contained, robust Lua script and executes it headless:

```lua
-- ==============================================================================
-- Autodesk Netfabb 2027 Headless Nesting Script for Flashforge WaxJet 51C
-- Generated automatically by LP Agent (Loading Prints Agent)
-- ==============================================================================

system:setloggingtooglwindow(false)
system:logtofile([[{LOG_FILE_PATH}]])

local MACHINE_X = {PLATFORM_X}  -- 235.0 mm
local MACHINE_Y = {PLATFORM_Y}  -- 138.0 mm
local MACHINE_Z = {PLATFORM_Z}  -- 100.0 mm
local CLEARANCE = {CLEARANCE}    -- 2.0 mm
local BORDER_XY = {BORDER_XY}    -- 3.0 mm
local BORDER_Z  = {BORDER_Z}     -- 0.5 mm
local VOXEL_RES = {VOXEL_RES}    -- 0.75 mm
local IS_2D     = {IS_2D_MODE}   -- true for 2D flat, false for 3D packing

-- 1. Initialize Tray and Platform Root
local root = tray.root
tray.machinesize_x = MACHINE_X
tray.machinesize_y = MACHINE_Y
tray.machinesize_z = MACHINE_Z

-- 2. Ingest Repaired STL Files
local stl_files = {
{STL_FILES_ARRAY}
}

local loaded_count = 0
for i, file_path in ipairs(stl_files) do
    local mesh_file = nil
    if system.loadstl then
        mesh_file = system:loadstl(file_path)
    elseif system.loadmesh then
        mesh_file = system:loadmesh(file_path)
    end

    if mesh_file ~= nil then
        local part_name = string.match(file_path, "([^/\\]+)%.%w+$") or ("part_" .. i)
        local tm = root:addmesh(mesh_file, part_name)
        loaded_count = loaded_count + 1
    else
        system:log("ERROR: Failed to load mesh: " .. file_path)
    end
end

system:log("Loaded " .. tostring(loaded_count) .. " parts into tray.")

if loaded_count == 0 then
    system:log("FATAL: No parts loaded. Exiting.")
    if os and os.exit then os.exit(2) else error("No parts loaded") end
end

-- 3. Initialize TrueShape Packer
local packer = tray:createpacker(tray.packingid_trueshape)

packer.showprogress = false
packer.packing_2d = IS_2D
packer.packing_use_shadow_2d = IS_2D
packer.borderspacingxy = BORDER_XY
packer.borderspacingz = BORDER_Z
packer.minimaldistance = CLEARANCE
packer.voxel_size = VOXEL_RES

-- Critical Jewelry Protection: Never allow interlocking ring bands
packer.avoid_interlocking = true

if IS_2D then
    -- 2D Flat Nesting: Restrict rotations to planar Z increments (no flipping)
    packer.rotation_x = 0
    packer.rotation_y = 0
    packer.rotation_z = 45 -- 45-degree planar search steps
    packer.rotation_use_compound = false
    packer:setplacementpriorities(
        packer.minimum_build_height,
        packer.maximum_contact_area,
        packer.minimum_buildbox_volume
    )
else
    -- 3D Packing: Allow 3D orientations while preserving gravity stability
    packer.rotation_x = 90
    packer.rotation_y = 90
    packer.rotation_z = 45
    packer.rotation_use_compound = true
    packer:setplacementpriorities(
        packer.minimum_buildbox_volume,
        packer.minimum_build_height,
        packer.maximum_box_overlap
    )
end

-- 4. Execute Packing Algorithm
system:log("Starting TrueShape nesting calculation...")
local errorcode = packer:pack()
system:log("Packing algorithm completed with return code: " .. tostring(errorcode))

-- 5. Audit Plate Allocation and Leftovers
local packed_parts = {}
local leftover_parts = {}

for idx = 0, root.meshcount - 1 do
    local tm = root:getmesh(idx)
    if tm.packingstate == packer.isPacked then
        table.insert(packed_parts, tm)
    else
        table.insert(leftover_parts, tm)
    end
end

system:log("Nesting Results: " .. #packed_parts .. " packed, " .. #leftover_parts .. " leftover.")

-- 6. Export Merged Plate STL and Netfabb Project
if #packed_parts > 0 then
    -- Construct unified merged mesh for WaxJet slicer
    local master_mesh = nil
    for _, tm in ipairs(packed_parts) do
        local part_mesh = tm.mesh:dupe()
        part_mesh:applymatrix(tm.matrix)
        if master_mesh == nil then
            master_mesh = part_mesh
        else
            master_mesh:merge(part_mesh)
        end
    end

    if master_mesh ~= nil then
        master_mesh:unify(0.01)
        master_mesh:savetostl([[{MERGED_PLATE_STL_PATH}]])
        system:log("Merged build plate STL successfully written to: " .. [[{MERGED_PLATE_STL_PATH}]])
    end

    -- Save native Netfabb Project (.fabbproject)
    if application and application.savefabbproject then
        application:savefabbproject([[{FABBPROJECT_PATH}]])
        system:log("Netfabb project saved to: " .. [[{FABBPROJECT_PATH}]])
    end
end

-- 7. Write Structured JSON Audit for LP Agent Python Orchestrator
local audit_file = io.open([[{AUDIT_JSON_PATH}]], "w")
if audit_file ~= nil then
    audit_file:write("{\n")
    audit_file:write('  "exit_code": ' .. tostring(errorcode) .. ',\n')
    audit_file:write('  "packed_count": ' .. tostring(#packed_parts) .. ',\n')
    audit_file:write('  "leftover_count": ' .. tostring(#leftover_parts) .. ',\n')
    audit_file:write('  "packed_files": [\n')
    for i, tm in ipairs(packed_parts) do
        local comma = (i < #packed_parts) and "," or ""
        audit_file:write('    "' .. tm.name .. '"' .. comma .. '\n')
    end
    audit_file:write('  ],\n')
    audit_file:write('  "leftover_files": [\n')
    for i, tm in ipairs(leftover_parts) do
        local comma = (i < #leftover_parts) and "," or ""
        audit_file:write('    "' .. tm.name .. '"' .. comma .. '\n')
    end
    audit_file:write('  ]\n')
    audit_file:write("}\n")
    audit_file:close()
end

system:log("LP Agent Netfabb routine completed successfully.")
```

---

## 5. CODEBASE ARCHITECTURE (LP AGENT)

The repository follows a clean, single-responsibility, legacy-ready architecture:

```
LP Agent/
├── .github/
│   └── workflows/
│       └── ci.yml                      # Automated test & syntax validation pipeline
├── config/
│   ├── __init__.py
│   └── lp_config.py                   # Central dataclass: machine dims, buffers, paths
├── core/
│   ├── __init__.py
│   ├── date_scanner.py                # Ingests today's repaired STLs from date folder
│   ├── strategy_selector.py           # 2D Flat vs 3D Packing decision engine
│   ├── netfabb_runner.py              # Headless Netfabb console process supervisor
│   ├── collision_checker.py           # Independent post-nesting collision validator
│   └── plate_distributor.py           # Multi-plate overflow splitter (Plate 1, 2, ...)
├── pipeline/
│   ├── __init__.py
│   ├── orchestrator.py                # End-to-end execution runner & stage coordinator
│   └── stage_result.py                # Structured execution manifests & status records
├── manifest/
│   ├── __init__.py
│   └── manifest_writer.py             # Generates plate_manifest.json & factory work-order
├── utils/
│   ├── __init__.py
│   ├── logger.py                      # Console + rotating disk logger
│   └── path_guards.py                 # Traversal security & safe date path formatting
├── tests/
│   ├── conftest.py                    # Mock fixtures for Netfabb & test STLs
│   ├── test_date_scanner.py
│   ├── test_strategy_selector.py
│   ├── test_netfabb_runner.py
│   └── test_plate_distributor.py
├── agent_entry.py                     # Root CLI entry point
├── AGENTS.md                          # Operating instructions & subagent specifications
├── CLAUDE.md                          # Process guardrails, dev loop, test gates
└── README.md                          # Setup, hardware guide, and operator runbook
```

---

## 6. LEGACY RELIABILITY, FAILURE HANDLING & RESILIENCE

To ensure the system functions reliably for years with zero manual intervention, the code adheres to these failure handling rules:

### 6.1 The Non-Negotiable Guardrails (MUST / NEVER)

| Rule # | Guardrail | Enforcement Point |
|---|---|---|
| **LP-G1** | **NEVER** place two parts within $< 1.5\text{ mm}$ of each other. Target is strictly $2.0\text{ mm}$. | Pre-nesting Lua buffer + `collision_checker.py` post-validation. |
| **LP-G2** | **NEVER** drop or ignore leftover parts that do not fit on Plate 1. | `plate_distributor.py` must allocate leftovers to Plate 2, Plate 3, etc. |
| **LP-G3** | **NEVER** ingest an un-repaired STL if a `* (repaired).stl` exists for that design/component. | `date_scanner.py` strict provenance filtering. |
| **LP-G4** | **NEVER** crash the entire print run because a single STL is corrupted or oversized. | Corrupt/oversized files are quarantined to `exceptions/`; remaining files pack cleanly. |
| **LP-G5** | **MUST** verify output files hit physical disk before reporting success (No ghost files). | `os.path.exists()` and `os.path.getsize() > 1024` check. |
| **LP-G6** | **MUST** support dry-run mode (`--dry-run`) with zero file modifications. | Handled in `agent_entry.py` and `orchestrator.py`. |

### 6.2 Failure Mode Recovery Matrix

```
+------------------------------------+---------------------------------------------------------------+
| Failure Mode                       | Automated Recovery Action                                     |
+------------------------------------+---------------------------------------------------------------+
| Date folder empty or missing       | Log warning, output empty manifest status, exit with code 0.  |
| Mesh corrupted / unreadable        | Quarantine to exceptions/<file>, alert PM, pack valid files.  |
| Single part exceeds platform bounds| Move to oversized_exceptions/, alert PM, continue run.       |
| Netfabb console hangs (> 180s)     | Process watchdog terminates process, retries with voxel=1.0mm.|
| Plate exceeds area capacity        | Leftovers routed automatically to Plate 2, Plate 3.          |
| Interlocking detected              | Re-orient offending ring by 90-deg pitch or separate to next. |
+------------------------------------+---------------------------------------------------------------+
```

---

## 7. CLI INTERFACE & OPERATOR COMMANDS

```bat
:: Standard Daily Production Run (Automatically finds today's date folder on Q:)
python agent_entry.py load

:: Explicit Date Run
python agent_entry.py load --date 06.09.2026

:: Force Specific Nesting Mode
python agent_entry.py load --mode 2d          REM Force 2D Flat layout (shift print)
python agent_entry.py load --mode 3d          REM Force 3D Packing (overnight RPM)

:: Custom Clearance & Platform Margins
python agent_entry.py load --clearance 2.5 --border 4.0

:: Dry-Run (Preview packing count & plate distribution without writing merged STLs)
python agent_entry.py load --dry-run

:: Validate Existing Plate for Collisions & Distance Compliance
python agent_entry.py verify --plate "Q:\Printing\September\06.09.2026\Agent\merged_plate_01.stl"
```

---

## 8. SUMMARY OUTPUT DELIVERABLES (PER PRINT RUN)

When LP Agent completes a run for date `DD.MM.YYYY`, the output directory contains:

```
Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\
├── Plate_01\
│   ├── merged_plate_01.stl             # Slicer-ready unified build plate (Flashforge format)
│   ├── plate_01.fabbproject            # Autodesk Netfabb master scene with individual parts
│   └── plate_01_summary.json           # Part count, coordinates, bounding box list, density
├── Plate_02\ (if overflow occurs)
│   ├── merged_plate_02.stl
│   └── plate_02.fabbproject
├── exceptions\ (if any invalid files encountered)
│   └── exception_report.json           # Detailed diagnostic on quarantined files
└── plate_manifest.json                 # Master audit record linking SEA.Net bag tokens to plates
```
