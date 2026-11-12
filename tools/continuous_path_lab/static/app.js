const state = {
  shapes: [],
  file: null,
  fileInfo: null,
  previewModel: null,
  result: null,
  backendOnline: false,
  startPoint: null,
  endPoint: null,
  endpointTouched: false,
  endpointDrag: null,
  previewTimer: null,
};

const $ = (id) => document.getElementById(id);

const FALLBACK_SHAPES = [
  { name: "annulus", holes: 1 },
  { name: "c_shape", holes: 0 },
  { name: "dumbbell", holes: 0 },
  { name: "narrow_boundary_slot", holes: 1 },
  { name: "rectangle", holes: 0 },
  { name: "sharp_corner", holes: 0 },
  { name: "square_hole", holes: 1 },
  { name: "star", holes: 0 },
  { name: "thin_tab", holes: 0 },
  { name: "two_holes", holes: 2 },
];

const queryApi = new URLSearchParams(window.location.search).get("api");
const API_ORIGIN = queryApi || (window.location.port === "8765" ? "" : "http://127.0.0.1:8765");

function apiUrl(path) {
  return `${API_ORIGIN}${path}`;
}

async function fetchJson(path, options = {}) {
  const res = await fetch(apiUrl(path), options);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `${res.status} ${res.statusText}`);
  }
  return data;
}

function setBackendStatus(online, detail = "") {
  state.backendOnline = online;
  const text = $("backendText");
  text.textContent = online ? `Backend: ${API_ORIGIN || "same origin"}` : "Backend: offline";
  text.className = online ? "backendOk" : "backendFail";
  if (detail) {
    $("subStatus").textContent = detail;
  }
}

function setStatus(text, detail = "", ok = null) {
  const status = $("statusText");
  status.textContent = text;
  status.className = ok === null ? "" : ok ? "ok" : "fail";
  $("subStatus").textContent = detail;
}

function show(el, visible) {
  el.classList.toggle("hidden", !visible);
}

function sourceChanged() {
  const source = $("sourceType").value;
  show($("builtinRow"), source === "builtin");
  show($("fileRow"), source === "file");
  show($("polygonRow"), source === "polygon");
  show($("stlRows"), source === "file" && state.fileInfo && state.fileInfo.type === "stl");
  queuePreviewRefresh(true);
}

function algorithmChanged() {
  const isV7 = $("algorithm").value === "contour_tree_v7";
  show($("directionRow"), !isV7);
  show($("startFractionRow"), !isV7);
  show($("exitFractionRow"), !isV7);
  show($("attemptsRow"), !isV7);
  render();
}

async function loadShapes() {
  try {
    const data = await fetchJson("/api/shapes");
    state.shapes = data.shapes || FALLBACK_SHAPES;
    setBackendStatus(true);
  } catch (err) {
    state.shapes = FALLBACK_SHAPES;
    setBackendStatus(false, "Start the Python backend on port 8765 before generating.");
  }
  const select = $("shapeSelect");
  select.innerHTML = "";
  for (const shape of state.shapes) {
    const option = document.createElement("option");
    option.value = shape.name;
    option.textContent = `${shape.name}${shape.holes ? ` (${shape.holes} hole)` : ""}`;
    select.appendChild(option);
  }
  queuePreviewRefresh(true);
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function inspectSelectedFile(file) {
  const dataBase64 = arrayBufferToBase64(await file.arrayBuffer());
  state.file = { fileName: file.name, dataBase64 };
  state.fileInfo = null;
  setStatus("Inspecting file", file.name);
  const data = await fetchJson("/api/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.file),
  });
  setBackendStatus(true);
  state.fileInfo = data;
  if (data.type === "stl") {
    const zMin = Number(data.zMin || 0);
    const zMax = Number(data.zMax || 0);
    const h = Number($("layerHeight").value || 0.2);
    $("layerIndex").max = String(Math.max(0, Math.floor((zMax - zMin) / h)));
    setStatus("STL ready", `Z ${zMin.toFixed(3)} to ${zMax.toFixed(3)}, ${data.triangles} triangles`);
  } else {
    setStatus("DXF ready", `${data.outerPoints} outer points, ${data.holes} hole(s)`);
  }
  sourceChanged();
  queuePreviewRefresh(true);
}

