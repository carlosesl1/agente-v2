"use strict";

const state = {
  range: "7d",
  snapshot: null,
  selectedExecution: null,
  nodes: [],
  selectedNode: null,
  statusFilter: "",
  completenessFilter: "",
  leadFilter: "",
  zoom: 1,
  connected: false,
  dashboardEpoch: 0,
  detailEpoch: 0,
  fullEpoch: { input: 0, output: 0 },
  dashboardError: null,
  liveError: null,
  mobileNavigationOpen: false,
  operatorMenuOpen: false,
  dashboardLoading: false,
  drawerOpen: false,
  detailTrigger: null,
};

const KPI_DEFINITIONS = [
  ["executions", "Execuções", "◎", "Eventos recebidos no período", "neutral"],
  ["distinct_leads", "Leads distintos", "◇", "IDs distintos no período", "neutral"],
  ["in_progress", "Em andamento", "◌", "Pending, running e stale", "active"],
  ["completed", "Concluídas", "✓", "Conclusão técnica", "success"],
  ["failed", "Falhas", "!", "Estado técnico failed", "danger"],
  ["manual_review", "Revisão manual", "↗", "Estado manual_review", "warning"],
  ["technical_completion_rate", "Conclusão técnica", "%", "Concluídas sobre execuções", "success"],
  ["average_terminal_duration_ms", "Duração média terminal", "◷", "Apenas execuções terminais", "neutral"],
];
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const mobileNavigationMedia = window.matchMedia("(max-width: 720px)");

function showJSON(element, value) {
  element.textContent = JSON.stringify(value, null, 2);
}

function renderAlert() {
  const alert = $("dashboard-alert");
  const messages = [state.dashboardError, state.liveError].filter(Boolean);
  alert.textContent = messages.join(" ");
  alert.hidden = messages.length === 0;
}

function setMobileNavigationOpen(open, restoreFocus = false) {
  state.mobileNavigationOpen = Boolean(open);
  const sidebar = $("app-sidebar");
  sidebar.classList.toggle("mobile-open", state.mobileNavigationOpen);
  $("sidebar-backdrop").hidden = !state.mobileNavigationOpen;
  $("mobile-menu").setAttribute("aria-expanded", String(state.mobileNavigationOpen));
  const sidebarClosed = mobileNavigationMedia.matches && !state.mobileNavigationOpen;
  if (sidebarClosed) {
    sidebar.setAttribute("inert", "");
    sidebar.setAttribute("aria-hidden", "true");
  } else {
    sidebar.removeAttribute("inert");
    sidebar.removeAttribute("aria-hidden");
  }
  if (sidebarClosed && restoreFocus) $("mobile-menu").focus();
}

function setOperatorMenuOpen(open, restoreFocus = false) {
  state.operatorMenuOpen = Boolean(open);
  $("operator-popover").hidden = !state.operatorMenuOpen;
  $("operator-menu").setAttribute("aria-expanded", String(state.operatorMenuOpen));
  if (!state.operatorMenuOpen && restoreFocus) $("operator-menu").focus();
}

