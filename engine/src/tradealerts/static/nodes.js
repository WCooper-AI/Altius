/* Trading Alerts web interface: The node view of the strategy editor. The rules themselves live in nodegraph.js
   (a pure model); this file draws that model as nodes and wires on a pannable, zoomable canvas and turns pointer
   gestures into calls on the model (connect, disconnect, newNode, removeNode), writing the result back into the
   strategy with applyGraph.
   ES modules: each file imports what it uses (boot.js is the page's entry, see web/app.py). */

import { append, clear, h, svg } from "./util.js";
import { OP_WORDS, isBlank } from "./describe.js";
import { leafFields, numberInput, operandEditor, choiceSelect } from "./editor.js";
import { applyGraph, buildGraph, canConnect, connect, disconnect, inputsOf, layout, loadGraph, newNode, nodeById,
  NODE_TYPES, removeNode, rulesSignature, usedNodes, wireInto } from "./nodegraph.js";
import { pref, savePref } from "./testing.js";

const MIN_SCALE = 0.35, MAX_SCALE = 1.75;
const LOOSE_TITLE = "Not connected to Entry or Exit, so it isn't part of the strategy yet";
const MENUS = ["Values", "Conditions", "Combine"];

/** The node view: the strategy's rules as nodes and wires on a canvas, instead of the list view's sections.
    opts: { data, onEdit, storeKey, state, sections: { market, risk, exitFields }, entryTools }. */