function numeric(id) {
  return Number($(id).value);
}

function buildInputPayload() {
  const source = $("sourceType").value;
  const payload = {
    source,
  };

  if (source === "builtin") {
    payload.shape = $("shapeSelect").value;
  } else if (source === "polygon") {
    payload.polygon = $("polygonJson").value;
  } else if (source === "file") {
    if (!state.file) {
      throw new Error("Choose a DXF or STL file first.");
    }
    Object.assign(payload, state.file);
    if (state.fileInfo && state.fileInfo.type === "stl") {
      payload.layerHeight = numeric("layerHeight");
      payload.layerIndex = Math.max(0, Math.floor(numeric("layerIndex")));
    }
  }
  return payload;
}

function buildGeneratePayload() {
  const payload = buildInputPayload();
  const algorithm = $("algorithm").value;
  payload.options = {
    algorithm,
    lineWidth: numeric("lineWidth"),
    spacing: numeric("spacing"),
    grid: numeric("grid"),
    coverageGrid: 150,
    coverageThreshold: numeric("coverageThreshold"),
    overlapThreshold: numeric("overlapThreshold"),
    spacingWarningThreshold: Math.max(0, Math.floor(numeric("spacingWarningThreshold"))),
    maxLevels: 256,
    spacingTolerance: 0.25,
    startFraction: numeric("startFraction"),
    exitFraction: numeric("exitFraction"),
  };
  if (algorithm === "contour_tree_v7") {
    if (state.endpointTouched && state.startPoint && state.endPoint) {
      payload.options.startPoint = state.startPoint;
      payload.options.endPoint = state.endPoint;
    }
  } else {
    payload.options.direction = $("direction").value;
    payload.options.retryAttempts = Math.max(1, Math.floor(numeric("retryAttempts")));
  }
  return payload;
}

function pointInPolygon(point, polygon) {
  if (!polygon || polygon.length < 3) return false;
  const [x, y] = point;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if ((yi > y) !== (yj > y)) {
      const xAtY = ((xj - xi) * (y - yi)) / (yj - yi || 1e-12) + xi;
      if (x < xAtY) inside = !inside;
    }
  }
  return inside;
}

function modelContains(model, point) {
  return pointInPolygon(point, model.outer) && !(model.holes || []).some((hole) => pointInPolygon(point, hole));
}

function interiorPointNear(model, tx, ty) {
  const [minX, minY, maxX, maxY] = model.bounds;
  const preferred = [minX + (maxX - minX) * tx, minY + (maxY - minY) * ty];
  if (modelContains(model, preferred)) return preferred;
  let best = null;
  for (let y = 0; y <= 28; y += 1) {
    for (let x = 0; x <= 28; x += 1) {
      const p = [minX + (maxX - minX) * (x / 28), minY + (maxY - minY) * (y / 28)];
      if (!modelContains(model, p)) continue;
      const score = dist(p, preferred);
      if (!best || score < best.score) best = { score, point: p };
    }
  }
  return best ? best.point : [(minX + maxX) * 0.5, (minY + maxY) * 0.5];
}

function setDefaultEndpoints(model) {
  state.startPoint = interiorPointNear(model, 0.32, 0.48);
  state.endPoint = interiorPointNear(model, 0.72, 0.52);
  state.endpointTouched = false;
}

function queuePreviewRefresh(resetEndpoints = false) {
  window.clearTimeout(state.previewTimer);
  state.previewTimer = window.setTimeout(() => {
    refreshPreviewModel(resetEndpoints).catch((err) => {
      state.previewModel = null;
      state.result = null;
      render();
      if (state.backendOnline) setStatus("Preview error", err.message || String(err), false);
    });
  }, 120);
}

