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
- **3D Packing**: Column stacking for mass repeat production (RPM). Each file's copies form **one vertical stack** at a single XY position, at the model's own angle, with the tier gap between them — like blocks. A column grows until it reaches the height limit, then a new one starts alongside it.
- **Collision Detection & Interference Checking**: Continuous mathematical boundary calculation preventing mesh intersections.
- **Clearance Buffer (Padding)**: Solid $2.0\text{ mm}$ safety zone around each model by default, adjustable down to a $0.3\text{ mm}$ floor for dense small-part plates. $1.5\text{ mm}$ remains the guideline for prong-safe wax washing.
- **Interlocking & Entanglement Prevention**: Topological checking ensuring ring shanks and loops never hook through each other.
- **Multi-Plate Overflow Handling**: Leftover parts automatically populate sequential plates (Plate 1, Plate 2, ...).
- **Adjustable Plate Spacing**: Part-to-part gap (0.3–20 mm) and platform edge margin are operator-controlled from the UI (or `--clearance` on the CLI). The viewport re-estimates the fit live, so you can see how many plates a spacing choice costs before committing to a run. Sentry verifies against the spacing you chose, so a deliberately dense plate is not failed by a fixed rule.
- **Filename Quantity Multipliers**: A trailing `xN` means "put N of this on the plate".
- **Native Fallback Packer**: If Netfabb is unavailable or exceeds its time budget, LP Agent shelf-packs and merges the plate in-process and still writes a real `merged_plate_XX.stl`.

---

## Filename Quantity Suffixes

A trailing `xN` on a print file multiplies it onto the plate. The multiplier may sit
before or after the `(repaired)` tags, with or without a space:

| Filename | Parts placed |
|---|---|
| `6104-I-1.50mm-NFG-219956-PP x2 (repaired).stl` | 2 |
| `MC9841-0.50ct Vintage Cushion-219962-PP1 X2.stl` | 2 |
| `9500-I-NFG-PP1 (repaired) (repaired) X16.stl` | 16 |
| `10093-Arrow-PP (repaired)x4.stl` | 4 |
| `Star of David-20mm-PPx2.stl` | 2 |

**Only the multiplier at the end of the name counts.** Jewellery filenames are full of
`x` characters that are stone dimensions, and multiplying by one of those would flood a
plate with parts nobody ordered:

| Filename | Parts placed | Why |
|---|---|---|
| `9455-R-3.30mmx7 Protea Eternity-...-PP (repaired).stl` | 1 | `x7` is a stone count, mid-name |
| `6104-Q-(14X1.5mm)-v1-219936-PP (repaired).stl` | 1 | `14X1.5mm` is a dimension |
| `MC9461-10x7mm-Tanzanite-215288-PP1 (repaired).stl` | 1 | `10x7mm` is a stone size |
| `9877-GA-PP (repaired) x7 - Copy.stl` | 1 | `- Copy` is a Windows duplicate, not an order |

Each copy is packed, verified and reported independently as `name [2 of 3].stl`, so if only
part of a set fits, the remainder spills onto the next plate rather than being lost.
Quantities are capped at 500 per file. The UI shows a `×N` badge per file and a
`4 files → 7 parts` summary before you pack.

A file asking for **3 or more** copies marks the batch as repeat production, which is what
routes it to 3D Packing in Auto mode.

---

## 3D Packing (repeat production)

For RPM runs — many units of one design — a file's copies are **stacked into a column**,
not spread across the plate:

```
9500-RING-PP X3.stl   ->  one column at one XY position
                            z = 22.5  ┌────────┐
                            z = 11.5  ├────────┤   3 mm tier gap between each
                            z =  0.5  └────────┘
```

A worked plate — four files, 48 parts:

| File | Units | Result |
|---|---|---|
| `9500-RING-PP X3` | 3 | 1 column of 3 |
| `A-RING-PP X3` | 3 | 1 column of 3 |
| `B-PENDANT-PP X2` | 2 | 1 column of 2 (12 mm part → z = 0.5, 15.5) |
| `C-HEAD-PP X40` | 40 | 8 columns of 5 |

→ 11 stacks, 52.5 mm tall, under the 60 mm cap, verified.

Behaviour worth knowing:

- **A column is bounded by the height limit, not the unit count.** An x40 of a 9 mm part
  under a 60 mm cap becomes 8 columns of 5, never one 480 mm tower.
- **The model keeps its own angle.** Every unit in a stack shares one orientation, and the
  part is only rotated 90° when it would not otherwise fit the plate at all — then the
  whole column turns together, so the stack still reads as one block.
- **One column holds one design.** Designs never share a stack, so a column can be lifted
  off and counted as a single order line.
- **A wider tier gap makes columns shorter, not the plate taller** — the cap bounds the
  column, so loosening the gap trades stack depth for plate area.
- **Print time tracks plate height.** The WaxJet carriage sweeps the whole platform every
  pass, so more columns are nearly free while more height is not.

### Which engine builds a 3D plate

The **layout is always LP Agent's**. Netfabb's 3D packer is a Monte Carlo search that picks
its own placements, so letting it pack would scatter a design's copies across a tier and
discard the stacking entirely.

