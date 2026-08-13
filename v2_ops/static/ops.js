"use strict";

const state = { executions: [], selectedExecution: null, nodes: [], selectedNode: null, zoom: 1 };
const $ = (id) => document.getElementById(id);
const showJSON = (element, value) => { element.textContent = JSON.stringify(value, null, 2); };

async function getJSON(path) {
  const response = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function statusBadge(value) {
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = value;
  return badge;
}

function renderExecutions() {
  const root = $("execution-list");
  root.replaceChildren();
  for (const execution of state.executions) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "execution-card" + (execution.execution_id === state.selectedExecution ? " active" : "");
    const title = document.createElement("strong");
    title.textContent = execution.lead_id;
    const subtitle = document.createElement("small");
    subtitle.textContent = execution.execution_id;
    card.append(title, subtitle, statusBadge(execution.status));
    card.addEventListener("click", () => selectExecution(execution.execution_id));
    root.append(card);
  }
}

function nodePosition(index) {
  const column = index % 3;
  const row = Math.floor(index / 3);
  return { x: 55 + column * 270, y: 55 + row * 145 };
}

function renderCanvas() {
  const nodesRoot = $("nodes");
  const edgesRoot = $("edges");
  nodesRoot.replaceChildren();
  edgesRoot.replaceChildren();
  const positions = new Map();
  state.nodes.forEach((node, index) => {
    const position = nodePosition(index);
    positions.set(node.node_id, position);
    const element = document.createElement("button");
    element.type = "button";
    element.className = "node" + (node.node_id === state.selectedNode ? " selected" : "");
    element.style.left = `${position.x}px`;
    element.style.top = `${position.y}px`;
    const kind = document.createElement("div");
    kind.className = "kind";
    kind.textContent = node.node_type.replaceAll("_", " ");
    const ordinal = document.createElement("strong");
    ordinal.textContent = `#${node.ordinal} · tentativa ${node.attempt}`;
    const status = document.createElement("div");
    status.className = "status";
    status.textContent = node.status;
    element.append(kind, ordinal, status);
    element.addEventListener("click", () => selectNode(node.node_id));
    nodesRoot.append(element);
  });
  state.nodes.forEach((node, index) => {
    if (index === 0) return;
    const from = positions.get(state.nodes[index - 1].node_id);
    const to = positions.get(node.node_id);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", "edge");
    path.setAttribute("d", `M ${from.x + 178} ${from.y + 38} C ${from.x + 220} ${from.y + 38}, ${to.x - 42} ${to.y + 38}, ${to.x} ${to.y + 38}`);
    edgesRoot.append(path);
  });
  nodesRoot.style.transform = `scale(${state.zoom})`;
}

function selectNode(nodeId) {
  state.selectedNode = nodeId;
  const node = state.nodes.find((item) => item.node_id === nodeId);
  if (!node) return;
  $("node-title").textContent = node.node_type.replaceAll("_", " ");
  showJSON($("input-summary"), node.input_summary);
  showJSON($("output-summary"), node.output_summary);
  showJSON($("metadata"), node.technical_metadata);
  showJSON($("node-error"), node.error);
  $("input-full").hidden = true;
  $("output-full").hidden = true;
  $("load-full-input").hidden = !node.has_full_input;
  $("load-full-output").hidden = !node.has_full_output;
  renderCanvas();
}

async function selectExecution(executionId) {
  state.selectedExecution = executionId;
  const payload = await getJSON(`/ops/api/executions/${encodeURIComponent(executionId)}/nodes`);
  state.nodes = payload.nodes;
  state.selectedNode = state.nodes.length ? state.nodes[0].node_id : null;
  $("canvas-title").textContent = executionId;
  renderExecutions();
  renderCanvas();
  if (state.selectedNode) selectNode(state.selectedNode);
}

async function loadExecutions() {
  const params = new URLSearchParams();
  const lead = $("lead-search").value.trim();
  const status = $("status-filter").value;
  if (lead) params.set("lead_id", lead);
  if (status) params.set("status", status);
  const payload = await getJSON(`/ops/api/executions?${params.toString()}`);
  state.executions = payload.executions;
  renderExecutions();
  if (!state.selectedExecution && state.executions.length) await selectExecution(state.executions[0].execution_id);
}

async function loadFull(side) {
  if (!state.selectedExecution || !state.selectedNode) return;
  const payload = await getJSON(`/ops/api/executions/${encodeURIComponent(state.selectedExecution)}/nodes/${state.selectedNode}/full?side=${side}`);
  const target = side === "input" ? $("input-full") : $("output-full");
  showJSON(target, payload.value ?? { status: "not_recorded" });
  target.hidden = false;
}

function connectLive() {
  const source = new EventSource("/ops/api/events");
  source.addEventListener("ready", () => { $("live-state").textContent = "LIVE"; });
  source.addEventListener("change", () => { loadExecutions().catch(disconnected); });
  source.onerror = disconnected;
}

function disconnected() { $("live-state").textContent = "DESCONECTADO"; }

$("lead-search").addEventListener("change", () => loadExecutions().catch(disconnected));
$("status-filter").addEventListener("change", () => loadExecutions().catch(disconnected));
$("load-full-input").addEventListener("click", () => loadFull("input").catch(disconnected));
$("load-full-output").addEventListener("click", () => loadFull("output").catch(disconnected));
$("zoom-in").addEventListener("click", () => { state.zoom = Math.min(1.6, state.zoom + 0.1); renderCanvas(); });
$("zoom-out").addEventListener("click", () => { state.zoom = Math.max(0.5, state.zoom - 0.1); renderCanvas(); });
$("fit-canvas").addEventListener("click", () => { state.zoom = 1; $("execution-canvas").scrollTo(0, 0); renderCanvas(); });

loadExecutions().catch(disconnected);
connectLive();
setInterval(() => loadExecutions().catch(disconnected), 2000);