async function getJSON(path) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) {
    window.location.assign("/ops/login");
    throw new Error("authentication_required");
  }
  if (response.status === 503) {
    const error = new Error("source_unavailable");
    error.code = "source_unavailable";
    throw error;
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

function formatDuration(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "Não registrado";
  }
  const milliseconds = Math.max(0, Number(value));
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(1)} s`;
}

function formatMetric(key, value) {
  if (value === null || value === undefined) return "—";
  if (key === "technical_completion_rate") return `${Number(value).toFixed(1)}%`;
  if (key === "average_terminal_duration_ms") return formatDuration(value);
  return String(value);
}

function formatTimestamp(value) {
  if (!value) return "Não registrado";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "Não registrado";
  return timestamp.toLocaleString("pt-BR", { timeZone: "UTC" });
}

function displayValue(value) {
  return value === null || value === undefined || value === "" ? "Não registrado" : String(value);
}

function shortExecutionId(value) {
  const identifier = String(value);
  return identifier.length > 18 ? `${identifier.slice(0, 8)}…${identifier.slice(-7)}` : identifier;
}

function statusBadge(value) {
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = displayValue(value);
  return badge;
}

function emptyMessage(root, message) {
  const paragraph = document.createElement("p");
  paragraph.className = "empty-chart";
  paragraph.textContent = message;
  root.replaceChildren(paragraph);
}

function createSparkline(points) {
  if (!points.length) return null;
  const width = 66;
  const height = 23;
  const maximum = Math.max(1, ...points.map((point) => Math.max(0, Number(point.count) || 0)));
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("class", "sparkline");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Execuções no período");
  const polyline = document.createElementNS(SVG_NAMESPACE, "polyline");
  const coordinates = points.map((point, index) => {
    const x = points.length === 1 ? width / 2 : (index / (points.length - 1)) * width;
    const count = Math.max(0, Number(point.count) || 0);
    const y = height - 2 - (count / maximum) * (height - 4);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  polyline.setAttribute("points", coordinates.join(" "));
  polyline.setAttribute("fill", "none");
  svg.append(polyline);
  return svg;
}

function renderKpis() {
  const root = $("kpi-grid");
  const metrics = state.snapshot ? state.snapshot.metrics : {};
  const points = state.snapshot ? state.snapshot.execution_series : [];
  const cards = KPI_DEFINITIONS.map(([key, label, icon, description, tone]) => {
    const card = document.createElement("article");
    card.className = `kpi-card ${tone}`;
    const top = document.createElement("div");
    top.className = "kpi-top";
    const iconElement = document.createElement("span");
    iconElement.className = "kpi-icon";
    iconElement.setAttribute("aria-hidden", "true");
    iconElement.textContent = icon;
    const heading = document.createElement("span");
    heading.className = "kpi-label";
    heading.textContent = label;
    top.append(iconElement, heading);
    const value = document.createElement("strong");
    value.className = "kpi-value";
    value.textContent = formatMetric(key, metrics[key]);
    const foot = document.createElement("div");
    foot.className = "kpi-foot";
    const descriptionElement = document.createElement("span");
    descriptionElement.textContent = description;
    foot.append(descriptionElement);
    const series = document.createElement("div");
    series.className = "kpi-series";
    const note = document.createElement("span");
    note.textContent = "Execuções no período";
    series.append(note);
    const sparkline = createSparkline(points);
    if (sparkline) series.append(sparkline);
    foot.append(series);
    card.append(top, value, foot);
    return card;
  });
  root.replaceChildren(...cards);
}

function renderExecutionSeries() {
  const root = $("execution-series");
  const points = state.snapshot ? state.snapshot.execution_series : [];
  if (!points.length || points.every((point) => Number(point.count) === 0)) {
    emptyMessage(root, "Nenhuma execução registrada.");
    return;
  }
  const width = 640;
  const height = 220;
  const padding = 28;
  const maximum = Math.max(1, ...points.map((point) => Math.max(0, Number(point.count) || 0)));
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Série de execuções no período");
  const polyline = document.createElementNS(SVG_NAMESPACE, "polyline");
  const coordinates = points.map((point, index) => {
    const denominator = Math.max(1, points.length - 1);
    const x = padding + (index / denominator) * (width - padding * 2);
    const count = Math.max(0, Number(point.count) || 0);
    const y = height - padding - (count / maximum) * (height - padding * 2);
    const marker = document.createElementNS(SVG_NAMESPACE, "circle");
    marker.setAttribute("cx", x.toFixed(2));
    marker.setAttribute("cy", y.toFixed(2));
    marker.setAttribute("r", "4");
    const title = document.createElementNS(SVG_NAMESPACE, "title");
    title.textContent = `${formatTimestamp(point.start_at)}: ${count}`;
    marker.append(title);
    svg.append(marker);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  polyline.setAttribute("points", coordinates.join(" "));
  polyline.setAttribute("fill", "none");
  svg.prepend(polyline);
  root.replaceChildren(svg);
}

function labeledBar(label, count, maximum, suffix) {
  const row = document.createElement("div");
  row.className = "bar-row";
  const heading = document.createElement("span");
  heading.textContent = label;
  const track = document.createElement("div");
  track.className = "bar-track";
  const bar = document.createElement("span");
  bar.className = "bar-value";
  const safeCount = Math.max(0, Number(count) || 0);
  const percent = maximum > 0 ? Math.min(100, (safeCount / maximum) * 100) : 0;
  bar.style.width = `${percent.toFixed(2)}%`;
  track.append(bar);
  const value = document.createElement("strong");
  value.textContent = suffix ? `${safeCount} ${suffix}` : String(safeCount);
  row.append(heading, track, value);
  return row;
}

function renderDistribution(rootId, items, key) {
  const root = $(rootId);
  if (!items.length || items.every((item) => Number(item.count) === 0)) {
    emptyMessage(root, "Nenhum registro no período.");
    return;
  }
  const maximum = Math.max(1, ...items.map((item) => Math.max(0, Number(item.count) || 0)));
  root.replaceChildren(...items.map((item) => labeledBar(displayValue(item[key]), item.count, maximum, "")));
}

function renderMilestones() {
  const root = $("milestones-chart");
  const items = state.snapshot ? state.snapshot.milestones : [];
  if (!items.length || items.every((item) => Number(item.count) === 0)) {
    emptyMessage(root, "Nenhum marco registrado.");
    return;
  }
  const maximum = Math.max(1, ...items.map((item) => Math.max(0, Number(item.count) || 0)));
  root.replaceChildren(
    ...items.map((item) => labeledBar(displayValue(item.milestone), item.count, maximum, "execuções com marco")),
  );
}

function renderTopNodeTypes() {
  const root = $("top-node-types");
  const items = state.snapshot ? state.snapshot.top_node_types : [];
  if (!items.length) {
    emptyMessage(root, "Nenhum nó registrado.");
    return;
  }
  const maximum = Math.max(1, ...items.map((item) => Math.max(0, Number(item.count) || 0)));
  root.replaceChildren(
    ...items.map((item, index) => labeledBar(`${index + 1}. ${displayValue(item.node_type)}`, item.count, maximum, "")),
  );
}

function filteredExecutions() {
  const executions = state.snapshot ? state.snapshot.executions : [];
  return executions.filter((execution) => (
    (!state.leadFilter || execution.lead_id === state.leadFilter)
    && (!state.statusFilter || execution.status === state.statusFilter)
    && (!state.completenessFilter || execution.trace_completeness === state.completenessFilter)
  ));
}

function milestoneText(execution) {
  const facts = [
    ["reserva", execution.has_reservation],
    ["pagamento", execution.has_payment],
    ["entrega pública", execution.has_public_delivery],
    ["handoff", execution.has_handoff],
  ];
  return facts.map(([label, present]) => `${label}: ${present ? "Sim" : "Não"}`).join(" · ");
}

function executionFacts(execution) {
  return [
    ["Lead ID", displayValue(execution.lead_id)],
    ["Execução", shortExecutionId(execution.execution_id)],
    ["Recebida", formatTimestamp(execution.received_at)],
    ["Duração", formatDuration(execution.duration_ms)],
    ["Estado", displayValue(execution.status)],
    ["Completude", displayValue(execution.trace_completeness)],
    ["Nó atual", displayValue(execution.current_node_type)],
    ["Nós", String(Number(execution.node_count) || 0)],
    ["Marcos", milestoneText(execution)],
    ["Motivo terminal", displayValue(execution.terminal_reason)],
  ];
}

function executionLink(execution) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "execution-link";
  button.textContent = shortExecutionId(execution.execution_id);
  button.setAttribute("title", String(execution.execution_id));
  button.addEventListener("click", () => openExecution(execution.execution_id).catch(handleDashboardError));
  return button;
}

function renderExecutionTable() {
  const tableRoot = $("execution-table-body");
  const mobileRoot = $("execution-mobile-list");
  const executions = filteredExecutions();
  const rows = [];
  const cards = [];
  for (const execution of executions) {
    const facts = executionFacts(execution);
    const row = document.createElement("tr");
    facts.forEach(([, value], index) => {
      const cell = document.createElement("td");
      if (index === 1) cell.append(executionLink(execution));
      else if (index === 4) cell.append(statusBadge(value));
      else cell.textContent = value;
      row.append(cell);
    });
    rows.push(row);

    const card = document.createElement("article");
    card.className = "execution-mobile-card";
    for (const [label, value] of facts) {
      const fact = document.createElement("div");
      const heading = document.createElement("strong");
      heading.textContent = label;
      if (label === "Execução") fact.append(heading, executionLink(execution));
      else {
        const content = document.createElement("span");
        content.textContent = value;
        fact.append(heading, content);
      }
      card.append(fact);
    }
    cards.push(card);
  }
  tableRoot.replaceChildren(...rows);
  mobileRoot.replaceChildren(...cards);
  const empty = $("empty-state");
  empty.hidden = executions.length !== 0;
  empty.textContent = state.snapshot && state.snapshot.executions.length
    ? "Nenhuma execução corresponde aos filtros exatos."
    : "Nenhuma execução registrada neste período.";
}

function populateFilter(select, items, key, allLabel, selectedValue) {
  const options = [];
  const all = document.createElement("option");
  all.value = "";
  all.textContent = allLabel;
  options.push(all);
  for (const item of items) {
    const option = document.createElement("option");
    option.value = String(item[key]);
    option.textContent = String(item[key]);
    options.push(option);
  }
  select.replaceChildren(...options);
  select.value = selectedValue;
}

function renderDashboard() {
  if (!state.snapshot) return;
  $("generated-at").textContent = `Atualizado em ${formatTimestamp(state.snapshot.generated_at)} UTC`;
  renderKpis();
  renderExecutionSeries();
  renderDistribution("status-distribution", state.snapshot.status_distribution, "status");
  renderDistribution("trace-distribution", state.snapshot.trace_distribution, "trace_completeness");
  renderMilestones();
  renderTopNodeTypes();
  populateFilter($("status-filter"), state.snapshot.status_distribution, "status", "Todos", state.statusFilter);
  populateFilter(
    $("completeness-filter"),
    state.snapshot.trace_distribution,
    "trace_completeness",
    "Todas",
    state.completenessFilter,
  );
  renderExecutionTable();
}

async function loadDashboard() {
  const requestedRange = state.range;
  const epoch = ++state.dashboardEpoch;
  let payload;
  try {
    payload = await getJSON(`/ops/api/dashboard?range=${encodeURIComponent(requestedRange)}`);
  } catch (error) {
    if (epoch !== state.dashboardEpoch || requestedRange !== state.range) {
      error.superseded = true;
      throw error;
    }
    if (error && error.message !== "authentication_required") {
      state.dashboardError = error && error.code === "source_unavailable"
        ? "Fonte operacional indisponível. Os últimos dados recebidos permanecem visíveis."
        : "Não foi possível atualizar os dados operacionais.";
      renderAlert();
      error.dashboardHandled = true;
    }
    throw error;
  }
  if (epoch !== state.dashboardEpoch || requestedRange !== state.range) return false;
  state.snapshot = payload;
  state.dashboardError = null;
  renderAlert();
  renderDashboard();
  return true;
}

async function refreshDashboard() {
  if (state.dashboardLoading) return;
  state.dashboardLoading = true;
  $("refresh-dashboard").disabled = true;
  try {
    await loadDashboard();
  } finally {
    state.dashboardLoading = false;
    $("refresh-dashboard").disabled = false;
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
    element.className = `node${node.node_id === state.selectedNode ? " selected" : ""}`;
    element.style.left = `${position.x}px`;
    element.style.top = `${position.y}px`;
    const kind = document.createElement("div");
    kind.className = "kind";
    kind.textContent = displayValue(node.node_type).replaceAll("_", " ");
    const ordinal = document.createElement("strong");
    ordinal.textContent = `#${Number(node.ordinal) || 0} · tentativa ${Number(node.attempt) || 0}`;
    const status = document.createElement("div");
    status.className = "status";
    status.textContent = displayValue(node.status);
    element.append(kind, ordinal, status);
    element.addEventListener("click", () => selectNode(node.node_id));
    nodesRoot.append(element);
  });
  state.nodes.forEach((node, index) => {
    if (index === 0) return;
    const from = positions.get(state.nodes[index - 1].node_id);
    const to = positions.get(node.node_id);
    const path = document.createElementNS(SVG_NAMESPACE, "path");
    path.setAttribute("class", "edge");
    path.setAttribute("d", `M ${from.x + 178} ${from.y + 38} C ${from.x + 220} ${from.y + 38}, ${to.x - 42} ${to.y + 38}, ${to.x} ${to.y + 38}`);
    edgesRoot.append(path);
  });
  nodesRoot.style.transform = `scale(${Math.max(0.5, Math.min(1.6, Number(state.zoom) || 1))})`;
}