export function nodeView(opts) {
  const { data, onEdit, storeKey, state, sections, entryTools } = opts;
  let graph = null, scale = 1, panX = 60, panY = 60, selected = null, drag = null, rafQueued = false;
  const nodeEls = new Map(); // node id -> its .node element
  const resized = new ResizeObserver(() => queueRedraw()); // a node grew or shrank: its dots moved

  // ----- toolbar
  const addSel = h("select", { class: "wide", "aria-label": "Add a node" },
    h("option", { value: "", disabled: true }, "Add…"),
    MENUS.map((menu) => {
      const items = Object.entries(NODE_TYPES).filter(([, def]) => def.menu === menu);
      return items.length ? h("optgroup", { label: menu }, items.map(([t, def]) => h("option", { value: t }, def.label))) : null;
    }));
  addSel.addEventListener("change", () => { const type = addSel.value; addSel.value = ""; if (type) addNodeAt(type); });
  const toolbar = h("div", { class: "node-toolbar" }, addSel,
    h("button", { type: "button", onclick: () => tidyUp() }, "Tidy up"),
    h("span", { class: "spacer" }),
    h("button", { class: "quiet icon", type: "button", "aria-label": "Zoom out", onclick: () => zoomBy(1 / 1.2) }, "−"),
    h("button", { class: "quiet icon", type: "button", "aria-label": "Zoom in", onclick: () => zoomBy(1.2) }, "+"),
    h("button", { type: "button", onclick: () => fit() }, "Fit"));
  const hint = h("p", { class: "node-hint" }, "Drag from a dot on the right of a node to a dot on the left of another to connect them.");

  // ----- canvas
  const wires = svg("svg", { class: "wire-layer" });
  const world = h("div", { class: "node-world" }, wires);
  const canvasEl = h("div", { class: "node-canvas", tabindex: "0" }, world);
  const drawer = h("div", { class: "node-drawer", hidden: true });
  const root = h("div", { class: "node-editor" }, toolbar, hint, h("div", { class: "node-stage" }, canvasEl, drawer));

  canvasEl.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".node, .dot, path.wire")) return;
    select(null);
    startPan(e);
  });
  canvasEl.addEventListener("wheel", onWheel, { passive: false });
  canvasEl.addEventListener("keydown", onKeyDown);

  // ----- persisting the graph
  function persist() { if (storeKey) savePref(storeKey, JSON.stringify(graph)); }
  /** A node's own data changed (an operand, a comparison, a time/weekdays/news field): the wires didn't move. */
  function dataEdit() { applyGraph(graph, data); persist(); onEdit(); refreshLoose(); queueRedraw(); }
  /** A node or wire was added, removed, connected or disconnected: rebuild the canvas from the graph. */
  function structuralEdit() { applyGraph(graph, data); persist(); onEdit(); renderNodes(); }

  // ----- loading or building the graph (never applyGraph on first render)
  const sig = rulesSignature(data);
  let fresh = false;
  if (state.graph && state.graph.sig === sig) graph = state.graph;
  else {
    const stored = storeKey ? pref(storeKey, "") : "";
    graph = stored ? loadGraph(stored, data) : null;
    if (!graph) { graph = buildGraph(data); fresh = true; }
  }
  state.graph = graph;
  applyTransform();
  renderNodes();
  if (fresh) {
    requestAnimationFrame(() => {
      layout(graph, (id) => measure(id));
      placeAll();
      persist();
      fit();
    });
  } else requestAnimationFrame(() => fit());

  return root;

  // --------------------------------------------------------------------- drawing

  function measure(id) {
    const el = nodeEls.get(id);
    return el ? [el.offsetWidth, el.offsetHeight] : [220, 100];
  }
  function placeAll() {
    for (const n of graph.nodes) {
      const el = nodeEls.get(n.id);
      if (el) { el.style.left = n.x + "px"; el.style.top = n.y + "px"; }
    }
    redrawWires();
  }
  function applyTransform() { world.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`; }

  function renderNodes() {
    for (const child of Array.from(world.children)) if (child !== wires) child.remove();
    nodeEls.clear();
    resized.disconnect();
    const used = usedNodes(graph);
    for (const node of graph.nodes) world.appendChild(buildNode(node, used.has(node.id)));
    redrawWires();
  }
  function refreshLoose() {
    const used = usedNodes(graph);
    for (const [id, el] of nodeEls) {
      const loose = !used.has(id);
      el.classList.toggle("loose", loose);
      if (loose) el.title = LOOSE_TITLE; else el.removeAttribute("title");
    }
  }

  function dot(nodeId, port, kind, role) {
    const el = h("span", { class: "dot", "data-role": role, "data-node": nodeId, "data-port": port, "data-kind": kind,
      title: role === "output" ? "Drag to connect to another node" : "Drag an output dot here to connect, or drag this away to disconnect" });
    if (role === "output") el.addEventListener("pointerdown", (e) => startWireFrom(e, nodeId, null));
    else el.addEventListener("pointerdown", (e) => {
      const existing = wireInto(graph, nodeId, port);
      if (existing) startWireFrom(e, existing.from, { to: nodeId, port });
    });
    return el;
  }

  function inputRow(node, input, extra = null) {
    const wire = wireInto(graph, node.id, input.key);
    return h("div", { class: "node-row" }, dot(node.id, input.key, input.kind, "input"),
      h("span", { class: "row-label" }, input.label), wire ? null : extra);
  }

  function buildNode(node, used) {
    const def = NODE_TYPES[node.type];
    const el = h("div", { class: "node" + (used ? "" : " loose"), "data-type": node.type,
      style: { left: node.x + "px", top: node.y + "px" } });
    if (!used) el.title = LOOSE_TITLE;
    el.addEventListener("pointerdown", () => select({ kind: "node", id: node.id }));
    const head = h("div", { class: "node-head" }, h("span", { class: "node-title" }, def.label));
    if (!def.fixed) head.appendChild(h("button", { class: "quiet icon remove", type: "button",
      "aria-label": "Remove " + def.label, title: "Remove", onclick: (e) => { e.stopPropagation(); onRemoveNode(node.id); } }, "✕"));
    if (def.output) head.appendChild(dot(node.id, "out", def.output, "output"));
    head.addEventListener("pointerdown", (e) => startNodeDrag(e, node, el));
    const body = h("div", { class: "node-body" });
    fillBody(body, node);
    append(el, [head, body]);
    nodeEls.set(node.id, el);
    resized.observe(el);
    return el;
  }

  function fillBody(body, node) {
    switch (node.type) {
      case "value":
        body.appendChild(operandEditor(() => node.data, (v) => { node.data = v; }, dataEdit, "Price or indicator"));
        break;
      case "compare": {
        const side = (key, label) => inputRow(node, { key, kind: "value", label },
          numberInput(node.data[key], (v) => { node.data[key] = v; dataEdit(); },
            { cls: "narrow", step: "any", allowEmpty: true, emptyValue: null, label }));
        body.appendChild(side("left", "Left side"));
        body.appendChild(choiceSelect(Object.entries(OP_WORDS), node.data.op, (v) => { node.data.op = v; dataEdit(); }, { label: "Comparison" }));
        body.appendChild(side("right", "Right side"));
        break;
      }
      case "time": case "weekdays": case "news":
        append(body, leafFields(() => node.data, dataEdit));
        break;
      case "custom":
        append(body, [h("span", { class: "muted small" }, "Kept as written; edit it in YAML."),
          h("code", { class: "custom-card" }, JSON.stringify(node.data))]);
        break;
      case "not":
        body.appendChild(inputRow(node, inputsOf(graph, node)[0]));
        break;
      case "all": case "any":
        for (const input of inputsOf(graph, node)) body.appendChild(inputRow(node, input));
        break;
      case "entry":
        body.appendChild(inputRow(node, inputsOf(graph, node)[0]));
        body.appendChild(entryTools);
        break;
      case "exit":
        body.appendChild(sections.exitFields(data, onEdit));
        body.appendChild(inputRow(node, inputsOf(graph, node)[0]));
        break;
      case "market": {
        const summary = h("p", { class: "node-summary" });
        updateMarketSummary(summary);
        append(body, [summary, h("button", { type: "button",
          onclick: () => openDrawer(sections.market, summary, updateMarketSummary) }, "Edit…")]);
        break;
      }
      case "risk": {
        const summary = h("p", { class: "node-summary" });
        updateRiskSummary(summary);
        append(body, [summary, h("button", { type: "button",
          onclick: () => openDrawer(sections.risk, summary, updateRiskSummary) }, "Edit…")]);
        break;
      }
    }
  }

  function updateMarketSummary(el) {
    const m = data.market || {};
    const market = m.label || m.symbol || "No market chosen yet";
    const candle = m.timeframe ? `Candle size: ${m.timeframe}.` : "Candle size not chosen yet.";
    const dir = data.direction === "long" ? "Long." : data.direction === "short" ? "Short." : "Direction not chosen yet.";
    el.textContent = `${market}. ${candle} ${dir}`;
  }
  function updateRiskSummary(el) {
    const r = data.risk || {};
    const balance = isBlank(r.account_size) ? "not set" : `${r.account_size} ${r.currency || ""}`.trim();
    const risk = isBlank(r.risk_per_trade_pct) ? "not set" : r.risk_per_trade_pct + "%";
    el.textContent = `Balance: ${balance}. Risk per trade: ${risk}.`;
  }
  function openDrawer(section, summary, refresh) {
    clear(drawer);
    drawer.hidden = false;
    const close = h("button", { class: "quiet icon", type: "button", "aria-label": "Close", onclick: () => { drawer.hidden = true; } }, "✕");
    const cb = () => { onEdit(); refresh(summary); };
    append(drawer, [h("div", { class: "node-drawer-head" }, close), section(data, cb)]);
  }

  function onRemoveNode(id) {
    if (selected && selected.kind === "node" && selected.id === id) selected = null;
    if (removeNode(graph, id)) structuralEdit();
  }

  // --------------------------------------------------------------------- selection

  function select(sel) { selected = sel; highlightSelection(); }
  function highlightSelection() {
    for (const [id, el] of nodeEls) el.classList.toggle("selected", !!selected && selected.kind === "node" && selected.id === id);
    redrawWires();
  }

  // --------------------------------------------------------------------- wires

  function bezierD(x1, y1, x2, y2) {
    const dx = Math.max(40, Math.abs(x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }
  function dotCenter(el) {
    const wr = world.getBoundingClientRect(), dr = el.getBoundingClientRect();
    return [(dr.left + dr.width / 2 - wr.left) / scale, (dr.top + dr.height / 2 - wr.top) / scale];
  }
  function toWorld(clientX, clientY) {
    const wr = world.getBoundingClientRect();
    return [(clientX - wr.left) / scale, (clientY - wr.top) / scale];
  }
  function sizeWires() {
    let maxX = 1200, maxY = 800;
    for (const n of graph.nodes) {
      const [w, hgt] = measure(n.id);
      maxX = Math.max(maxX, n.x + w + 400);
      maxY = Math.max(maxY, n.y + hgt + 300);
    }
    wires.setAttribute("width", String(maxX));
    wires.setAttribute("height", String(maxY));
  }
  function drawWire(fromId, toId, port) {
    const fromDot = nodeEls.get(fromId)?.querySelector('[data-role="output"]');
    const toDot = nodeEls.get(toId)?.querySelector(`[data-role="input"][data-port="${port}"]`);
    const fromNode = nodeById(graph, fromId);
    if (!fromDot || !toDot || !fromNode) return;
    const [x1, y1] = dotCenter(fromDot), [x2, y2] = dotCenter(toDot);
    const sel = !!selected && selected.kind === "wire" && selected.to === toId && selected.port === port;
    const path = svg("path", { d: bezierD(x1, y1, x2, y2), class: `wire k-${NODE_TYPES[fromNode.type].output}` + (sel ? " selected" : "") });
    path.addEventListener("click", (e) => { e.stopPropagation(); select({ kind: "wire", to: toId, port }); });
    wires.appendChild(path);
  }
  function drawTemp(fromId, pointer) {
    const fromDot = nodeEls.get(fromId)?.querySelector('[data-role="output"]');
    const fromNode = nodeById(graph, fromId);
    if (!fromDot || !fromNode) return;
    const [x1, y1] = dotCenter(fromDot);
    wires.appendChild(svg("path", { d: bezierD(x1, y1, pointer[0], pointer[1]),
      class: `wire temp k-${NODE_TYPES[fromNode.type].output}` }));
  }
  function redrawWires() {
    sizeWires();
    clear(wires);
    for (const w of graph.wires) {
      if (drag && drag.hideWire && drag.hideWire.to === w.to && drag.hideWire.port === w.port) continue;
      drawWire(w.from, w.to, w.port);
    }
    if (drag && drag.tempFrom) drawTemp(drag.tempFrom, drag.pointer);
  }
  function queueRedraw() {
    if (rafQueued) return;
    rafQueued = true;
    requestAnimationFrame(() => { rafQueued = false; redrawWires(); });
  }

  // --------------------------------------------------------------------- pointer gestures

  function addNodeAt(type) {
    const rect = canvasEl.getBoundingClientRect();
    const n = addNodeAt.count = (addNodeAt.count || 0) + 1;
    const x = (rect.width / 2 - panX) / scale + (n % 6) * 26;
    const y = (rect.height / 2 - panY) / scale + (n % 6) * 26;
    newNode(graph, type, x, y);
    structuralEdit();
  }

  function startNodeDrag(e, node, el) {
    if (e.target.closest("button, input, select, textarea, .dot")) return;
    e.preventDefault();
    canvasEl.focus({ preventScroll: true });
    select({ kind: "node", id: node.id });
    const header = e.currentTarget;
    header.setPointerCapture(e.pointerId);
    const start = { x: e.clientX, y: e.clientY, nx: node.x, ny: node.y };
    const move = (ev) => {
      node.x = start.nx + (ev.clientX - start.x) / scale;
      node.y = start.ny + (ev.clientY - start.y) / scale;
      el.style.left = node.x + "px"; el.style.top = node.y + "px";
      queueRedraw();
    };
    const end = () => { cleanup(); persist(); };
    const cancel = () => { node.x = start.nx; node.y = start.ny; el.style.left = node.x + "px"; el.style.top = node.y + "px"; cleanup(); redrawWires(); };
    function cleanup() {
      header.removeEventListener("pointermove", move); header.removeEventListener("pointerup", end); header.removeEventListener("pointercancel", cancel);
      drag = null;
    }
    header.addEventListener("pointermove", move);
    header.addEventListener("pointerup", end);
    header.addEventListener("pointercancel", cancel);
    drag = { cancel };
  }

  function startPan(e) {
    canvasEl.setPointerCapture(e.pointerId);
    const start = { x: e.clientX, y: e.clientY, px: panX, py: panY };
    const move = (ev) => { panX = start.px + (ev.clientX - start.x); panY = start.py + (ev.clientY - start.y); applyTransform(); };
    const end = () => cleanup();
    const cancel = () => { panX = start.px; panY = start.py; applyTransform(); cleanup(); };
    function cleanup() {
      canvasEl.removeEventListener("pointermove", move); canvasEl.removeEventListener("pointerup", end); canvasEl.removeEventListener("pointercancel", cancel);
      drag = null;
    }
    canvasEl.addEventListener("pointermove", move);
    canvasEl.addEventListener("pointerup", end);
    canvasEl.addEventListener("pointercancel", cancel);
    drag = { cancel };
  }

  function markDroppable(fromId, show) {
    for (const el of canvasEl.querySelectorAll('[data-role="input"]')) {
      el.classList.toggle("can-drop", show && canConnect(graph, fromId, el.dataset.node, el.dataset.port));
    }
  }

  function startWireFrom(e, fromId, hideWire) {
    e.stopPropagation(); e.preventDefault();
    const dotEl = e.currentTarget;
    dotEl.setPointerCapture(e.pointerId);
    drag = { tempFrom: fromId, hideWire, pointer: toWorld(e.clientX, e.clientY) };
    markDroppable(fromId, true);
    redrawWires();
    const move = (ev) => { drag.pointer = toWorld(ev.clientX, ev.clientY); queueRedraw(); };
    const finish = (ev) => {
      const target = document.elementFromPoint(ev.clientX, ev.clientY);
      const inputDot = target && target.closest && target.closest('[data-role="input"]');
      let did = false;
      if (inputDot && canConnect(graph, fromId, inputDot.dataset.node, inputDot.dataset.port)) {
        if (hideWire) disconnect(graph, hideWire.to, hideWire.port);
        connect(graph, fromId, inputDot.dataset.node, inputDot.dataset.port);
        did = true;
      } else if (hideWire) { disconnect(graph, hideWire.to, hideWire.port); did = true; }
      cleanup();
      if (did) structuralEdit(); else redrawWires();
    };
    const cancel = () => { cleanup(); redrawWires(); };
    function cleanup() {
      dotEl.removeEventListener("pointermove", move); dotEl.removeEventListener("pointerup", finish); dotEl.removeEventListener("pointercancel", cancel);
      markDroppable(fromId, false);
      drag = null;
    }
    dotEl.addEventListener("pointermove", move);
    dotEl.addEventListener("pointerup", finish);
    dotEl.addEventListener("pointercancel", cancel);
    drag.cancel = cancel;
  }

  // --------------------------------------------------------------------- pan, zoom, fit, tidy

  function onWheel(e) {
    e.preventDefault();
    const rect = canvasEl.getBoundingClientRect(), cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    if (e.ctrlKey) {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * Math.exp(-e.deltaY * 0.0015)));
      const wx = (cx - panX) / scale, wy = (cy - panY) / scale;
      scale = next;
      panX = cx - wx * scale; panY = cy - wy * scale;
    } else { panX -= e.deltaX; panY -= e.deltaY; }
    applyTransform();
  }
  function zoomBy(factor) {
    const rect = canvasEl.getBoundingClientRect(), cx = rect.width / 2, cy = rect.height / 2;
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
    const wx = (cx - panX) / scale, wy = (cy - panY) / scale;
    scale = next;
    panX = cx - wx * scale; panY = cy - wy * scale;
    applyTransform();
  }
  function fit() {
    const rect = canvasEl.getBoundingClientRect();
    if (!graph.nodes.length || !rect.width || !rect.height) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of graph.nodes) {
      const [w, hgt] = measure(n.id);
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + w); maxY = Math.max(maxY, n.y + hgt);
    }
    const pad = 40;
    scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min((rect.width - pad * 2) / Math.max(1, maxX - minX),
      (rect.height - pad * 2) / Math.max(1, maxY - minY))));
    panX = pad - minX * scale + Math.max(0, rect.width - pad * 2 - (maxX - minX) * scale) / 2;
    panY = pad - minY * scale + Math.max(0, rect.height - pad * 2 - (maxY - minY) * scale) / 2;
    applyTransform();
    redrawWires();
  }
  function tidyUp() {
    layout(graph, (id) => measure(id));
    placeAll();
    persist();
  }

  // --------------------------------------------------------------------- keyboard

  function onKeyDown(e) {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (e.key === "Escape") { if (drag && drag.cancel) drag.cancel(); return; }
    if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault();
      if (selected.kind === "wire") { disconnect(graph, selected.to, selected.port); selected = null; structuralEdit(); }
      else if (selected.kind === "node") onRemoveNode(selected.id);
    }
  }
}
