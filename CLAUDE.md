# CLAUDE.md — Loading Prints Agent (LP Agent) · Operating Manual

Operating manual for AI agents building and maintaining the **Loading Prints Agent (LP Agent)**.  
Ntobeko Basil Mthethwa is the PM and only reviewer; agents do the building.  
**This file owns process and guardrails.** Full architecture lives in [MASTER_PROMPT.md](file:///c:/Users/21824341/Desktop/Dev%20Projects/LP%20Agent/MASTER_PROMPT.md).

---

## 1. The Production Context

LP Agent is the 3rd pillar of the Browns Jewellery production automation line:
1. **jewellery-cad-agent**: Resolves SEA.Net job bags, identifies styles, finger sizes, flow gates (WFG vs NFG), and stages files to `Q:\Printing\<Month>\<DD.MM.YYYY>\Agent\`.
2. **CAD Agent**: Automates MatrixGold/Rhino, stamps sprues, validates meshes, runs Netfabb Extended Repair, outputs `* (repaired).stl`.
3. **LP Agent (This Project)**: Ingests repaired STLs from today's date folder, executes 2D Flat Nesting or 3D Packing via headless Autodesk Netfabb 2027 (`netfabb_console.exe`), ensures parts never touch or interlock, and outputs WaxJet 51C build plates (`merged_plate_XX.stl`, `.fabbproject`, `plate_manifest.json`).

---

## 2. Guardrails (MUST / NEVER)

| # | Rule | Enforced By |
|---|---|---|
| **LP-G1** | **NEVER** allow parts to touch or violate clearance buffer ($\ge 2.0\text{ mm}$ target, $\ge 1.5\text{ mm}$ minimum). | `netfabb_runner.py` (Lua buffer) + `collision_checker.py` (post-check). |
| **LP-G2** | **NEVER** drop or skip leftover parts when a plate fills up — split overflow into Plate 2, Plate 3. | `plate_distributor.py` multi-plate allocation loop. |
| **LP-G3** | **NEVER** load un-repaired STLs if a `* (repaired).stl` exists for that design or component. | `date_scanner.py` provenance matcher. |
| **LP-G4** | **NEVER** crash or halt the entire plate because a single mesh is corrupted or oversized. | Exception quarantine (`exceptions/` + `exception_report.json`). |
| **LP-G5** | **NEVER** modify, rename, or delete raw files inside `Q:\2026` or date folders. Read-only input; write only to `Plate_XX/` subfolders. | `path_guards.py` runtime path assertions. |
| **LP-G6** | **MUST** enable `avoid_interlocking = true` in Netfabb to prevent ring shanks and hoops from chaining together. | `netfabb_runner.py` Lua configuration. |
| **LP-G7** | **MUST** be green on `ruff check .` and `pytest -q` before opening PR or finishing tasks. | CI & local preflight verification. |
| **LP-G8** | **MUST** support `--dry-run` to preview packing and plate splits without writing files. | CLI runner `agent_entry.py`. |
| **LP-G9** | **MUST** verify output files exist and are non-empty on disk before reporting success. | `manifest_writer.py` and `orchestrator.py`. |
| **LP-G10** | **MUST** monitor build plate sizes against the safe guideline budget (150 MB – 600 MB typical, warn on >1GB). Encourage pre-merge mesh optimization while strictly preserving 100% of fine jewelry details without restricting agents. | `config/lp_config.py` + `native_packer.py` / `netfabb_runner.py` |

---

## 3. The Development Loop

1. **Intake**: Review the goal, affected components, and real date folder test fixtures.
2. **Tests First**: Write unit tests for new behavior citing the rule or failure case before writing implementation.
3. **Small Commits**: One concern per commit: `feat(scope): why` or `fix(scope): why`.
4. **Local Verification**:
   ```bat
   ruff check .
   pytest -q
   python agent_entry.py load --dry-run
   ```
5. **No Blind Overwrites**: Check existing code, maintain legacy compatibility, keep dependencies standard (Python 3.10+ stdlib + Netfabb 2027 console).

---

## 4. Key Netfabb 2027 Parameters for Flashforge WaxJet 51C

- **Platform Size**: $235.00\text{ mm (X)} \times 138.00\text{ mm (Y)} \times 100.00\text{ mm (Z)}$
- **Platform Border Spacing**: $XY = 3.0\text{ mm}$, $Z = 0.5\text{ mm}$
- **Part-to-Part Spacing**: $2.0\text{ mm}$ (clearance buffer)
- **Voxel Size**: $0.75\text{ mm}$ (fine jewelry detail balance)
- **Avoid Interlocking**: `true` (ring shanks and chain loops cannot hook)
- **2D Mode**: `packer.packing_2d = true`, Z-rotation only ($45^\circ$ steps)
- **3D Mode**: `packer.packing_2d = false`, compound $X, Y, Z$ rotations ($45^\circ / 90^\circ$ steps)