function clearInspector() {
  state.selectedNode = null;
  state.fullEpoch.input += 1;
  state.fullEpoch.output += 1;
  $("node-title").textContent = "Nenhum nó selecionado";
  showJSON($("input-summary"), {});
  showJSON($("output-summary"), {});
  showJSON($("metadata"), {});
  showJSON($("node-error"), null);
  $("input-full").textContent = "";
  $("output-full").textContent = "";
  $("input-full").hidden = true;
  $("output-full").hidden = true;
  $("load-full-input").hidden = true;
  $("load-full-output").hidden = true;
}

function selectNode(nodeId) {
  state.selectedNode = nodeId;
  state.fullEpoch.input += 1;
  state.fullEpoch.output += 1;
  const node = state.nodes.find((item) => item.node_id === nodeId);
  if (!node) {
    clearInspector();
    return;
  }
  $("node-title").textContent = displayValue(node.node_type).replaceAll("_", " ");
  showJSON($("input-summary"), node.input_summary);
  showJSON($("output-summary"), node.output_summary);
  showJSON($("metadata"), node.technical_metadata);
  showJSON($("node-error"), node.error);
  $("input-full").textContent = "";
  $("output-full").textContent = "";
  $("input-full").hidden = true;
  $("output-full").hidden = true;
  $("load-full-input").hidden = !node.has_full_input;
  $("load-full-output").hidden = !node.has_full_output;
  renderCanvas();
}

