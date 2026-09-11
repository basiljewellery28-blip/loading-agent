/* ═══════════════════════════════════════════════════════════════
   LP Agent — Application Logic & Three.js 3D Viewport
   ═══════════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ── WaxJet 51C Platform Constants ──────────────────────────
  const PLATFORM = { x: 235, y: 138, z: 100 };

  // ── State ──────────────────────────────────────────────────
  const state = {
    currentDate: null,
    currentDirectory: null,
    scanResult: null,
    packResult: null,
    selectedMode: 'auto',
    activePlateIndex: 0,
    partMeshes: [],   // Three.js mesh objects on the platform
    // Operator-adjustable spacing. These drive both the on-screen preview and the
    // parameters sent to the packer, so what is previewed is what gets packed.
    clearance: 2.0,
    border: 3.0,
    layerGapZ: 3.0,
    maxPlateHeight: 60.0,
    machineMaxHeight: 100.0,
    minClearance: 0.3,
    // Viewport display toggles.
    showBBox: true,
    showSolid: false,
    // Whether the viewport is showing real packed positions or a local estimate.
    layoutSource: 'preview',
    packJobId: null,
    pollTimer: null,
  };

  // ── DOM References ─────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  const dateInput       = $('date-input');
  const targetFolderDisplay = $('target-folder-display');
  const btnToday        = $('btn-today');
  const btnScan         = $('btn-scan');
  const scanProgress    = $('scan-progress');
  const partsContainer  = $('parts-container');
  const scannerBadge    = $('scanner-badge');
  const quantitySummary = $('quantity-summary');

  const strategyToggle  = $('strategy-toggle');
  const strategyInfo    = $('strategy-info');

  const btnPack         = $('btn-pack');
  const btnOpenNetfabb  = $('btn-open-netfabb');
  const btnDownload     = $('btn-download');
  const btnManifest     = $('btn-manifest');

  const cardPlates      = $('card-plates');
  const plateTabs       = $('plate-tabs');
  const plateInfo       = $('plate-info');

  const sidebar         = $('sidebar');
  const sidebarWrap     = $('sidebar-wrap');
  const btnToggleAll    = $('btn-toggle-all');

  const statusNetfabb   = $('status-netfabb');
  const statusNetfabbTx = $('status-netfabb-text');
  const statusVersion   = $('status-version');

  const toastContainer  = $('toast-container');
  const manifestOverlay = $('manifest-overlay');
  const manifestContent = $('manifest-content');
  const manifestClose   = $('manifest-close');

  const infoParts       = $('info-parts');
  const infoSource      = $('info-source');
  const infoUsage       = $('info-usage');
  const tooltip3d       = $('tooltip-3d');
  const tooltipTitle    = $('tooltip-title');
  const tooltipDims     = $('tooltip-dims');
  const tooltipPos      = $('tooltip-pos');
  const tooltipBBox     = $('tooltip-bbox');

  const clearanceRange  = $('clearance-range');
  const clearanceValue  = $('clearance-value');
  const borderRange     = $('border-range');
  const borderValue     = $('border-value');
  const spacingBadge    = $('spacing-badge');
  const presetRow       = $('preset-row');
  const layerGapRange   = $('layer-gap-range');
  const layerGapValue   = $('layer-gap-value');
  const layerGapRow     = $('layer-gap-row');
  const maxHeightRange  = $('max-height-range');
  const maxHeightValue  = $('max-height-value');
  const maxHeightRow    = $('max-height-row');
  const maxHeightHint   = $('max-height-hint');
  const heightPresetRow = $('height-preset-row');

  const capOverlay      = $('cap-overlay');
  const capPlateName    = $('cap-plate-name');
  const capLead         = $('cap-lead');
  const capSplitHeight  = $('cap-split-height');
  const capSplitDetail  = $('cap-split-detail');
  const capExceedHeight = $('cap-exceed-height');
  const capExceedDetail = $('cap-exceed-detail');
  const capTimeoutNote  = $('cap-timeout-note');
  const fitFill         = $('fit-fill');
  const fitText         = $('fit-text');

  const packProgress    = $('pack-progress');
  const packStage       = $('pack-stage');
  const packPct         = $('pack-pct');
  const packFill        = $('pack-fill');
  const packElapsed     = $('pack-elapsed');
  const packEta         = $('pack-eta');

  const buildStatus     = $('build-status');
  const buildBadge      = $('build-badge');
  const buildEngine     = $('build-engine');
  const buildFacts      = $('build-facts');
  const buildReason     = $('build-reason');

  // ── Three.js Setup ─────────────────────────────────────────
  let scene, camera, renderer, controls, raycaster, mouse;
  let platformGroup, partsGroup, envelopeLines;

  function initThreeJS() {
    const canvas = $('viewport-canvas');
    const area = $('viewport-area');
    const cx = PLATFORM.x / 2;
    const cz = PLATFORM.y / 2;

    // Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x060a13);
    scene.fog = new THREE.FogExp2(0x060a13, 0.0015);

    // Camera
    const aspect = (area.clientWidth && area.clientHeight) ? area.clientWidth / area.clientHeight : 16 / 9;
    camera = new THREE.PerspectiveCamera(45, aspect, 0.1, 2000);
    camera.position.set(cx + 175, 170, cz + 185);

    // Renderer
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(area.clientWidth, area.clientHeight, false);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    // Controls
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(cx, 15, cz);
    controls.minDistance = 50;
    controls.maxDistance = 800;
    controls.maxPolarAngle = Math.PI * 0.48;
    controls.update();

    // Raycaster
    raycaster = new THREE.Raycaster();
    mouse = new THREE.Vector2(-999, -999);

    // Automatic container resize tracking
    if (window.ResizeObserver) {
      const resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect;
          if (width > 0 && height > 0 && camera && renderer) {
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
            renderer.setSize(width, height, false);
          }
        }
      });
      resizeObserver.observe(area);
    }

    // Lights
    const ambientLight = new THREE.AmbientLight(0x8899bb, 0.6);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(200, 300, 200);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.set(1024, 1024);
    dirLight.shadow.camera.left = -200;
    dirLight.shadow.camera.right = 200;
    dirLight.shadow.camera.top = 200;
    dirLight.shadow.camera.bottom = -200;
    scene.add(dirLight);

    const fillLight = new THREE.DirectionalLight(0x06b6d4, 0.25);
    fillLight.position.set(-150, 100, -100);
    scene.add(fillLight);

    // Groups
    platformGroup = new THREE.Group();
    partsGroup = new THREE.Group();
    scene.add(platformGroup);
    scene.add(partsGroup);

    // Build platform
    createPlatform();

    // Grid helper (subtle)
    const gridHelper = new THREE.GridHelper(500, 50, 0x1a2332, 0x111827);
    gridHelper.position.set(PLATFORM.x / 2, -3.1, PLATFORM.y / 2);
    scene.add(gridHelper);

    // Events
    window.addEventListener('resize', onResize);
    canvas.addEventListener('pointermove', onPointerMove);

    // Animate
    animate();
  }

  function rebuildPlatform() {
    // The printable-area outline tracks the edge-margin setting, so the operator can see
    // the usable area shrink as they widen it.
    while (platformGroup.children.length > 0) {
      const child = platformGroup.children[0];
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
      platformGroup.remove(child);
    }
    createPlatform();
  }

  function createPlatform() {
    // Base plate (metallic dark gray)
    const plateGeo = new THREE.BoxGeometry(PLATFORM.x, 3, PLATFORM.y);
    const plateMat = new THREE.MeshStandardMaterial({
      color: 0x2a3040,
      metalness: 0.7,
      roughness: 0.3,
    });
    const plate = new THREE.Mesh(plateGeo, plateMat);
    plate.position.set(PLATFORM.x / 2, -1.5, PLATFORM.y / 2);
    plate.receiveShadow = true;
    platformGroup.add(plate);

    // Printable area outline (cyan inner border)
    const BORDER = state.border;
    const innerX = Math.max(0, PLATFORM.x - 2 * BORDER);
    const innerY = Math.max(0, PLATFORM.y - 2 * BORDER);
    const borderPoints = [
      new THREE.Vector3(BORDER, 0.05, BORDER),
      new THREE.Vector3(BORDER + innerX, 0.05, BORDER),
      new THREE.Vector3(BORDER + innerX, 0.05, BORDER + innerY),
      new THREE.Vector3(BORDER, 0.05, BORDER + innerY),
      new THREE.Vector3(BORDER, 0.05, BORDER),
    ];
    const borderGeo = new THREE.BufferGeometry().setFromPoints(borderPoints);
    const borderMat = new THREE.LineBasicMaterial({ color: 0x06b6d4, transparent: true, opacity: 0.5 });
    platformGroup.add(new THREE.Line(borderGeo, borderMat));

    // Platform edge outline
    const edgePoints = [
      new THREE.Vector3(0, 0.05, 0),
      new THREE.Vector3(PLATFORM.x, 0.05, 0),
      new THREE.Vector3(PLATFORM.x, 0.05, PLATFORM.y),
      new THREE.Vector3(0, 0.05, PLATFORM.y),
      new THREE.Vector3(0, 0.05, 0),
    ];
    const edgeGeo = new THREE.BufferGeometry().setFromPoints(edgePoints);
    const edgeMat = new THREE.LineBasicMaterial({ color: 0x64748b, transparent: true, opacity: 0.4 });
    platformGroup.add(new THREE.Line(edgeGeo, edgeMat));

    // Build volume wireframe envelope
    const envelopeGeo = new THREE.BoxGeometry(PLATFORM.x, PLATFORM.z, PLATFORM.y);
    const envelopeMat = new THREE.LineBasicMaterial({ color: 0x1f2937, transparent: true, opacity: 0.25 });
    const envelopeEdges = new THREE.EdgesGeometry(envelopeGeo);
    envelopeLines = new THREE.LineSegments(envelopeEdges, envelopeMat);
    envelopeLines.position.set(PLATFORM.x / 2, PLATFORM.z / 2, PLATFORM.y / 2);
    platformGroup.add(envelopeLines);

    // Axis labels using tiny meshes
    addAxisLabel('X', PLATFORM.x + 8, 0, 0, 0x06b6d4);
    addAxisLabel('Y', 0, 0, PLATFORM.y + 8, 0x10b981);
    addAxisLabel('Z', 0, PLATFORM.z + 8, 0, 0xf59e0b);
  }

  function addAxisLabel(text, x, y, z, color) {
    // Tiny sphere as axis endpoint indicator
    const geo = new THREE.SphereGeometry(1.5, 8, 8);
    const mat = new THREE.MeshBasicMaterial({ color });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(x, y, z);
    platformGroup.add(mesh);
  }

  function onResize() {
    const area = $('viewport-area');
    if (!area || !camera || !renderer) return;
    const w = area.clientWidth;
    const h = area.clientHeight;
    if (w > 0 && h > 0) {
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    }
  }

  function onPointerMove(event) {
    const canvas = renderer.domElement;
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    // Raycast for part hover
    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(partsGroup.children, false);

    if (intersects.length > 0) {
      const obj = intersects[0].object;
      if (obj.userData && obj.userData.partName) {
        const d = obj.userData;
        tooltipTitle.textContent = d.partName;
        tooltipDims.textContent = `Size: ${d.sizeX} × ${d.sizeY} × ${d.sizeZ} mm`;
        tooltipPos.textContent =
          `Min: (${d.posX.toFixed(1)}, ${d.posY.toFixed(1)}, ${d.posZ.toFixed(1)}) mm`;
        tooltipBBox.textContent =
          `Box X:[${d.posX.toFixed(1)}–${d.maxX.toFixed(1)}] ` +
          `Y:[${d.posY.toFixed(1)}–${d.maxY.toFixed(1)}] ` +
          `Z:[${d.posZ.toFixed(1)}–${d.maxZ.toFixed(1)}]`;
        tooltip3d.style.left = (event.clientX - $('viewport-area').getBoundingClientRect().left + 16) + 'px';
        tooltip3d.style.top = (event.clientY - $('viewport-area').getBoundingClientRect().top - 16) + 'px';
        tooltip3d.classList.add('tooltip-3d--visible');

        // Highlight the hovered part's bounding box, so it reads in either display mode.
        partsGroup.children.forEach(m => {
          const hovered = m.userData && m.userData.partName === d.partName;
          if (m.material && m.material.emissive) {
            m.material.emissive.setHex(hovered ? 0x06b6d4 : 0x000000);
            m.material.emissiveIntensity = hovered ? 0.3 : 0;
          }
          if (m.userData && m.userData.isBBox) {
            m.material.opacity = hovered ? 1.0 : 0.95;
            m.material.linewidth = hovered ? 2 : 1;
          }
        });
        return;
      }
    }

    tooltip3d.classList.remove('tooltip-3d--visible');
    partsGroup.children.forEach(m => {
      if (m.material && m.material.emissive) {
        m.material.emissive.setHex(0x000000);
        m.material.emissiveIntensity = 0;
      }
      if (m.userData && m.userData.isBBox) m.material.opacity = 0.95;
    });
  }

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }

  // ── Camera Views ───────────────────────────────────────────
  function setCameraView(view) {
    const cx = PLATFORM.x / 2, cz = PLATFORM.y / 2;

    switch(view) {
      case 'iso':
        controls.target.set(cx, 15, cz);
        camera.position.set(cx + 175, 170, cz + 185);
        break;
      case 'top':
        controls.target.set(cx, 0, cz);
        camera.position.set(cx, 320, cz + 0.01);
        break;
      case 'front':
        controls.target.set(cx, 35, cz);
        camera.position.set(cx, 50, cz + 240);
        break;
    }
    controls.update();

    // Update active button
    ['view-iso', 'view-top', 'view-front'].forEach(id => {
      $(id).classList.toggle('btn--active', id === `view-${view}`);
    });
  }

  // ── Part Color Palette ─────────────────────────────────────
  const PART_COLORS = [
    0x06b6d4, // Cyan
    0xf59e0b, // Gold
    0x10b981, // Emerald
    0x8b5cf6, // Violet
    0xec4899, // Pink
    0xf97316, // Orange
    0x14b8a6, // Teal
    0x6366f1, // Indigo
  ];

  function getPartColor(index, total) {
    return PART_COLORS[index % PART_COLORS.length];
  }

  // ── 3D Part Rendering ──────────────────────────────────────
  function clearParts() {
    while (partsGroup.children.length > 0) {
      const child = partsGroup.children[0];
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
      partsGroup.remove(child);
    }
    state.partMeshes = [];
  }

  function addPartBox(name, x, y, z, sizeX, sizeY, sizeZ, colorIndex) {
    const color = getPartColor(colorIndex, 1);

    // Three.js is Y-up while the plate is Z-up, so plate Z maps to scene Y.
    const geo = new THREE.BoxGeometry(sizeX, sizeZ, sizeY);
    const mat = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.3,
      roughness: 0.6,
      transparent: true,
      opacity: 0.85,
    });

    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(x + sizeX / 2, z + sizeZ / 2, y + sizeY / 2);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    // Stays in the scene even in bounding-box-only mode: the raycaster skips invisible
    // objects, so hiding it outright would kill hover tooltips. Opacity does the hiding.
    applySolidVisibility(mesh);

    const userData = {
      partName: name,
      sizeX: sizeX.toFixed(2),
      sizeY: sizeY.toFixed(2),
      sizeZ: sizeZ.toFixed(2),
      posX: x,
      posY: y,
      posZ: z,
      maxX: x + sizeX,
      maxY: y + sizeY,
      maxZ: z + sizeZ,
      isSolid: true,
    };
    mesh.userData = userData;

    partsGroup.add(mesh);
    state.partMeshes.push(mesh);

    // Bounding box: a bright, always-legible wireframe of the part's true AABB. This is
    // the primary representation, because the packer reasons about these boxes -- seeing
    // them is how an operator checks that gaps and the envelope are respected.
    const bboxGeo = new THREE.BoxGeometry(sizeX, sizeZ, sizeY);
    const edgesGeo = new THREE.EdgesGeometry(bboxGeo);
    const edgesMat = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.95,
    });
    const edges = new THREE.LineSegments(edgesGeo, edgesMat);
    edges.position.copy(mesh.position);
    edges.visible = state.showBBox;
    edges.userData = { ...userData, isBBox: true };
    bboxGeo.dispose();
    partsGroup.add(edges);

    // Footprint shadow on the plate, so the XY packing reads clearly from the top view.
    const padPoints = [
      new THREE.Vector3(x, 0.06, y),
      new THREE.Vector3(x + sizeX, 0.06, y),
      new THREE.Vector3(x + sizeX, 0.06, y + sizeY),
      new THREE.Vector3(x, 0.06, y + sizeY),
      new THREE.Vector3(x, 0.06, y),
    ];
    const padGeo = new THREE.BufferGeometry().setFromPoints(padPoints);
    const padMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35 });
    const pad = new THREE.Line(padGeo, padMat);
    pad.visible = state.showBBox;
    pad.userData = { isFootprint: true };
    partsGroup.add(pad);
  }

  function applySolidVisibility(mesh) {
    mesh.material.opacity = state.showSolid ? 0.85 : 0.0;
    mesh.material.depthWrite = state.showSolid;
    mesh.castShadow = state.showSolid;
  }

  function applyDisplayToggles() {
    partsGroup.children.forEach(obj => {
      if (!obj.userData) return;
      if (obj.userData.isSolid) applySolidVisibility(obj);
      else if (obj.userData.isBBox || obj.userData.isFootprint) obj.visible = state.showBBox;
    });
    // With both layers off nothing is visible; keep bounding boxes as the floor.
    if (!state.showSolid && !state.showBBox) {
      state.showBBox = true;
      $('toggle-bbox').classList.add('btn--active');
      partsGroup.children.forEach(obj => {
        if (obj.userData && (obj.userData.isBBox || obj.userData.isFootprint)) obj.visible = true;
      });
    }
  }

  function updateUsageChip(boxes) {
    // Share of the usable plate area covered by part footprints.
    //
    // Measured per tier, not across the whole plate: on a 6-tier stack the summed
    // footprints are six plates' worth, which reads as a nonsensical "488% used".
    const usable = Math.max(1, (PLATFORM.x - 2 * state.border) * (PLATFORM.y - 2 * state.border));

    if (!boxes.length) {
      infoUsage.textContent = '▦ — % used';
      return;
    }

    // Group into columns by XY. Coverage is the floor area those columns occupy: every
    // unit above the first sits on the same footprint, so counting them all would report
    // the same square millimetres several times over.
    const columns = new Map();
    boxes.forEach(b => {
      const key = `${b.min_x.toFixed(2)},${b.min_y.toFixed(2)}`;
      if (!columns.has(key)) {
        columns.set(key, { area: (b.max_x - b.min_x) * (b.max_y - b.min_y), units: 0 });
      }
      columns.get(key).units += 1;
    });

    const floor = [...columns.values()].reduce((sum, c) => sum + c.area, 0);
    const tallest = Math.max(...[...columns.values()].map(c => c.units));
    const pct = (floor / usable * 100).toFixed(0);

    infoUsage.textContent = tallest > 1
      ? `▦ ${pct}% floor · ${columns.size} stacks up to ${tallest}`
      : `▦ ${pct}% used`;
  }

  function setLayoutSource(source) {
    state.layoutSource = source;
    const labels = {
      packed:  '✅ Actual packed layout',
      preview: '👁 Preview estimate (not packed yet)',
    };
    infoSource.textContent = labels[source] || labels.preview;
    infoSource.classList.toggle('viewport-info__chip--live', source === 'packed');
  }

  /**
   * Render the real packed positions reported by the packer.
   * `boxes` are absolute platform-space AABBs: {name, min_x, max_x, min_y, max_y, min_z, max_z}.
   */
  function renderPlacedBoxes(boxes) {
    clearParts();
    if (!boxes || !boxes.length) {
      infoParts.textContent = '🔩 0 parts';
      updateUsageChip([]);
      return;
    }

    boxes.forEach((b, i) => {
      addPartBox(
        b.name,
        b.min_x, b.min_y, b.min_z,
        b.max_x - b.min_x,
        b.max_y - b.min_y,
        b.max_z - b.min_z,
        i
      );
    });

    infoParts.textContent = `🔩 ${boxes.length} parts`;
    updateUsageChip(boxes);
    setLayoutSource('packed');
  }

  /**
   * Estimate a layout for parts that have not been packed yet.
   *
   * Mirrors the backend shelf packer (tallest-first, current clearance and edge margin,
   * quantity suffixes expanded) so the preview is a fair indication of fit rather than a
   * decorative grid. Returns the parts that did not fit, which drives the "needs N plates"
   * hint.
   */
  function previewLayout(parts) {
    const gap = state.clearance;
    const border = state.border;
    const usableX = PLATFORM.x - 2 * border;
    const usableY = PLATFORM.y - 2 * border;

    // Expand quantities the same way the backend does: an "x4" file is four parts here.
    const sized = [];
    parts
      .filter(p => p.geometry && typeof p.geometry.size_x === 'number')
      .forEach(p => {
        const qty = p.quantity || 1;
        for (let i = 1; i <= qty; i++) {
          sized.push({
            name: qty > 1 ? `${p.filename} [${i} of ${qty}]` : p.filename,
            source: p.filename,
            sx: p.geometry.size_x,
            sy: p.geometry.size_y,
            sz: p.geometry.size_z,
          });
        }
      });

    const is3d = state.selectedMode === '3d';
    const placed = [];
    const spill = [];

    if (is3d) {
      // Mirror the backend's column packer: each design's copies become one vertical
      // stack, bounded by the height limit rather than by the unit count.
      const baseZ = 0.5;
      const tierGap = state.layerGapZ;
      const ceiling = Math.min(state.maxPlateHeight, PLATFORM.z);

      const groups = new Map();
      sized.forEach(item => {
        if (!groups.has(item.source)) groups.set(item.source, []);
        groups.get(item.source).push(item);
      });

      // Tallest design first, matching the backend.
      const ordered = [...groups.values()].sort((a, b) => b[0].sz - a[0].sz);

      let cx = border, cy = border, shelfH = 0, tallest = 0, columns = 0, full = false;

      ordered.forEach(members => {
        if (full) { spill.push(...members); return; }

        const item = members[0];
        let sx = item.sx, sy = item.sy;
        if (sx > usableX || sy > usableY) {
          // Keep the model's angle; turn it only to make it fit at all.
          if (item.sy <= usableX && item.sx <= usableY) { sx = item.sy; sy = item.sx; }
          else { spill.push(...members); return; }
        }
        if (baseZ + item.sz > ceiling) { spill.push(...members); return; }

        const perColumn = Math.max(1, Math.floor((ceiling - baseZ + tierGap) / (item.sz + tierGap)));

        for (let i = 0; i < members.length; i += perColumn) {
          const chunk = members.slice(i, i + perColumn);

          if (cx + sx > border + usableX) { cx = border; cy += shelfH + gap; shelfH = 0; }
          if (cy + sy > border + usableY) { spill.push(...members.slice(i)); full = true; return; }

          let z = baseZ;
          chunk.forEach(unit => {
            placed.push({
              name: unit.name,
              min_x: cx, max_x: cx + sx,
              min_y: cy, max_y: cy + sy,
              min_z: z, max_z: z + unit.sz,
            });
            z += unit.sz + tierGap;
          });

          columns += 1;
          tallest = Math.max(tallest, chunk.length);
          cx += sx + gap;
          shelfH = Math.max(shelfH, sy);
        }
      });

      const height3d = placed.reduce((h, b) => Math.max(h, b.max_z), 0);
      return {
        placed, spill, measured: sized.length, files: parts.length,
        tiers: Math.max(1, tallest), columns, height: height3d,
      };
    }

    // 2D: a single tier, tallest shelf first.
    sized.sort((a, b) => (b.sy - a.sy) || (b.sx - a.sx));
    let cx = border, cy = border, shelfH = 0;

    sized.forEach(item => {
      let sx = item.sx, sy = item.sy;
      if (sx > usableX || sy > usableY) {
        if (item.sy <= usableX && item.sx <= usableY) { sx = item.sy; sy = item.sx; }
        else { spill.push(item); return; }
      }

      if (cx + sx > border + usableX) { cx = border; cy += shelfH + gap; shelfH = 0; }
      if (cy + sy > border + usableY) { spill.push(item); return; }

      placed.push({
        name: item.name,
        min_x: cx, max_x: cx + sx,
        min_y: cy, max_y: cy + sy,
        min_z: 0.5, max_z: 0.5 + item.sz,
      });
      cx += sx + gap;
      shelfH = Math.max(shelfH, sy);
    });

    const height = placed.reduce((h, b) => Math.max(h, b.max_z), 0);
    return {
      placed, spill, measured: sized.length, files: parts.length,
      tiers: 1, columns: placed.length, height,
    };
  }

  /** Total parts a file list produces once quantity suffixes are applied. */
  function totalInstances(parts) {
    return (parts || []).reduce((n, p) => n + (p.quantity || 1), 0);
  }

  function renderScannedParts(parts) {
    clearParts();
    if (!parts || !parts.length) {
      infoParts.textContent = '🔩 0 parts';
      updateFitPreview(null);
      updateUsageChip([]);
      return;
    }

    const layout = previewLayout(parts);
    layout.placed.forEach((b, i) => {
      addPartBox(b.name, b.min_x, b.min_y, b.min_z,
                 b.max_x - b.min_x, b.max_y - b.min_y, b.max_z - b.min_z, i);
    });

    const instances = totalInstances(parts);
    infoParts.textContent = instances === parts.length
      ? `🔩 ${parts.length} parts`
      : `🔩 ${instances} parts (${parts.length} files)`;
    updateUsageChip(layout.placed);
    setLayoutSource('preview');
    updateFitPreview(layout);
  }

  function updateFitPreview(layout) {
    if (!layout) {
      fitFill.style.width = '0%';
      fitText.textContent = 'Scan a folder to estimate plate fit';
      fitFill.className = 'fit-preview__fill';
      return;
    }

    if (layout.measured === 0) {
      fitFill.style.width = '0%';
      fitText.textContent = 'Part sizes not measured yet — run a pack to get exact fit';
      return;
    }

    const fitCount = layout.placed.length;
    const pct = Math.round(fitCount / layout.measured * 100);
    fitFill.style.width = `${pct}%`;

    // "parts" here means instances after quantity expansion, which is what occupies the
    // plate — saying "files" would understate an x16 batch by an order of magnitude.
    const expanded = layout.measured !== layout.files
      ? ` (from ${layout.files} file${layout.files === 1 ? '' : 's'})` : '';

    // In 3D the shape of the plate is "N columns stacked M high"; height predicts print
    // time, so it leads.
    const stack = (layout.tiers || 1) > 1
      ? ` as <strong>${layout.columns} stacks</strong> of up to ` +
        `<strong>${layout.tiers}</strong>, ${layout.height.toFixed(1)} mm tall`
      : '';

    if (layout.spill.length === 0) {
      fitFill.className = 'fit-preview__fill fit-preview__fill--ok';
      fitText.innerHTML =
        `All <strong>${fitCount}</strong> parts${expanded} fit on <strong>1 plate</strong>` +
        `${stack} at ${state.clearance.toFixed(1)} mm spacing.`;
    } else {
      const plates = Math.ceil(layout.measured / Math.max(1, fitCount));
      fitFill.className = 'fit-preview__fill fit-preview__fill--warn';
      fitText.innerHTML =
        `<strong>${fitCount}</strong> of <strong>${layout.measured}</strong> parts${expanded} ` +
        `fit on plate 1${stack} — about <strong>${plates} plates</strong> ` +
        `at ${state.clearance.toFixed(1)} mm spacing.`;
    }
  }

  /**
   * Draw a packed plate. Prefers the real placed positions from the packer and only falls
   * back to an estimate (clearly labelled) when a plate predates position reporting.
   */
  async function renderPackedPlate(plateData) {
    if (!plateData) return;

    if (plateData.placed_boxes && plateData.placed_boxes.length) {
      renderPlacedBoxes(plateData.placed_boxes);
      return;
    }

    // No positions in hand: ask the backend for this plate's audited layout.
    try {
      const query = state.currentDirectory
        ? `directory=${encodeURIComponent(state.currentDirectory)}`
        : `date=${encodeURIComponent(state.currentDate || '')}`;
      const layout = await api(
        `/api/plate-layout?plate=${encodeURIComponent(plateData.plate_name)}&${query}`);
      if (layout.placed_boxes && layout.placed_boxes.length) {
        plateData.placed_boxes = layout.placed_boxes;
        if (!plateData.build) plateData.build = layout.build;
        renderPlacedBoxes(layout.placed_boxes);
        renderBuildStatus(plateData);
        return;
      }
    } catch (err) {
      // Fall through to the estimate below.
    }

    // Last resort: estimate from scan geometry, and say so in the viewport.
    const files = plateData.packed_files || [];
    const scanParts = (state.scanResult && state.scanResult.parts) || [];
    const byName = {};
    scanParts.forEach(p => { byName[p.filename] = p; });
    const parts = files.map(f => byName[f]).filter(Boolean);

    clearParts();
    const layout = previewLayout(parts);
    layout.placed.forEach((b, i) => {
      addPartBox(b.name, b.min_x, b.min_y, b.min_z,
                 b.max_x - b.min_x, b.max_y - b.min_y, b.max_z - b.min_z, i);
    });
    infoParts.textContent = `🔩 ${files.length} parts`;
    updateUsageChip(layout.placed);
    setLayoutSource('preview');
  }


  // ── API Helpers ────────────────────────────────────────────
  async function api(path, options = {}) {
    try {
      const resp = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || resp.statusText);
      }
      return await resp.json();
    } catch (err) {
      toast(err.message, 'error');
      throw err;
    }
  }

  // ── Toast Notifications ────────────────────────────────────
  function toast(message, type = 'info') {
    const icons = { success: '✓', error: '✕', info: 'ℹ' };
    const el = document.createElement('div');
    el.className = `toast toast--${type}`;
    el.innerHTML = `<span>${icons[type] || 'ℹ'}</span> ${message}`;
    toastContainer.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(50px)';
      el.style.transition = 'all 0.3s ease';
      setTimeout(() => el.remove(), 300);
    }, 4000);
  }

  // ── Event Handlers ─────────────────────────────────────────

  // Today button
  btnToday.addEventListener('click', () => {
    const now = new Date();
    const dd = String(now.getDate()).padStart(2, '0');
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const yyyy = now.getFullYear();
    dateInput.value = `${dd}.${mm}.${yyyy}`;
  });

  // Scan button
  btnScan.addEventListener('click', async () => {
    const rawInput = dateInput.value.trim();
    if (!rawInput) {
      toast('Please enter a date or folder path first', 'error');
      return;
    }

    btnScan.disabled = true;
    scanProgress.style.display = 'block';

    try {
      const isPath = rawInput.includes('\\') || rawInput.includes('/') || rawInput.includes(':');
      const url = isPath
        ? `/api/scan?directory=${encodeURIComponent(rawInput)}`
        : `/api/scan?date=${encodeURIComponent(rawInput)}`;

      const result = await api(url);
      state.scanResult = result;
      state.currentDate = result.date;
      state.currentDirectory = result.directory;

      if (targetFolderDisplay) {
        targetFolderDisplay.style.display = 'block';
        targetFolderDisplay.innerHTML = `📁 <strong>Path:</strong> ${result.directory}`;
      }

      if (!result.exists) {
        partsContainer.innerHTML = `
          <div class="empty-state">
            <div class="empty-state__icon">🚫</div>
            <div class="empty-state__text">Folder not found:<br/><code style="font-size:0.75rem;">${result.directory}</code></div>
          </div>`;
        scannerBadge.textContent = '0';
        scannerBadge.style.background = 'var(--red-glow)';
        scannerBadge.style.color = 'var(--red)';
        clearParts();
        btnPack.disabled = true;
        return;
      }

      // Render parts list
      if (result.parts.length === 0) {
        partsContainer.innerHTML = `
          <div class="empty-state">
            <div class="empty-state__icon">📭</div>
            <div class="empty-state__text">No STL parts found in folder</div>
          </div>`;
        scannerBadge.textContent = '0';
        clearParts();
        btnPack.disabled = true;
      } else {
        let html = '<div class="parts-list">';
        result.parts.forEach((p, i) => {
          const color = PART_COLORS[i % PART_COLORS.length];
          const colorHex = '#' + color.toString(16).padStart(6, '0');
          const dims = p.geometry
            ? `${p.geometry.size_x} × ${p.geometry.size_y} × ${p.geometry.size_z} mm`
            : 'unknown';
          const tris = p.geometry ? `${(p.geometry.triangles / 1000).toFixed(1)}K △` : '';
          const qty = p.quantity || 1;

          html += `
            <div class="part-item">
              <div class="part-item__icon" style="color: ${colorHex};">◆</div>
              <div class="part-item__info">
                <div class="part-item__name" title="${p.filename}">${p.filename}</div>
                <div class="part-item__meta">${dims} • ${tris}</div>
              </div>
              ${qty > 1 ? `<span class="part-item__qty" title="Filename requests ${qty} copies">×${qty}</span>` : ''}
              ${p.is_repaired ? '<span class="part-item__badge">repaired</span>' : ''}
            </div>`;
        });
        html += '</div>';
        partsContainer.innerHTML = html;

        // Make the multiplier explicit: it changes how many parts reach the plate, and a
        // misread suffix is the difference between 31 parts and 300.
        const instances = totalInstances(result.parts);
        const multiplied = result.parts.filter(p => (p.quantity || 1) > 1);
        if (multiplied.length) {
          quantitySummary.style.display = 'block';
          quantitySummary.innerHTML =
            `✕ <strong>${multiplied.length}</strong> file(s) carry a quantity suffix — ` +
            `<strong>${result.parts.length} files → ${instances} parts</strong> on the plate.`;
        } else {
          quantitySummary.style.display = 'none';
        }

        scannerBadge.textContent = instances === result.parts.length
          ? result.parts.length
          : `${result.parts.length} → ${instances}`;
        scannerBadge.style.background = 'var(--emerald-glow)';
        scannerBadge.style.color = 'var(--emerald)';

        // Enable packing
        btnPack.disabled = false;

        // Render preview on platform
        renderScannedParts(result.parts);
      }

      // Check for existing plates
      await loadExistingPlates(state.currentDirectory || state.currentDate);

      toast(`Scanned ${result.parts.length} parts from ${result.date}`, 'success');
    } catch (err) {
      // Toast already shown by api()
    } finally {
      btnScan.disabled = false;
      scanProgress.style.display = 'none';
    }
  });

  // Strategy toggle
  strategyToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-mode]');
    if (!btn) return;

    state.selectedMode = btn.dataset.mode;
    strategyToggle.querySelectorAll('.strategy-toggle__option').forEach(b => {
      b.classList.toggle('strategy-toggle__option--active', b === btn);
    });

    const descriptions = {
      auto: '<strong>Auto</strong> — 3D for repeat batches (x3+ in the filename), 2D otherwise.',
      '2d': '<strong>2D Flat</strong> — Single tier. Minimises Z-height and print time.',
      '3d': '<strong>3D Pack</strong> — Each file\'s copies stack into one column at its own angle. Columns grow until they reach the height limit, then a new one starts alongside.',
    };
    strategyInfo.innerHTML = descriptions[state.selectedMode];

    // 2D and 3D pack differently, so the preview has to be recomputed on a mode change
    // rather than left showing the previous mode's layout.
    onSpacingChanged();
  });

  // ── Pack Run (job + progress polling) ──────────────────────

  function showPackProgress(show) {
    packProgress.style.display = show ? 'block' : 'none';
    if (!show) {
      packFill.style.width = '0%';
      packPct.textContent = '0%';
      packStage.textContent = 'Starting…';
      packElapsed.textContent = '0:00';
      packEta.textContent = '—';
    }
  }

  function updatePackProgress(job) {
    const pct = job.progress_percent != null ? job.progress_percent : 0;
    packFill.style.width = `${pct}%`;
    packPct.textContent = `${pct.toFixed(0)}%`;
    packStage.textContent = job.stage || 'Working…';
    packElapsed.textContent = job.elapsed_human || '0:00';
    packEta.textContent = job.eta_human || '—';
  }

  // ── Height-cap Decision ────────────────────────────────────

  /**
   * Present the two costed plans and let the operator choose.
   *
   * Both come from a plan rather than a build, so the run is paused before any merging —
   * answering costs only the thinking time, not a wasted merge.
   */
  function showCapDecision(decision) {
    const cap = decision.capped;
    const over = decision.uncapped;

    capPlateName.textContent = decision.plate_name;
    capLead.innerHTML =
      `Fitting everything on this plate needs <strong>${over.height_mm.toFixed(1)} mm</strong>, ` +
      `past your <strong>${cap.height_limit_mm.toFixed(0)} mm</strong> limit. ` +
      `Staying under it moves <strong>${decision.extra_parts_if_exceeded}</strong> part(s) ` +
      `to the next plate.`;

    capSplitHeight.textContent = `${cap.height_mm.toFixed(1)} mm`;
    capSplitDetail.innerHTML =
      `<strong>${cap.parts_placed}</strong> parts · ${cap.tiers} tier(s)<br>` +
      `${cap.parts_left} move to the next plate`;

    capExceedHeight.textContent = `${over.height_mm.toFixed(1)} mm`;
    capExceedDetail.innerHTML =
      `<strong>${over.parts_placed}</strong> parts · ${over.tiers} tier(s)<br>` +
      `${over.parts_left} move to the next plate`;

    capTimeoutNote.textContent =
      'No answer within 15 minutes keeps the limit and splits the plate.';

    capOverlay.classList.add('manifest-overlay--visible');
  }

  function hideCapDecision() {
    capOverlay.classList.remove('manifest-overlay--visible');
  }

  async function answerCapDecision(choice) {
    if (!state.packJobId) return;
    hideCapDecision();
    try {
      await api(`/api/pack/decide/${state.packJobId}`, {
        method: 'POST',
        body: JSON.stringify({ choice }),
      });
      toast(choice === 'exceed'
        ? 'Allowing the taller plate'
        : 'Keeping the height limit — surplus moves to the next plate', 'info');
    } catch (err) {
      // api() has already reported it.
    }
  }

  $('cap-choice-split').addEventListener('click', () => answerCapDecision('split'));
  $('cap-choice-exceed').addEventListener('click', () => answerCapDecision('exceed'));

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function finishPack(job) {
    stopPolling();
    btnPack.disabled = false;
    btnPack.innerHTML = '⚡ Run Plate Pack';

    if (job.status === 'error' || !job.result) {
      showPackProgress(false);
      toast(job.error || 'Packing failed', 'error');
      return;
    }

    const result = job.result;
    state.packResult = result;
    updatePackProgress(job);

    updateSentryCards(result);

    if (result.plates && result.plates.length > 0) {
      state.activePlateIndex = 0;
      showPlates(result.plates);
      await renderPackedPlate(result.plates[0]);
      renderBuildStatus(result.plates[0]);
      updateActionButtons(result.plates[0]);
    } else {
      updateActionButtons(null);
    }

    // Report the outcome honestly: an unbuilt plate is a failure, not a success with a
    // green toast. This is the case that used to be reported as a clean pass.
    const unbuilt = (result.plates || []).filter(p => !(p.build && p.build.plate_built));
    if (!result.success || unbuilt.length) {
      toast(
        result.error_message ||
        `${unbuilt.length} plate(s) were not built — check the plate status panel`,
        'error'
      );
    } else {
      const engines = [...new Set(result.plates.map(p => p.build.builder))].join(', ');
      toast(
        `Built ${result.total_plates_generated} plate(s) from ${result.total_valid_parts} parts ` +
        `via ${engines} in ${result.duration_human || result.duration_seconds + 's'}`,
        'success'
      );
    }

    setTimeout(() => showPackProgress(false), 2500);
  }

  btnPack.addEventListener('click', async () => {
    if (!state.currentDirectory && !state.currentDate) {
      toast('Scan a date or folder first', 'error');
      return;
    }

    btnPack.disabled = true;
    btnPack.innerHTML = '<span class="spinner"></span> Packing…';
    showPackProgress(true);

    try {
      const start = await api('/api/pack', {
        method: 'POST',
        body: JSON.stringify({
          date: state.currentDate,
          directory: state.currentDirectory,
          mode: state.selectedMode,
          clearance: state.clearance,
          border_spacing_xy: state.border,
          layer_gap_z: state.layerGapZ,
          max_plate_height: state.maxPlateHeight,
        }),
      });

      state.packJobId = start.job_id;

      // Poll rather than hold a request open: a real batch runs for minutes and the
      // operator needs to watch it advance, not stare at a spinner.
      stopPolling();
      state.pollTimer = setInterval(async () => {
        try {
          const job = await api(`/api/pack/status/${state.packJobId}`);
          updatePackProgress(job);

          // The run parks here until answered; keep polling but do not treat it as done.
          if (job.status === 'awaiting_decision' && job.decision) {
            showCapDecision(job.decision);
            return;
          }
          hideCapDecision();

          if (job.status !== 'running') await finishPack(job);
        } catch (err) {
          stopPolling();
          btnPack.disabled = false;
          btnPack.innerHTML = '⚡ Run Plate Pack';
          showPackProgress(false);
        }
      }, 1000);
    } catch (err) {
      btnPack.disabled = false;
      btnPack.innerHTML = '⚡ Run Plate Pack';
      showPackProgress(false);
    }
  });

  // Open in Netfabb
  btnOpenNetfabb.addEventListener('click', async () => {
    const plate = getActivePlate();
    if (!plate || !plate.fabbproject) {
      toast('No .fabbproject available', 'error');
      return;
    }

    try {
      await api('/api/open-netfabb', {
        method: 'POST',
        body: JSON.stringify({ fabbproject_path: plate.fabbproject }),
      });
      toast('Netfabb launched with project', 'success');
    } catch (err) {
      // handled
    }
  });

  // Download STL
  btnDownload.addEventListener('click', () => {
    const plate = getActivePlate();
    if (!plate || !plate.merged_stl) {
      toast('No merged STL available', 'error');
      return;
    }
    // Extract plate name and filename from path
    const parts = plate.merged_stl.replace(/\\/g, '/').split('/');
    const filename = parts.pop();
    const plateName = parts.pop();
    const date = state.currentDate || '';
    const dir = state.currentDirectory || '';
    window.open(`/api/download/${plateName}/${filename}?date=${encodeURIComponent(date)}&directory=${encodeURIComponent(dir)}`, '_blank');
  });

  // Manifest modal
  btnManifest.addEventListener('click', async () => {
    // Never fail silently: a button that does nothing when clicked is indistinguishable
    // from a broken one, which is exactly how the class-name mismatch above went unnoticed.
    if (!state.currentDirectory && !state.currentDate) {
      toast('Scan a date or folder first', 'error');
      return;
    }

    try {
      const query = state.currentDirectory
        ? `directory=${encodeURIComponent(state.currentDirectory)}`
        : `date=${encodeURIComponent(state.currentDate)}`;
      const data = await api(`/api/plates?${query}`);
      let html = '';

      if (data.root_manifest) {
        const rm = data.root_manifest;
        html += `<p style="margin-bottom: 0.75rem; font-size: 0.82rem; color: var(--text-secondary);">
          <strong>${rm.machine_name}</strong> • ${rm.strategy?.mode?.toUpperCase() || '—'} mode
          • ${rm.totals?.total_plates_generated || 0} plate(s)
          • ${rm.totals?.total_parts_packed || 0} parts packed
        </p>`;
      }

      html += '<table class="manifest-table"><thead><tr>';
      html += '<th>Plate</th><th>Mode</th><th>Parts</th><th>Plate STL</th><th>Verified</th><th>Files</th>';
      html += '</tr></thead><tbody>';

      data.plates.forEach(p => {
        const verified = p.manifest?.verification?.passed;
        const vBadge = verified
          ? '<span class="part-item__badge" style="background:var(--emerald-glow);color:var(--emerald);">PASS</span>'
          : '<span class="part-item__badge" style="background:var(--red-glow);color:var(--red);">CHECK</span>';

        const b = p.build || {};
        const bBadge = b.plate_built
          ? `<span class="part-item__badge" style="background:var(--emerald-glow);color:var(--emerald);">BUILT</span>
             <span style="font-size:0.7rem;color:var(--text-muted);">
               ${(b.merged_stl_triangles || 0).toLocaleString()} △ · ${formatBytes(b.merged_stl_bytes)}</span>`
          : `<span class="part-item__badge" style="background:var(--red-glow);color:var(--red);">NOT BUILT</span>
             <span style="font-size:0.7rem;color:var(--text-muted);">${b.failure_reason || ''}</span>`;

        html += `<tr>
          <td><strong>${p.plate_name}</strong></td>
          <td>${(p.manifest?.mode || '—').toUpperCase()}</td>
          <td>${p.part_count || (p.files || []).filter(f => f.endsWith('.stl')).length}</td>
          <td>${bBadge}</td>
          <td>${vBadge}</td>
          <td style="font-size: 0.75rem; color: var(--text-muted);">${(p.files || []).join(', ')}</td>
        </tr>`;
      });

      html += '</tbody></table>';

      if (!data.plates.length) {
        html += `<p style="font-size:0.8rem;color:var(--text-muted);">
          No plates have been generated in this folder yet — run a plate pack first.</p>`;
      }

      manifestContent.innerHTML = html;
      manifestOverlay.classList.add('manifest-overlay--visible');
    } catch (err) {
      // api() toasts its own failures, but a rendering error would otherwise vanish here.
      toast(`Could not open the manifest: ${err.message}`, 'error');
    }
  });

  manifestClose.addEventListener('click', () => {
    manifestOverlay.classList.remove('manifest-overlay--visible');
  });

  manifestOverlay.addEventListener('click', (e) => {
    if (e.target === manifestOverlay) {
      manifestOverlay.classList.remove('manifest-overlay--visible');
    }
  });

  // Camera view buttons
  $('view-iso').addEventListener('click', () => setCameraView('iso'));
  $('view-top').addEventListener('click', () => setCameraView('top'));
  $('view-front').addEventListener('click', () => setCameraView('front'));
  $('view-reset').addEventListener('click', () => setCameraView('iso'));

  // ── Sentry Quality Cards ───────────────────────────────────
  function updateSentryCards(result) {
    if (!result || !result.plates || !result.plates.length) return;

    const plate = result.plates[state.activePlateIndex] || result.plates[0];
    const v = plate.verification;

    if (!v) {
      $('q-envelope').textContent = '—';
      $('q-collisions').textContent = '—';
      $('q-clearance').textContent = '—';
      $('q-parts').textContent = plate.part_count || '—';
      return;
    }

    // Envelope
    const envOk = !v.envelope_violations || v.envelope_violations.length === 0;
    $('q-envelope').textContent = envOk ? 'PASS' : 'FAIL';
    $('q-envelope').className = `quality-item__value quality-item__value--${envOk ? 'pass' : 'fail'}`;

    // Collisions.
    //
    // On a TrueShape plate these counts are bounding-box overlaps, which outline nesting
    // produces by design — showing them as collisions would fail every Netfabb plate.
    const advisory = v.clearance_is_authoritative === false;
    const count = v.clearance_violations_count || 0;
    const collOk = advisory ? true : !v.has_hard_collisions;
    $('q-collisions').textContent = advisory && count ? `${count}*` : count;
    $('q-collisions').className =
      `quality-item__value quality-item__value--${collOk ? 'pass' : 'fail'}`;
    $('q-collisions').title = advisory && count
      ? v.clearance_note || 'Bounding-box overlaps from TrueShape outline nesting (advisory)'
      : '';

    // Clearance buffer
    const clearOk = v.passed;
    $('q-clearance').textContent = advisory && count
      ? 'TRUE■'
      : (clearOk ? `≥ ${state.clearance.toFixed(1)}` : `< ${state.clearance.toFixed(1)}`);
    $('q-clearance').title = advisory && count
      ? 'Mesh clearance enforced by the TrueShape nester, not by box distance'
      : '';
    $('q-clearance').className = `quality-item__value quality-item__value--${clearOk ? 'pass' : 'warn'}`;

    // Parts count
    $('q-parts').textContent = v.total_parts_checked || plate.part_count || 0;
    $('q-parts').className = 'quality-item__value quality-item__value--pass';
  }

  // ── Plate Tabs ─────────────────────────────────────────────
  function showPlates(plates) {
    if (!plates.length) {
      cardPlates.style.display = 'none';
      return;
    }

    cardPlates.style.display = 'block';
    plateTabs.innerHTML = '';

    plates.forEach((plate, i) => {
      const tab = document.createElement('button');
      tab.className = 'plate-tab' + (i === 0 ? ' plate-tab--active' : '');
      const built = plate.build && plate.build.plate_built;
      // Mark unbuilt plates on the tab itself so a bad plate is visible without clicking.
      tab.innerHTML = `${plate.plate_name} <span class="plate-tab__dot ` +
        `plate-tab__dot--${built ? 'ok' : 'fail'}"></span>`;
      tab.addEventListener('click', async () => {
        state.activePlateIndex = i;
        plateTabs.querySelectorAll('.plate-tab').forEach(t => t.classList.remove('plate-tab--active'));
        tab.classList.add('plate-tab--active');
        updatePlateInfo(plate);
        renderBuildStatus(plate);
        updateActionButtons(plate);
        await renderPackedPlate(plate);
        if (state.packResult) updateSentryCards(state.packResult);
      });
      plateTabs.appendChild(tab);
    });

    updatePlateInfo(plates[0]);
    renderBuildStatus(plates[0]);
  }

  function updatePlateInfo(plate) {
    plateInfo.innerHTML = `
      <strong>${plate.plate_name}</strong> • ${(plate.mode || '2d').toUpperCase()} mode • ${plate.part_count} parts
    `;
  }

  function formatBytes(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  /**
   * Show whether this plate was actually built.
   *
   * Deliberately separate from the Sentry verification cards: verification only judges the
   * layout, and it reported PASS for a plate whose STL was a 49-byte placeholder. The
   * evidence here is the file on disk — its size, triangle count, and which engine wrote it.
   */
  function renderBuildStatus(plate) {
    const b = plate && plate.build;
    if (!b) {
      buildStatus.style.display = 'none';
      return;
    }

    buildStatus.style.display = 'block';

    const engineNames = {
      'netfabb': 'Autodesk Netfabb TrueShape',
      'netfabb-placed': 'LP Agent columns, built by Netfabb',
      'lp-native': 'LP Agent native packer',
      'none': 'no packer completed',
    };

    if (b.plate_built) {
      buildBadge.textContent = '✅ PLATE BUILT';
      buildBadge.className = 'build-status__badge build-status__badge--ok';
      // Plate height drives WaxJet print time, so show it whenever the plate is stacked.
      const stack = (b.layers_used || 1) > 1
        ? `<span>▤ stacked ${b.layers_used} high</span>` +
          `<span>↕ ${(b.plate_height_mm || 0).toFixed(1)} mm</span>`
        : '';
      buildFacts.innerHTML =
        stack +
        `<span>${(b.merged_stl_triangles || 0).toLocaleString()} triangles</span>` +
        `<span>${formatBytes(b.merged_stl_bytes)}</span>` +
        (b.duration_human ? `<span>⏱ ${b.duration_human}</span>` : '');
    } else {
      buildBadge.textContent = '⛔ NOT BUILT';
      buildBadge.className = 'build-status__badge build-status__badge--fail';
      buildFacts.innerHTML =
        `<span>${formatBytes(b.merged_stl_bytes)} on disk</span>` +
        `<span>${(b.merged_stl_triangles || 0).toLocaleString()} triangles</span>`;
    }

    buildEngine.textContent = engineNames[b.builder] || b.builder || '';

    const notes = [];
    if (b.timed_out) notes.push('Netfabb exceeded its time budget.');
    if (b.failure_reason) notes.push(b.failure_reason);
    if (notes.length) {
      buildReason.style.display = 'block';
      buildReason.textContent = notes.join(' ');
    } else {
      buildReason.style.display = 'none';
    }
  }

  // ── Spacing Controls ───────────────────────────────────────
  function refreshSpacingUI() {
    clearanceValue.textContent = `${state.clearance.toFixed(1)} mm`;
    borderValue.textContent = `${state.border.toFixed(1)} mm`;
    layerGapValue.textContent = `${state.layerGapZ.toFixed(1)} mm`;
    maxHeightValue.textContent = `${state.maxPlateHeight.toFixed(0)} mm`;
    spacingBadge.textContent = `${state.clearance.toFixed(1)} mm`;

    // Above 90mm is the machine's limit rather than a working setting, so say so.
    const nearMachineLimit = state.maxPlateHeight > 90;
    maxHeightHint.innerHTML = nearMachineLimit
      ? '<strong style="color:var(--gold);">At the machine limit.</strong> ' +
        'A stack this tall is slow to print and hard to wash support wax out of.'
      : 'Taller stacks are harder to wash support wax out of. Parts that will not fit ' +
        'under this height move to the next plate.';

    heightPresetRow.querySelectorAll('.preset').forEach(btn => {
      btn.classList.toggle('preset--active',
        Math.abs(parseFloat(btn.dataset.height) - state.maxPlateHeight) < 0.5);
    });

    // The tier gap only does anything when stacking, so dim it outside 3D rather than
    // hiding it — the operator can still see and preset the value before switching mode.
    const stacking = state.selectedMode === '3d' || state.selectedMode === 'auto';
    layerGapRow.classList.toggle('slider-row--inactive', !stacking);
    maxHeightRow.classList.toggle('slider-row--inactive', !stacking);

    // Flag sub-1.5 mm as a deliberate choice rather than silently accepting it: it is
    // allowed down to the 0.3 mm floor, but it is tighter than the prong-safe default.
    const tight = state.clearance < 1.5;
    spacingBadge.style.background = tight ? 'var(--gold-glow)' : 'var(--bg-elevated)';
    spacingBadge.style.color = tight ? 'var(--gold)' : 'var(--text-muted)';
    spacingBadge.title = tight
      ? 'Below the 1.5 mm prong-safe guideline — check clearances before printing'
      : '';

    presetRow.querySelectorAll('.preset').forEach(btn => {
      const val = parseFloat(btn.dataset.clearance);
      btn.classList.toggle('preset--active', Math.abs(val - state.clearance) < 0.05);
    });
  }

  function onSpacingChanged() {
    refreshSpacingUI();
    rebuildPlatform();
    // Re-estimate the layout so the operator sees the effect of the change immediately,
    // rather than only after a multi-minute pack run.
    if (state.scanResult && state.scanResult.parts && state.scanResult.parts.length) {
      renderScannedParts(state.scanResult.parts);
    }
  }

  clearanceRange.addEventListener('input', () => {
    state.clearance = Math.max(state.minClearance, parseFloat(clearanceRange.value));
    onSpacingChanged();
  });

  borderRange.addEventListener('input', () => {
    state.border = parseFloat(borderRange.value);
    onSpacingChanged();
  });

  layerGapRange.addEventListener('input', () => {
    state.layerGapZ = Math.max(state.minClearance, parseFloat(layerGapRange.value));
    onSpacingChanged();
  });

  maxHeightRange.addEventListener('input', () => {
    state.maxPlateHeight = Math.min(state.machineMaxHeight, parseFloat(maxHeightRange.value));
    onSpacingChanged();
  });

  heightPresetRow.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-height]');
    if (!btn) return;
    state.maxPlateHeight = Math.min(state.machineMaxHeight, parseFloat(btn.dataset.height));
    maxHeightRange.value = state.maxPlateHeight;
    onSpacingChanged();
  });

  presetRow.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-clearance]');
    if (!btn) return;
    state.clearance = Math.max(state.minClearance, parseFloat(btn.dataset.clearance));
    clearanceRange.value = state.clearance;
    onSpacingChanged();
  });

  // ── Viewport Display Toggles ───────────────────────────────
  $('toggle-bbox').addEventListener('click', () => {
    state.showBBox = !state.showBBox;
    $('toggle-bbox').classList.toggle('btn--active', state.showBBox);
    applyDisplayToggles();
  });

  $('toggle-solid').addEventListener('click', () => {
    state.showSolid = !state.showSolid;
    $('toggle-solid').classList.toggle('btn--active', state.showSolid);
    applyDisplayToggles();
  });

  /**
   * Enable only the actions this plate can actually perform.
   *
   * The native packer writes no .fabbproject, so offering "Open in Netfabb" for such a
   * plate just produces an error later. Same for exporting a plate that was never built.
   */
  function updateActionButtons(plate) {
    const built = !!(plate && plate.build && plate.build.plate_built);
    btnOpenNetfabb.disabled = !(plate && plate.fabbproject);
    btnOpenNetfabb.title = plate && plate.fabbproject
      ? 'Open this plate in the Netfabb GUI'
      : 'No Netfabb project for this plate — export the merged STL instead';
    btnDownload.disabled = !built;
    btnDownload.title = built
      ? 'Download the merged plate STL'
      : 'This plate has no geometry to export';
    btnManifest.disabled = false;
  }

  function getActivePlate() {
    if (!state.packResult || !state.packResult.plates) return null;
    return state.packResult.plates[state.activePlateIndex] || state.packResult.plates[0];
  }

  // ── Load Existing Plates ───────────────────────────────────
  async function loadExistingPlates(dateOrDir) {
    try {
      const isPath = dateOrDir && (dateOrDir.includes('\\') || dateOrDir.includes('/') || dateOrDir.includes(':'));
      const query = isPath ? `directory=${encodeURIComponent(dateOrDir)}` : `date=${encodeURIComponent(dateOrDir)}`;
      const data = await api(`/api/plates?${query}`);
      if (data.plates && data.plates.length > 0) {
        // Build a packResult-like structure from existing plates
        const plates = data.plates.map((p, i) => ({
          plate_index: i + 1,
          plate_name: p.plate_name,
          mode: p.manifest?.mode || '2d',
          part_count: p.manifest?.part_count || 0,
          packed_files: p.manifest?.packed_files || [],
          merged_stl: p.merged_stl,
          fabbproject: p.fabbproject,
          verification: p.manifest?.verification || null,
          // Build status comes from inspecting the STL on disk, so reopening a folder
          // exposes placeholder plates left behind by earlier runs.
          build: p.build || p.manifest?.build || null,
          placed_boxes: p.manifest?.placed_boxes || [],
        }));

        state.packResult = {
          success: plates.every(p => p.build && p.build.plate_built),
          plates,
          total_plates_generated: plates.length,
        };

        showPlates(plates);
        updateActionButtons(plates[0]);

        await renderPackedPlate(plates[0]);

        // Update quality cards from first plate
        if (plates[0].verification) {
          updateSentryCards(state.packResult);
        }

        const unbuilt = plates.filter(p => !(p.build && p.build.plate_built));
        if (unbuilt.length) {
          toast(
            `${unbuilt.length} existing plate(s) contain no geometry: ` +
            unbuilt.map(p => p.plate_name).join(', '),
            'error'
          );
        }
      }
    } catch (err) {
      // Plates endpoint not critical
    }
  }

  // ── Sidebar: collapsible sections & scroll affordance ──────

  const COLLAPSE_KEY = 'lp.collapsedCards';

  function readCollapsed() {
    // Browser storage can throw outright (private windows, blocked site data), so a
    // missing preference must never stop the panel rendering.
    try {
      return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]'));
    } catch (err) {
      return new Set();
    }
  }

  function writeCollapsed(ids) {
    try {
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...ids]));
    } catch (err) {
      // A remembered fold is a convenience, not something worth surfacing.
    }
  }

  /** True when the panel has more content than fits, so the foot fade is warranted. */
  function refreshScrollAffordance() {
    const overflowing = sidebar.scrollHeight - sidebar.clientHeight > 4;
    sidebarWrap.classList.toggle('sidebar-wrap--scrollable', overflowing);
  }

  function setCardCollapsed(card, collapsed) {
    card.classList.toggle('card--collapsed', collapsed);
    const chevron = card.querySelector('.card__chevron');
    if (chevron) chevron.textContent = '▼';
  }

  function initCollapsibleCards() {
    const collapsed = readCollapsed();

    sidebar.querySelectorAll('.card').forEach(card => {
      const header = card.querySelector('.card__header');
      if (!header || !card.id) return;

      if (!header.querySelector('.card__chevron')) {
        const chevron = document.createElement('span');
        chevron.className = 'card__chevron';
        chevron.textContent = '▼';
        header.appendChild(chevron);
      }

      setCardCollapsed(card, collapsed.has(card.id));

      header.addEventListener('click', () => {
        const nowCollapsed = !card.classList.contains('card--collapsed');
        setCardCollapsed(card, nowCollapsed);

        const ids = readCollapsed();
        if (nowCollapsed) ids.add(card.id); else ids.delete(card.id);
        writeCollapsed(ids);

        refreshScrollAffordance();
        updateToggleAllLabel();
      });
    });

    updateToggleAllLabel();
    refreshScrollAffordance();
  }

  function visibleCards() {
    return [...sidebar.querySelectorAll('.card')]
      .filter(c => c.offsetParent !== null || !c.style.display);
  }

  function updateToggleAllLabel() {
    const cards = visibleCards();
    const anyOpen = cards.some(c => !c.classList.contains('card--collapsed'));
    btnToggleAll.textContent = anyOpen ? 'Collapse all' : 'Expand all';
  }

  btnToggleAll.addEventListener('click', () => {
    const cards = visibleCards();
    const collapseThem = cards.some(c => !c.classList.contains('card--collapsed'));
    const ids = readCollapsed();

    cards.forEach(card => {
      setCardCollapsed(card, collapseThem);
      if (!card.id) return;
      if (collapseThem) ids.add(card.id); else ids.delete(card.id);
    });

    writeCollapsed(ids);
    refreshScrollAffordance();
    updateToggleAllLabel();
  });

  sidebar.addEventListener('scroll', refreshScrollAffordance, { passive: true });
  if (window.ResizeObserver) {
    // Cards grow and shrink as parts load and plates appear, so the fade has to follow.
    new ResizeObserver(refreshScrollAffordance).observe(sidebar);
  }

  // ── Bootstrap ──────────────────────────────────────────────
  async function init() {
    // Load system status first so the viewport is built with the real envelope and the
    // server's configured spacing, rather than hardcoded values that may disagree.
    try {
      const status = await api('/api/status');

      PLATFORM.x = status.envelope_mm.x;
      PLATFORM.y = status.envelope_mm.y;
      PLATFORM.z = status.envelope_mm.z;

      state.clearance = status.parameters.clearance_buffer_mm;
      state.border = status.parameters.border_spacing_xy_mm;
      // The server owns the spacing floor; mirror it rather than hardcoding it here.
      state.minClearance = status.parameters.min_clearance_mm ?? 0.3;
      state.layerGapZ = status.parameters.layer_gap_z_mm ?? 3.0;
      state.maxPlateHeight = status.parameters.max_plate_height_mm ?? 60.0;
      state.machineMaxHeight = status.parameters.machine_max_height_mm ?? PLATFORM.z;
      clearanceRange.min = state.minClearance;
      layerGapRange.min = state.minClearance;
      maxHeightRange.max = state.machineMaxHeight;
      clearanceRange.value = state.clearance;
      borderRange.value = state.border;
      layerGapRange.value = state.layerGapZ;
      maxHeightRange.value = state.maxPlateHeight;

      const netfabbOk = status.netfabb_console_available;
      statusNetfabb.className = `status-badge status-badge--${netfabbOk ? 'ok' : 'warn'}`;
      statusNetfabbTx.textContent = netfabbOk ? 'Netfabb ✓' : 'Netfabb ✕ (native packer)';

      $('status-printer-text').textContent =
        `${status.envelope_mm.x} × ${status.envelope_mm.y} × ${status.envelope_mm.z} mm`;

      // Show which build is actually serving. The server loads its code once at boot, so
      // a tab left open across a code change silently keeps running the old agent.
      if (status.app_version) {
        const started = status.server_started_at
          ? new Date(status.server_started_at).toLocaleTimeString()
          : '';
        statusVersion.textContent = `v${status.app_version}`;
        statusVersion.title = started
          ? `Server started ${started}. Restart it to pick up code changes.`
          : 'Restart the server to pick up code changes.';
      }
      $('info-dims').textContent =
        `📐 ${status.envelope_mm.x} × ${status.envelope_mm.y} × ${status.envelope_mm.z} mm`;

      // Auto-fill today's date
      dateInput.value = status.today;
    } catch (err) {
      toast('Failed to connect to LP Agent backend', 'error');
    }

    refreshSpacingUI();
    initCollapsibleCards();
    initThreeJS();
    setLayoutSource('preview');
    $('toggle-bbox').classList.toggle('btn--active', state.showBBox);
    $('toggle-solid').classList.toggle('btn--active', state.showSolid);
  }

  // Start
  init();

})();