async function refreshPreviewModel(resetEndpoints = false) {
  if ($("sourceType").value === "file" && !state.file) {
    state.previewModel = null;
    state.result = null;
    render();
    return;
  }
  const payload = buildInputPayload();
  if (payload.source === "builtin" && !payload.shape) return;
  const data = await fetchJson("/api/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setBackendStatus(true);
  state.previewModel = data.model;
  state.result = null;
  if (state.previewModel && (resetEndpoints || !state.startPoint || !state.endPoint || !modelContains(state.previewModel, state.startPoint) || !modelContains(state.previewModel, state.endPoint))) {
    setDefaultEndpoints(state.previewModel);
  }
  const name = state.previewModel ? state.previewModel.name : "shape";
  setStatus("Shape ready", name);
  render();
}

async function generate() {
  const payload = buildGeneratePayload();
  setStatus("Generating", "Building contours and route");
  $("generateBtn").disabled = true;
  $("downloadJson").disabled = true;
  $("downloadSvg").disabled = true;
  try {
    const data = await fetchJson("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setBackendStatus(true);
    state.result = data;
    $("downloadJson").disabled = false;
    $("downloadSvg").disabled = false;
    render();
    const m = data.metrics || {};
    setStatus(data.ok ? "Pass" : "Needs inspection", `${m.pathPoints || 0} points, coverage ${fmt(m.coverageRatio)}`, data.ok);
  } finally {
    $("generateBtn").disabled = false;
  }
}

function fmt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toFixed(3);
}

function boundsWithMargin(bounds, marginRatio = 0.08) {
  const [minX, minY, maxX, maxY] = bounds;
  const w = Math.max(maxX - minX, 1);
  const h = Math.max(maxY - minY, 1);
  const m = Math.max(w, h) * marginRatio;
  return [minX - m, minY - m, maxX + m, maxY + m];
}

function pts(points, closed = false) {
  const source = closed && points.length ? [...points, points[0]] : points;
  return source.map((p) => `${p[0].toFixed(4)},${(-p[1]).toFixed(4)}`).join(" ");
}

function el(name, attrs = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, String(value));
  }
  if (text) {
    node.textContent = text;
  }
  return node;
}

function addPolyline(svg, points, attrs, closed = false) {
  if (!points || points.length < 2) return;
  svg.appendChild(el("polyline", { points: pts(points, closed), fill: "none", "vector-effect": "non-scaling-stroke", ...attrs }));
}