async function refreshOpenExecution(executionId, epoch) {
  if (!executionId) return false;
  if (epoch !== state.detailEpoch || executionId !== state.selectedExecution) return false;
  state.nodes = [];
  $("canvas-title").textContent = executionId;
  clearInspector();
  renderCanvas();
  let payload;
  try {
    payload = await getJSON(`/ops/api/executions/${encodeURIComponent(executionId)}/nodes`);
  } catch (error) {
    if (epoch !== state.detailEpoch || executionId !== state.selectedExecution) {
      error.superseded = true;
    }
    throw error;
  }
  if (epoch !== state.detailEpoch || executionId !== state.selectedExecution) return false;
  state.nodes = payload.nodes;
  $("canvas-title").textContent = executionId;
  clearInspector();
  renderCanvas();
  if (state.nodes.length) selectNode(state.nodes[0].node_id);
  return true;
}

async function openExecution(executionId) {
  const epoch = ++state.detailEpoch;
  state.selectedExecution = executionId;
  state.nodes = [];
  state.drawerOpen = true;
  state.detailTrigger = document.activeElement;
  $("canvas-title").textContent = executionId;
  $("drawer-title").textContent = executionId;
  clearInspector();
  renderCanvas();
  $("execution-drawer").classList.add("open");
  $("execution-drawer").setAttribute("aria-hidden", "false");
  $("drawer-backdrop").hidden = false;
  $("nav-overview").removeAttribute("aria-current");
  $("nav-execution").disabled = false;
  $("nav-execution").setAttribute("aria-current", "page");
  return refreshOpenExecution(executionId, epoch);
}

