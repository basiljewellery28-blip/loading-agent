/* ═══════════════════════════════════════════════════════════════
   LP Agent — Application Logic & Three.js 3D Viewport
   ═══════════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ── WaxJet 51C Platform Constants ──────────────────────────
  const PLATFORM = { x: 235, y: 138, z: 100 };
  const BORDER   = 3.0;  // border spacing XY

  // ── State ──────────────────────────────────────────────────
  const state = {
    currentDate: null,
    scanResult: null,
    packResult: null,
    selectedMode: 'auto',
    activePlateIndex: 0,
    partMeshes: [],   // Three.js mesh objects on the platform
  };

  // ── DOM References ─────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  const dateInput       = $('date-input');
  const btnToday        = $('btn-today');
  const btnScan         = $('btn-scan');
  const scanProgress    = $('scan-progress');
  const partsContainer  = $('parts-container');
  const scannerBadge    = $('scanner-badge');

  const strategyToggle  = $('strategy-toggle');
  const strategyInfo    = $('strategy-info');

  const btnPack         = $('btn-pack');
  const btnOpenNetfabb  = $('btn-open-netfabb');
  const btnDownload     = $('btn-download');
  const btnManifest     = $('btn-manifest');

  const cardPlates      = $('card-plates');
  const plateTabs       = $('plate-tabs');
  const plateInfo       = $('plate-info');

  const statusNetfabb   = $('status-netfabb');
  const statusNetfabbTx = $('status-netfabb-text');

  const toastContainer  = $('toast-container');
  const manifestOverlay = $('manifest-overlay');
  const manifestContent = $('manifest-content');
  const manifestClose   = $('manifest-close');

  const infoParts       = $('info-parts');
  const tooltip3d       = $('tooltip-3d');
  const tooltipTitle    = $('tooltip-title');
  const tooltipDims     = $('tooltip-dims');
  const tooltipPos      = $('tooltip-pos');

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
    const innerX = PLATFORM.x - 2 * BORDER;
    const innerY = PLATFORM.y - 2 * BORDER;
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
        tooltipPos.textContent = `Pos: (${d.posX.toFixed(1)}, ${d.posY.toFixed(1)}, ${d.posZ.toFixed(1)})`;
        tooltip3d.style.left = (event.clientX - $('viewport-area').getBoundingClientRect().left + 16) + 'px';
        tooltip3d.style.top = (event.clientY - $('viewport-area').getBoundingClientRect().top - 16) + 'px';
        tooltip3d.classList.add('tooltip-3d--visible');

        // Highlight
        partsGroup.children.forEach(m => {
          if (m.material && m.material.emissive) {
            m.material.emissive.setHex(m === obj ? 0x06b6d4 : 0x000000);
            m.material.emissiveIntensity = m === obj ? 0.3 : 0;
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

    mesh.userData = {
      partName: name,
      sizeX: sizeX.toFixed(1),
      sizeY: sizeY.toFixed(1),
      sizeZ: sizeZ.toFixed(1),
      posX: x,
      posY: y,
      posZ: z,
    };

    partsGroup.add(mesh);
    state.partMeshes.push(mesh);

    // Wireframe outline
    const edgesGeo = new THREE.EdgesGeometry(geo);
    const edgesMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.15 });
    const edges = new THREE.LineSegments(edgesGeo, edgesMat);
    edges.position.copy(mesh.position);
    partsGroup.add(edges);
  }

  function renderScannedParts(parts) {
    clearParts();
    if (!parts || !parts.length) {
      infoParts.textContent = '🔩 0 parts';
      return;
    }

    // Simple 2D grid layout for preview
    const GAP = 2.0;
    let currentX = BORDER;
    let currentY = BORDER;
    let rowMaxY = 0;

    parts.forEach((part, i) => {
      const g = part.geometry;
      if (!g) return;

      const sx = g.size_x, sy = g.size_y, sz = g.size_z;

      // Wrap to next row if needed
      if (currentX + sx > PLATFORM.x - BORDER) {
        currentX = BORDER;
        currentY += rowMaxY + GAP;
        rowMaxY = 0;
      }

      addPartBox(part.filename, currentX, currentY, 0.5, sx, sy, sz, i);
      currentX += sx + GAP;
      rowMaxY = Math.max(rowMaxY, sy);
    });

    infoParts.textContent = `🔩 ${parts.length} parts`;
  }

  function renderPackedPlate(plateData) {
    clearParts();
    if (!plateData) return;

    // If we have a manifest with position data, use it
    // Otherwise render the packed files as a grid
    const files = plateData.packed_files || [];
    const mode = plateData.mode || '2d';

    // Get the geometry from scan result if available
    const scanParts = state.scanResult ? state.scanResult.parts : [];
    const geomMap = {};
    scanParts.forEach(p => { geomMap[p.filename] = p.geometry; });

    const GAP = 2.0;
    let currentX = BORDER;
    let currentY = BORDER;
    let currentZ = 0.5;
    let rowMaxY = 0;
    let rowCount = 0;

    files.forEach((filename, i) => {
      const g = geomMap[filename];
      const sx = g ? g.size_x : 8;
      const sy = g ? g.size_y : 8;
      const sz = g ? g.size_z : 8;

      if (currentX + sx > PLATFORM.x - BORDER) {
        currentX = BORDER;
        currentY += rowMaxY + GAP;
        rowMaxY = 0;
        rowCount++;
      }

      // For 3D mode, stack in Z when we run out of XY space
      if (mode === '3d' && currentY + sy > PLATFORM.y - BORDER) {
        currentX = BORDER;
        currentY = BORDER;
        rowMaxY = 0;
        currentZ += sz + GAP;
        rowCount = 0;
      }

      addPartBox(filename, currentX, currentY, currentZ, sx, sy, sz, i);
      currentX += sx + GAP;
      rowMaxY = Math.max(rowMaxY, sy);
    });

    infoParts.textContent = `🔩 ${files.length} parts`;
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
    const date = dateInput.value.trim();
    if (!date) {
      toast('Please enter a date first', 'error');
      return;
    }

    btnScan.disabled = true;
    scanProgress.style.display = 'block';

    try {
      const result = await api(`/api/scan?date=${encodeURIComponent(date)}`);
      state.scanResult = result;
      state.currentDate = date;

      if (!result.exists) {
        partsContainer.innerHTML = `
          <div class="empty-state">
            <div class="empty-state__icon">🚫</div>
            <div class="empty-state__text">Folder not found for ${date}</div>
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

          html += `
            <div class="part-item">
              <div class="part-item__icon" style="color: ${colorHex};">◆</div>
              <div class="part-item__info">
                <div class="part-item__name" title="${p.filename}">${p.filename}</div>
                <div class="part-item__meta">${dims} • ${tris}</div>
              </div>
              ${p.is_repaired ? '<span class="part-item__badge">repaired</span>' : ''}
            </div>`;
        });
        html += '</div>';
        partsContainer.innerHTML = html;

        scannerBadge.textContent = result.parts.length;
        scannerBadge.style.background = 'var(--emerald-glow)';
        scannerBadge.style.color = 'var(--emerald)';

        // Enable packing
        btnPack.disabled = false;

        // Render preview on platform
        renderScannedParts(result.parts);
      }

      // Check for existing plates
      await loadExistingPlates(date);

      toast(`Scanned ${result.parts.length} parts from ${date}`, 'success');
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
      auto: '<strong>Auto</strong> — Tactician selects based on batch geometry & homogeneity.',
      '2d': '<strong>2D Flat</strong> — Single-layer nesting. Minimizes Z-height and print time.',
      '3d': '<strong>3D Pack</strong> — Multi-tier stacking. Maximum batch density for repeat orders.',
    };
    strategyInfo.innerHTML = descriptions[state.selectedMode];
  });

  // Pack button
  btnPack.addEventListener('click', async () => {
    if (!state.currentDate) {
      toast('Scan a date folder first', 'error');
      return;
    }

    btnPack.disabled = true;
    btnPack.innerHTML = '<span class="spinner"></span> Packing…';

    try {
      const result = await api('/api/pack', {
        method: 'POST',
        body: JSON.stringify({
          date: state.currentDate,
          mode: state.selectedMode,
          clearance: 2.0,
        }),
      });

      state.packResult = result;

      // Update quality gate
      updateSentryCards(result);

      // Show plates
      if (result.plates && result.plates.length > 0) {
        showPlates(result.plates);
        renderPackedPlate(result.plates[0]);
      }

      // Enable action buttons
      btnOpenNetfabb.disabled = false;
      btnDownload.disabled = false;
      btnManifest.disabled = false;

      toast(
        `Packed ${result.total_valid_parts} parts → ${result.total_plates_generated} plate(s) in ${result.duration_seconds}s`,
        'success'
      );
    } catch (err) {
      // Toast already shown
    } finally {
      btnPack.disabled = false;
      btnPack.innerHTML = '⚡ Run Plate Pack';
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
    window.open(`/api/download/${plateName}/${filename}?date=${encodeURIComponent(date)}`, '_blank');
  });

  // Manifest modal
  btnManifest.addEventListener('click', async () => {
    if (!state.currentDate) return;

    try {
      const data = await api(`/api/plates?date=${encodeURIComponent(state.currentDate)}`);
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
      html += '<th>Plate</th><th>Mode</th><th>Parts</th><th>Verified</th><th>Files</th>';
      html += '</tr></thead><tbody>';

      data.plates.forEach(p => {
        const m = p.manifest || {};
        const verified = m.verification?.passed !== false
          ? '<span style="color:var(--emerald);">✓ PASS</span>'
          : '<span style="color:var(--red);">✕ FAIL</span>';
        html += `<tr>
          <td>${p.plate_name}</td>
          <td>${m.mode || '—'}</td>
          <td>${m.part_count || p.files?.length || '—'}</td>
          <td>${verified}</td>
          <td>${p.files?.length || 0} files</td>
        </tr>`;
      });

      html += '</tbody></table>';
      manifestContent.innerHTML = html;
      manifestOverlay.classList.add('manifest-overlay--visible');
    } catch (err) {
      // handled
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

    // Collisions
    const collOk = !v.has_hard_collisions;
    $('q-collisions').textContent = v.clearance_violations_count || 0;
    $('q-collisions').className = `quality-item__value quality-item__value--${collOk ? 'pass' : 'fail'}`;

    // Clearance buffer
    const clearOk = v.passed;
    $('q-clearance').textContent = clearOk ? '≥ 2.0' : '< 2.0';
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
      tab.textContent = plate.plate_name;
      tab.addEventListener('click', () => {
        state.activePlateIndex = i;
        plateTabs.querySelectorAll('.plate-tab').forEach(t => t.classList.remove('plate-tab--active'));
        tab.classList.add('plate-tab--active');
        renderPackedPlate(plate);
        updateSentryCards(state.packResult);
        updatePlateInfo(plate);
      });
      plateTabs.appendChild(tab);
    });

    updatePlateInfo(plates[0]);
  }

  function updatePlateInfo(plate) {
    plateInfo.innerHTML = `
      <strong>${plate.plate_name}</strong> • ${plate.mode.toUpperCase()} mode • ${plate.part_count} parts
    `;
  }

  function getActivePlate() {
    if (!state.packResult || !state.packResult.plates) return null;
    return state.packResult.plates[state.activePlateIndex] || state.packResult.plates[0];
  }

  // ── Load Existing Plates ───────────────────────────────────
  async function loadExistingPlates(date) {
    try {
      const data = await api(`/api/plates?date=${encodeURIComponent(date)}`);
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
        }));

        state.packResult = {
          success: true,
          plates,
          total_plates_generated: plates.length,
        };

        showPlates(plates);
        btnOpenNetfabb.disabled = false;
        btnDownload.disabled = false;
        btnManifest.disabled = false;

        // Update quality cards from first plate
        if (plates[0].verification) {
          updateSentryCards(state.packResult);
        }
      }
    } catch (err) {
      // Plates endpoint not critical
    }
  }

  // ── Bootstrap ──────────────────────────────────────────────
  async function init() {
    initThreeJS();

    // Load system status
    try {
      const status = await api('/api/status');
      const netfabbOk = status.netfabb_console_available;
      statusNetfabb.className = `status-badge status-badge--${netfabbOk ? 'ok' : 'warn'}`;
      statusNetfabbTx.textContent = netfabbOk ? 'Netfabb ✓' : 'Netfabb ✕';

      $('status-printer-text').textContent =
        `${status.envelope_mm.x} × ${status.envelope_mm.y} × ${status.envelope_mm.z} mm`;

      // Auto-fill today's date
      dateInput.value = status.today;
    } catch (err) {
      toast('Failed to connect to LP Agent backend', 'error');
    }
  }

  // Start
  init();

})();