Netfabb is still used to **build** the plate from those positions. LP Agent emits an
explicit placement script — no `packer:pack()` call — that loads each mesh, turns it if the
layout calls for it, and translates it onto its computed corner:

```lua
if p.rot == 1 then tm:rotate(0, 0, 1, math.pi / 2) end   -- radians, not degrees
local o = tm.outbox
tm:translate(p.x - o.minx, p.y - o.miny, p.z - o.minz)   -- measured, so exact
```

Translating from the box measured *after* rotating means a part lands on target regardless
of where Netfabb rotates about or where the mesh's own origin sits. Netfabb then merges and
writes the `.fabbproject`, which the native writer cannot produce.

| | Layout | Merge + export | `builder` |
|---|---|---|---|
| 3D, Netfabb present | LP Agent columns | Netfabb | `netfabb-placed` |
| 3D, Netfabb missing or failing | LP Agent columns | LP Agent | `lp-native` (no `.fabbproject`) |
| 2D | Netfabb TrueShape | Netfabb | `netfabb` |

Copies share a staged source mesh, so an x40 batch stages one file rather than forty.

### Spacing and height

| Control | Applies to | Default | Range |
|---|---|---|---|
| **Part gap** | XY, both modes | 2.0 mm | 0.3–20 mm |
| **Edge margin** | platform border | 3.0 mm | 0–20 mm |
| **Tier gap** | Z, 3D only | 3.0 mm | 0.3–20 mm |
| **Max plate height** | 3D only | **60 mm** | 20–100 mm (presets 60 / 75 / 90 / 100) |

### The height limit

The machine reaches 100 mm, but a tall stack is punishing to clean — support wax has to be
washed out from between every tier. Plates are therefore packed to **60 mm by default**,
with 90 mm as the outer safe setting and 100 mm the physical limit. The cap governs 3D
only; a 2D plate is a single tier and cannot approach it.

When respecting the cap costs an extra plate, that trade is **put to the operator rather
than decided silently** — per plate, because the right answer can differ between Plate_01
and Plate_02:

```
Plate_01 exceeds the height limit
  Respect the limit    19.5 mm   156 parts · 2 tiers · 44 move to the next plate
  Allow it taller      30.5 mm   200 parts · 3 tiers · 0 move to the next plate
```

Packing is bounding-box arithmetic (milliseconds) while merging the plate STL takes
minutes, so **both options are costed before any merging** — answering costs only the
thinking time. No answer within 15 minutes keeps the limit and splits the plate.

### Unattended runs

The CLI cannot prompt, so the policy is stated on the command line:

```bat
:: Respect the cap, accept more plates (default when the flag is absent)
python agent_entry.py load --mode 3d --max-height 60 --on-cap-exceeded split

:: Allow up to the machine limit to save a plate, with a warning
python agent_entry.py load --mode 3d --on-cap-exceeded exceed

:: Refuse to pack; run it from the UI where the choice can be made
python agent_entry.py load --mode 3d --on-cap-exceeded fail
```

The default is `split`: a run with nobody watching must not quietly make the cleaner's job
harder.

The tier gap is deliberately separate from the base standoff (`border_spacing_z`, 0.5 mm):
support wax fills the vertical gap and has to wash back out, so it is wider than the XY gap
by default (MASTER_PROMPT §3.2 specifies ≥ 3.0 mm). Sentry verifies the stacked layout in
all three axes.

---

## Plate Build Verification

A plate is only trustworthy if the merged STL actually holds geometry, so LP Agent reports
that separately from layout verification:

| Field | Meaning |
|---|---|
| `build.plate_built` | The merged STL on disk parses and contains triangles |
| `build.builder` | `netfabb` (TrueShape) or `lp-native` (fallback packer) |
| `build.merged_stl_triangles` / `merged_stl_bytes` | Evidence of what was written |
| `verification.passed` | Layout legality only — envelope and clearances |

`verification.passed` alone is **not** proof of a build. Earlier versions wrote a 49-byte
placeholder plate on Netfabb timeout and still reported `PASS`; the UI, the work order and
`plate_manifest.json` now all surface `plate_built`, and the UI marks an unbuilt plate
**⛔ NOT BUILT** with the reason.

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
Opens an interactive 3D platform at `http://localhost:4200` with:
- **Top command bar** — target folder, scan, strategy, pack and export all run across the top, leaving the sidebar full width for the parts list, spacing and plate status.
- **Live pack progress** — stage, percentage, elapsed time and estimated time remaining, so a multi-minute Netfabb run is visible rather than a spinner.
- **Bounding boxes** — every model's true AABB at its packed position, with hover readout of its exact X/Y/Z extents. Toggle solids on for volume, off for a clear view of the gaps.
- **Plate build status** — whether the plate was actually built, by which engine, with triangle count and file size.
- **Spacing controls** — part gap (0.3 mm floor) and edge margin sliders with presets and a live fit estimate; sub-1.5 mm gaps are flagged amber as a deliberate choice.
- **Quantity badges** — `×N` per file plus a `files → parts` summary, so a multiplier is visible before you commit to a run.
- Real-time collision telemetry, Netfabb integration, and plate switching.

The viewport distinguishes an **actual packed layout** from a **preview estimate** in the
bottom-left chip, so an estimate is never mistaken for what was packed.

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