function showOverview() {
  state.drawerOpen = false;
  $("execution-drawer").classList.remove("open");
  $("execution-drawer").setAttribute("aria-hidden", "true");
  $("drawer-backdrop").hidden = true;
  $("nav-execution").removeAttribute("aria-current");
  $("nav-overview").setAttribute("aria-current", "page");
  $("range-select").value = state.range;
  $("lead-search").value = state.leadFilter;
  $("status-filter").value = state.statusFilter;
  $("completeness-filter").value = state.completenessFilter;
  renderExecutionTable();
  if (state.detailTrigger && typeof state.detailTrigger.focus === "function") {
    state.detailTrigger.focus();
  }
  state.detailTrigger = null;
}

async function loadFull(side) {
  if (!state.selectedExecution || !state.selectedNode) return;
  const executionId = state.selectedExecution;
  const nodeId = state.selectedNode;
  const detailEpoch = state.detailEpoch;
  const fullEpoch = ++state.fullEpoch[side];
  let payload;
  try {
    payload = await getJSON(
      `/ops/api/executions/${encodeURIComponent(executionId)}/nodes/${encodeURIComponent(nodeId)}/full?side=${side}`,
    );
  } catch (error) {
    if (
      detailEpoch !== state.detailEpoch
      || fullEpoch !== state.fullEpoch[side]
      || executionId !== state.selectedExecution
      || nodeId !== state.selectedNode
    ) error.superseded = true;
    throw error;
  }
  if (
    detailEpoch !== state.detailEpoch
    || fullEpoch !== state.fullEpoch[side]
    || executionId !== state.selectedExecution
    || nodeId !== state.selectedNode
  ) return false;
  const target = side === "input" ? $("input-full") : $("output-full");
  showJSON(target, payload.value ?? { status: "not_recorded" });
  target.hidden = false;
  return true;
}