function dist(a, b) {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

function lerpPoint(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function progressFraction() {
  const slider = $("progress");
  return Number(slider.value) / Number(slider.max || 10000);
}

function updateProgressText() {
  const text = $("progressText");
  if (text) {
    text.textContent = `${(progressFraction() * 100).toFixed(2)}%`;
  }
}

function pathAtProgress(path, fraction) {
  if (!path || path.length === 0) return [];
  if (path.length === 1 || fraction <= 0) return [path[0]];
  if (fraction >= 1) return path.slice();

  const lengths = [];
  let total = 0;
  for (let i = 0; i < path.length - 1; i += 1) {
    const length = dist(path[i], path[i + 1]);
    lengths.push(length);
    total += length;
  }
  if (total <= 0) return [path[0]];

  const target = total * fraction;
  let travelled = 0;
  const visible = [path[0]];
  for (let i = 0; i < lengths.length; i += 1) {
    const length = lengths[i];
    if (length <= 0) continue;
    if (travelled + length >= target) {
      const t = (target - travelled) / length;
      visible.push(lerpPoint(path[i], path[i + 1], t));
      return visible;
    }
    visible.push(path[i + 1]);
    travelled += length;
  }
  return visible;
}

function render() {
  updateProgressText();
  const result = state.result;
  const model = result && result.model ? result.model : state.previewModel;
  const svg = $("preview");
  svg.innerHTML = "";
  if (!model) {
    renderEmptyPreview(svg);
    renderMetrics();
    renderDiagnostics();
    return;
  }

  const [minX, minY, maxX, maxY] = boundsWithMargin(model.bounds);
  svg.setAttribute("viewBox", `${minX} ${-maxY} ${maxX - minX} ${maxY - minY}`);

  addPolyline(svg, model.outer, { stroke: "#202320", "stroke-width": 2.2 }, true);
  for (const hole of model.holes || []) {
    addPolyline(svg, hole, { stroke: "#59605a", "stroke-width": 1.8, "stroke-dasharray": "4 3" }, true);
  }

  for (const cut of (result && result.cutPaths) || []) {
    addPolyline(svg, cut.points, {
      stroke: cut.kind === "start" ? "#16803c" : "#7b4db8",
      "stroke-width": 1.2,
      "stroke-dasharray": "2 2",
      opacity: 0.75,
    });
  }

  if (result && $("showContours").checked) {
    for (const contour of result.contours || []) {
      const color = contour.level % 2 === 0 ? "#7da1c4" : "#91ad74";
      addPolyline(svg, contour.points, { stroke: color, "stroke-width": 0.85, opacity: 0.58 }, true);
    }
  }

  if (result && $("showTree").checked) {
    for (const edge of result.routeEdges || []) {
      svg.appendChild(el("line", {
        x1: edge.a[0],
        y1: -edge.a[1],
        x2: edge.b[0],
        y2: -edge.b[1],
        stroke: edge.kind === "final" ? "#7b4db8" : "#d18b2c",
        "stroke-width": 1.4,
        "stroke-dasharray": edge.kind === "final" ? "none" : "3 3",
        "vector-effect": "non-scaling-stroke",
        opacity: 0.85,
      }));
    }
  }

  if (result) renderCoverageAudit(svg, result.coverageAudit);

  const path = (result && result.path) || [];
  const visiblePath = pathAtProgress(path, progressFraction());
  addPolyline(svg, visiblePath, { stroke: "#cf2e1f", "stroke-width": 2.1, "stroke-linecap": "round", "stroke-linejoin": "round" });

  if (path.length && visiblePath.length) {
    const start = path[0];
    const end = visiblePath[visiblePath.length - 1];
    svg.appendChild(el("circle", { cx: start[0], cy: -start[1], r: 0.9, fill: "#16803c", stroke: "#ffffff", "stroke-width": 0.35 }));
    svg.appendChild(el("circle", { cx: end[0], cy: -end[1], r: 0.9, fill: "#7b4db8", stroke: "#ffffff", "stroke-width": 0.35 }));
  }

  renderEndpointHandles(svg, model);
  renderMetrics();
  renderDiagnostics();
  renderQuickStats();
}

function renderEndpointHandles(svg, model) {
  if (!state.startPoint || !state.endPoint) setDefaultEndpoints(model);
  const [minX, minY, maxX, maxY] = model.bounds;
  const r = Math.max(Math.max(maxX - minX, maxY - minY) * 0.012, 1.0);
  const handles = [
    ["start", state.startPoint, "#16803c"],
    ["end", state.endPoint, "#7b4db8"],
  ];
  for (const [kind, point, fill] of handles) {
    const handle = el("circle", {
      cx: point[0],
      cy: -point[1],
      r,
      fill,
      stroke: "#ffffff",
      "stroke-width": r * 0.22,
      "vector-effect": "non-scaling-stroke",
      cursor: "move",
      "data-endpoint": kind,
    });
    svg.appendChild(handle);
  }
}

function renderCoverageAudit(svg, audit) {
  if (!audit) return;
  const radius = Math.max(Number(audit.cellSize || 0.6) * 0.45, 0.25);
  if ($("showUnderfill").checked) {
    for (const p of audit.underfillSamples || []) {
      svg.appendChild(el("circle", {
        cx: p[0],
        cy: -p[1],
        r: radius,
        fill: "#33bde8",
        opacity: 0.72,
      }));
    }
  }
  if ($("showOverlap").checked) {
    for (const p of audit.overlapSamples || []) {
      svg.appendChild(el("circle", {
        cx: p[0],
        cy: -p[1],
        r: radius,
        fill: "#d99a24",
        opacity: 0.55,
      }));
    }
  }
}

function renderMetrics() {
  if (!state.result) {
    $("metrics").innerHTML = [
      ["algorithm", "-"],
      ["path points", "-"],
      ["coverage", "-"],
      ["intersections", "-"],
      ["missed contours", "-"],
    ].map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${v}</dd>`).join("");
    renderQuickStats();
    return;
  }
  const m = (state.result && state.result.metrics) || {};
  const rows = [
    ["algorithm", state.result.algorithm || "-"],
    ["path points", m.pathPoints],
    ["path length", fmt(m.pathLength)],
    ["coverage", fmt(m.coverageRatio)],
    ["underfill", fmt(m.underfillRatio)],
    ["internal overlap", fmt(m.internalOverlapRatio)],
    ["underfill cells", m.strictUnderfillCells],
    ["underfill groups", m.underfillComponents],
    ["largest underfill", m.largestUnderfillComponent],
    ["overlap cells", m.strictOverlapCells],
    ["overlap groups", m.overlapComponents],
    ["outside overfill", fmt(m.overfillRatio)],
    ["legacy raster", fmt(m.legacyCoverageRatio)],
    ["intersections", m.selfIntersections],
    ["spacing warnings", m.spacingViolations],
    ["bead overlaps", m.beadOverlapViolations],
    ["bead crossings", m.extrusionCrossings],
    ["turnbacks", m.turnbackViolations],
    ["bead clearance", fmt(m.minBeadClearance)],
    ["max turn deg", fmt(m.maxTurnAngleDegrees)],
    ["guard rejects", m.physicalGuardRejected],
    ["semantic ignored", m.semanticIgnoredSpacingPairs],
    ["missed contours", m.missedContourCount],
    ["containment", m.containmentViolations],
    ["gap contours", m.residualGapContours],
    ["gap spirals", m.residualGapSpirals],
    ["width-fair segments", m.widthFairingSegments],
    ["max segment width", fmt(m.maxSegmentWidth)],
    ["underfill detours", m.underfillDetours],
    ["safe detours", m.safeUnderfillDetours],
    ["safe rejects", m.safeUnderfillDetourRejected],
    ["component stitches", m.componentStitches],
    ["stitch rejects", m.componentStitchRejected],
    ["endpoint cuts", m.endpointCutSegments],
    ["attempts", m.attemptCount],
    ["selected attempt", m.selectedAttempt],
    ["tree roots", m.treeRoots],
    ["elapsed s", fmt(m.elapsedSeconds)],
  ];
  $("metrics").innerHTML = rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v ?? "-"))}</dd>`).join("");
}

function renderDiagnostics() {
  const diagnostics = (state.result && state.result.diagnostics) || [];
  $("diagnostics").innerHTML = diagnostics.length
    ? diagnostics.map((d) => `<li>${escapeHtml(d)}</li>`).join("")
    : "<li>No diagnostics.</li>";
}

function renderQuickStats() {
  const quick = $("quickStats");
  if (!state.result || !state.result.metrics) {
    quick.textContent = "";
    return;
  }
  const m = state.result.metrics;
  quick.innerHTML = `
    <span>${escapeHtml(String(m.pathPoints || 0))} pts</span>
    <span>${fmt(m.coverageRatio)} coverage</span>
    <span>${fmt(m.internalOverlapRatio)} overlap</span>
    <span>${escapeHtml(String(m.selfIntersections || 0))} crosses</span>
    <span>${escapeHtml(String(m.extrusionViolationCount || 0))} bead</span>
  `;
}

function renderEmptyPreview(svg) {
  svg.setAttribute("viewBox", "0 0 100 70");
  svg.appendChild(el("rect", { x: 0, y: 0, width: 100, height: 70, fill: "#fbfcfa" }));
  svg.appendChild(el("path", {
    d: "M18 45 C24 20 43 18 50 35 S73 52 82 24",
    fill: "none",
    stroke: "#c8d2c5",
    "stroke-width": 2.2,
    "stroke-linecap": "round",
  }));
  svg.appendChild(el("text", {
    x: 50,
    y: 58,
    "text-anchor": "middle",
    fill: "#6f776f",
    "font-size": 5,
    "font-family": "system-ui, sans-serif",
  }, "Choose an input, then generate a toolpath"));
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function download(name, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadJson() {
  if (!state.result) return;
  download("continuous-path-result.json", JSON.stringify(state.result, null, 2), "application/json");
}

function downloadSvg() {
  const svg = $("preview").cloneNode(true);
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  download("continuous-path-preview.svg", `<?xml version="1.0" encoding="UTF-8"?>\n${svg.outerHTML}\n`, "image/svg+xml");
}

function svgPointFromEvent(event) {
  const svg = $("preview");
  const matrix = svg.getScreenCTM();
  if (!matrix) return null;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const mapped = point.matrixTransform(matrix.inverse());
  return [mapped.x, -mapped.y];
}

function updateDraggedEndpoint(event) {
  if (!state.endpointDrag) return;
  const point = svgPointFromEvent(event);
  const model = state.previewModel || (state.result && state.result.model);
  if (!point || !model) return;
  if (state.endpointDrag === "start") {
    state.startPoint = point;
  } else {
    state.endPoint = point;
  }
  state.endpointTouched = true;
  state.result = null;
  render();
}

function previewPointerDown(event) {
  const endpoint = event.target && event.target.getAttribute("data-endpoint");
  if (!endpoint) return;
  state.endpointDrag = endpoint;
  $("preview").setPointerCapture(event.pointerId);
  updateDraggedEndpoint(event);
  event.preventDefault();
}

function previewPointerMove(event) {
  if (!state.endpointDrag) return;
  updateDraggedEndpoint(event);
  event.preventDefault();
}

function previewPointerUp(event) {
  if (!state.endpointDrag) return;
  state.endpointDrag = null;
  try {
    $("preview").releasePointerCapture(event.pointerId);
  } catch (_err) {
    // Pointer capture may already be released by the browser.
  }
  event.preventDefault();
}

function bindEvents() {
  $("sourceType").addEventListener("change", sourceChanged);
  $("algorithm").addEventListener("change", algorithmChanged);
  $("shapeSelect").addEventListener("change", () => queuePreviewRefresh(true));
  $("polygonJson").addEventListener("input", () => queuePreviewRefresh(true));
  $("fileInput").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      await inspectSelectedFile(file);
    } catch (err) {
      setStatus("File error", err.message || String(err), false);
    }
  });
  $("layerHeight").addEventListener("change", () => {
    if (state.fileInfo && state.fileInfo.type === "stl") {
      const h = Math.max(0.01, numeric("layerHeight"));
      $("layerIndex").max = String(Math.max(0, Math.floor((state.fileInfo.zMax - state.fileInfo.zMin) / h)));
      queuePreviewRefresh(true);
    }
  });
  $("layerIndex").addEventListener("change", () => queuePreviewRefresh(true));
  $("generateBtn").addEventListener("click", () => {
    generate().catch((err) => setStatus("Generation error", err.message || String(err), false));
  });
  $("showContours").addEventListener("change", render);
  $("showTree").addEventListener("change", render);
  $("showUnderfill").addEventListener("change", render);
  $("showOverlap").addEventListener("change", render);
  $("progress").addEventListener("input", render);
  $("downloadJson").addEventListener("click", downloadJson);
  $("downloadSvg").addEventListener("click", downloadSvg);
  $("preview").addEventListener("pointerdown", previewPointerDown);
  $("preview").addEventListener("pointermove", previewPointerMove);
  $("preview").addEventListener("pointerup", previewPointerUp);
  $("preview").addEventListener("pointercancel", previewPointerUp);
}

async function boot() {
  bindEvents();
  algorithmChanged();
  sourceChanged();
  await loadShapes();
  renderMetrics();
  renderDiagnostics();
  render();
}

boot().catch((err) => setStatus("Startup error", err.message || String(err), false));