function markDisconnected() {
  state.connected = false;
  state.liveError = "Atualização ao vivo indisponível. Os últimos dados recebidos permanecem visíveis.";
  $("live-state").textContent = "Desconectado";
  $("source-health-state").textContent = "Desconectado";
  renderAlert();
}

function handleDashboardError(error) {
  if (error && (error.message === "authentication_required" || error.superseded || error.dashboardHandled)) return;
  if (error && error.code === "source_unavailable") {
    state.dashboardError = "Fonte operacional indisponível. Os últimos dados recebidos permanecem visíveis.";
    renderAlert();
    return;
  }
  state.dashboardError = "Não foi possível atualizar os dados operacionais.";
  renderAlert();
}

function connectLive() {
  const source = new EventSource("/ops/api/events");
  source.addEventListener("ready", () => {
    state.connected = true;
    state.liveError = null;
    $("live-state").textContent = "Ao vivo";
    $("source-health-state").textContent = "Online";
    renderAlert();
  });
  source.addEventListener("change", () => {
    loadDashboard()
      .then((applied) => {
        if (!applied || !state.selectedExecution) return false;
        const executionId = state.selectedExecution;
        const epoch = ++state.detailEpoch;
        return refreshOpenExecution(executionId, epoch);
      })
      .catch(handleDashboardError);
  });
  source.addEventListener("degraded", markDisconnected);
  source.onerror = markDisconnected;
}

$("range-select").addEventListener("change", (event) => {
  state.range = event.target.value;
  loadDashboard().catch(handleDashboardError);
});
$("lead-search").addEventListener("input", (event) => {
  state.leadFilter = event.target.value.trim();
  renderExecutionTable();
});
$("status-filter").addEventListener("change", (event) => {
  state.statusFilter = event.target.value;
  renderExecutionTable();
});
$("completeness-filter").addEventListener("change", (event) => {
  state.completenessFilter = event.target.value;
  renderExecutionTable();
});
$("mobile-menu").addEventListener("click", () => setMobileNavigationOpen(true));
$("sidebar-close").addEventListener("click", () => setMobileNavigationOpen(false, true));
$("sidebar-backdrop").addEventListener("click", () => setMobileNavigationOpen(false, true));
mobileNavigationMedia.addEventListener("change", () => {
  setMobileNavigationOpen(state.mobileNavigationOpen);
});
$("refresh-dashboard").addEventListener("click", () => refreshDashboard().catch(handleDashboardError));
$("operator-menu").addEventListener("click", () => setOperatorMenuOpen(!state.operatorMenuOpen));
$("drawer-close").addEventListener("click", showOverview);
$("drawer-backdrop").addEventListener("click", showOverview);
$("nav-overview").addEventListener("click", () => {
  showOverview();
  setMobileNavigationOpen(false, true);
});
$("nav-execution").addEventListener("click", () => {
  setMobileNavigationOpen(false, true);
  if (state.selectedExecution) openExecution(state.selectedExecution).catch(handleDashboardError);
});
$("load-full-input").addEventListener("click", () => loadFull("input").catch(handleDashboardError));
$("load-full-output").addEventListener("click", () => loadFull("output").catch(handleDashboardError));
$("zoom-in").addEventListener("click", () => {
  state.zoom = Math.min(1.6, state.zoom + 0.1);
  renderCanvas();
});
$("zoom-out").addEventListener("click", () => {
  state.zoom = Math.max(0.5, state.zoom - 0.1);
  renderCanvas();
});
$("fit-canvas").addEventListener("click", () => {
  state.zoom = 1;
  $("execution-canvas").scrollTo(0, 0);
  renderCanvas();
});
document.addEventListener("click", (event) => {
  if (
    state.operatorMenuOpen
    && !$("operator-menu").contains(event.target)
    && !$("operator-popover").contains(event.target)
  ) setOperatorMenuOpen(false, $("operator-popover").contains(document.activeElement));
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.operatorMenuOpen) setOperatorMenuOpen(false, true);
  if (state.mobileNavigationOpen) setMobileNavigationOpen(false, true);
  if (state.drawerOpen) showOverview();
});

setMobileNavigationOpen(false);
setOperatorMenuOpen(false);
loadDashboard().catch(handleDashboardError);
connectLive();
