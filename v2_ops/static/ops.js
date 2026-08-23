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
  ["executions", "Execuções", "activity", "Eventos recebidos no período", "neutral"],
  ["distinct_leads", "Leads distintos", "users", "IDs distintos no período", "neutral"],
  ["in_progress", "Em andamento", "loader-circle", "Pending, running e stale", "active"],
  ["completed", "Concluídas", "circle-check", "Conclusão técnica", "success"],
  ["failed", "Falhas", "circle-alert", "Estado técnico failed", "danger"],
  ["manual_review", "Revisão manual", "user-round-check", "Estado manual_review", "warning"],
  ["technical_completion_rate", "Conclusão técnica", "percent", "Concluídas sobre execuções", "success"],
  ["average_terminal_duration_ms", "Duração média terminal", "clock", "Apenas execuções terminais", "neutral"],
];
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const STATUS_PRESENTATION = Object.freeze({
  pending: ["Pendente", "var(--warning-500)"],
  running: ["Em execução", "var(--info-500)"],
  running_stale: ["Execução atrasada", "var(--danger-600)"],
  completed: ["Concluída", "var(--success-500)"],
  failed: ["Falha", "var(--danger-600)"],
  manual_review: ["Revisão manual", "var(--coral-500)"],
});
const TRACE_PRESENTATION = Object.freeze({
  complete_trace: "Trace completo",
  partial_trace: "Trace parcial",
  ledger_only: "Somente ledger",
});
const MILESTONE_PRESENTATION = Object.freeze({
  reservation: "Reserva",
  payment: "Pagamento",
  public_delivery: "Entrega pública",
  handoff: "Handoff",
});
const $ = (id) => document.getElementById(id);
const mobileNavigationMedia = window.matchMedia("(max-width: 720px)");

function createLucideIcon(name, className = "") {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("class", `lucide ${className}`.trim());
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NAMESPACE, "use");
  use.setAttribute("href", `#lucide-${name}`);
  svg.append(use);
  return svg;
}

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

function executionStatusPresentation(status) {
  return Object.freeze({
    pending: ["Pendente", "pending"],
    running: ["Em execução", "active"],
    running_stale: ["Execução atrasada", "failed"],
    completed: ["Concluída", "completed"],
    failed: ["Falha", "failed"],
    manual_review: ["Revisão manual", "handoff"],
  })[status] ?? [displayValue(status), "neutral"];
}

function tracePresentation(value) {
  return Object.freeze({
    complete_trace: ["Trace completo", "completed"],
    partial_trace: ["Trace parcial", "pending"],
    ledger_only: ["Somente ledger", "neutral"],
  })[value] ?? [displayValue(value), "neutral"];
}

function statusChip(presentation) {
  const chip = document.createElement("span");
  chip.className = `status-chip ${presentation[1]}`;
  chip.textContent = presentation[0];
  return chip;
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
    iconElement.append(createLucideIcon(icon));
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
  if (!points.length) {
    emptyMessage(root, "Nenhuma execução registrada.");
    return;
  }
  const width = 640;
  const height = 220;
  const padding = 32;
  const maximum = Math.max(1, ...points.map((point) => Math.max(0, Number(point.count) || 0)));
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Execuções registradas ao longo do período");
  const grid = document.createElementNS(SVG_NAMESPACE, "g");
  grid.setAttribute("class", "chart-grid");
  for (let index = 0; index <= 4; index += 1) {
    const y = padding + (index / 4) * (height - padding * 2);
    const gridLine = document.createElementNS(SVG_NAMESPACE, "line");
    gridLine.setAttribute("x1", String(padding));
    gridLine.setAttribute("x2", String(width - padding));
    gridLine.setAttribute("y1", y.toFixed(2));
    gridLine.setAttribute("y2", y.toFixed(2));
    grid.append(gridLine);
  }
  const area = document.createElementNS(SVG_NAMESPACE, "path");
  area.setAttribute("class", "chart-area");
  const line = document.createElementNS(SVG_NAMESPACE, "polyline");
  line.setAttribute("class", "chart-line");
  const markers = [];
  const coordinates = points.map((point, index) => {
    const x = points.length === 1
      ? width / 2
      : padding + (index / (points.length - 1)) * (width - padding * 2);
    const count = Math.max(0, Number(point.count) || 0);
    const y = height - padding - (count / maximum) * (height - padding * 2);
    const marker = document.createElementNS(SVG_NAMESPACE, "circle");
    marker.setAttribute("class", "chart-point");
    marker.setAttribute("cx", x.toFixed(2));
    marker.setAttribute("cy", y.toFixed(2));
    marker.setAttribute("r", "4");
    const title = document.createElementNS(SVG_NAMESPACE, "title");
    title.textContent = `${formatTimestamp(point.start_at)}: ${count}`;
    marker.append(title);
    markers.push(marker);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  line.setAttribute("points", coordinates.join(" "));
  const baseline = height - padding;
  const firstX = markers[0].getAttribute("cx");
  const lastX = markers[markers.length - 1].getAttribute("cx");
  area.setAttribute(
    "d",
    `M ${firstX} ${baseline} L ${coordinates.join(" L ")} L ${lastX} ${baseline} Z`,
  );
  const maximumLabel = document.createElementNS(SVG_NAMESPACE, "text");
  maximumLabel.setAttribute("class", "chart-axis-label");
  maximumLabel.setAttribute("x", "4");
  maximumLabel.setAttribute("y", String(padding + 4));
  maximumLabel.textContent = String(maximum);
  const zeroLabel = document.createElementNS(SVG_NAMESPACE, "text");
  zeroLabel.setAttribute("class", "chart-axis-label");
  zeroLabel.setAttribute("x", "18");
  zeroLabel.setAttribute("y", String(baseline + 4));
  zeroLabel.textContent = "0";
  svg.replaceChildren(grid, area, line, ...markers, maximumLabel, zeroLabel);
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

function closedStatusDistribution() {
  const items = state.snapshot ? state.snapshot.status_distribution : [];
  return items.filter((item) => (
    item !== null
    && typeof item === "object"
    && !Array.isArray(item)
    && Object.prototype.hasOwnProperty.call(STATUS_PRESENTATION, String(item.status))
  ));
}

function renderStatusDistribution() {
  const root = $("status-distribution");
  const items = closedStatusDistribution();
  if (!items.length) {
    emptyMessage(root, "Nenhum registro no período.");
    return;
  }
  const normalized = items.map((item) => ({
    key: String(item.status),
    count: Math.max(0, Number(item.count) || 0),
  }));
  const total = normalized.reduce((sum, item) => sum + item.count, 0);
  if (total === 0) {
    emptyMessage(root, "Nenhum registro no período.");
    return;
  }
  const donut = document.createElement("div");
  donut.className = "donut";
  donut.setAttribute("role", "img");
  donut.setAttribute("aria-label", `${total} execuções distribuídas por estado`);
  let offset = 0;
  const segments = normalized.map((item) => {
    const presentation = STATUS_PRESENTATION[item.key];
    const start = offset;
    offset += (item.count / total) * 100;
    return `${presentation[1]} ${start.toFixed(2)}% ${offset.toFixed(2)}%`;
  });
  donut.style.setProperty("--donut-segments", segments.join(", "));
  const center = document.createElement("strong");
  center.className = "donut-center";
  center.textContent = String(total);
  const centerLabel = document.createElement("span");
  centerLabel.textContent = "execuções";
  donut.append(center, centerLabel);
  const legend = document.createElement("div");
  legend.className = "chart-legend";
  for (const item of normalized) {
    const presentation = STATUS_PRESENTATION[item.key];
    const row = document.createElement("div");
    row.className = "legend-row";
    const swatch = document.createElement("i");
    swatch.style.setProperty("--legend-color", presentation[1]);
    swatch.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = presentation[0];
    const count = document.createElement("strong");
    count.textContent = String(item.count);
    row.append(swatch, label, count);
    legend.append(row);
  }
  root.replaceChildren(donut, legend);
}

function renderTraceDistribution() {
  const root = $("trace-distribution");
  const items = state.snapshot ? state.snapshot.trace_distribution : [];
  if (!items.length) {
    emptyMessage(root, "Nenhum registro de trace no período.");
    return;
  }
  const maximum = Math.max(1, ...items.map((item) => Math.max(0, Number(item.count) || 0)));
  const chart = document.createElement("div");
  chart.className = "bar-chart trace-bars";
  chart.replaceChildren(...items.map((item) => labeledBar(
    TRACE_PRESENTATION[item.trace_completeness],
    Math.max(0, Number(item.count) || 0),
    maximum,
    "",
  )));
  root.replaceChildren(chart);
}

function renderMilestones() {
  const root = $("milestones-chart");
  const items = state.snapshot ? state.snapshot.milestones : [];
  if (!items.length) {
    emptyMessage(root, "Nenhum marco registrado.");
    return;
  }
  const maximum = Math.max(1, ...items.map((item) => Math.max(0, Number(item.count) || 0)));
  const chart = document.createElement("div");
  chart.className = "bar-chart milestone-bars";
  chart.replaceChildren(...items.map((item) => labeledBar(
    MILESTONE_PRESENTATION[item.milestone],
    Math.max(0, Number(item.count) || 0),
    maximum,
    "execuções com marco",
  )));
  root.replaceChildren(chart);
}

function renderTopNodeTypes() {
  const root = $("top-node-types");
  const items = state.snapshot ? state.snapshot.top_node_types : [];
  if (!items.length) {
    emptyMessage(root, "Nenhum nó registrado.");
    return;
  }
  const maximum = Math.max(1, ...items.map((item) => Math.max(0, Number(item.count) || 0)));
  const ranking = document.createElement("div");
  ranking.className = "bar-chart node-ranking";
  ranking.replaceChildren(...items.map((item, index) => labeledBar(
    `${index + 1}. ${displayValue(item.node_type).replaceAll("_", " ")}`,
    Math.max(0, Number(item.count) || 0),
    maximum,
    "",
  )));
  root.replaceChildren(ranking);
}

function isExecutionSummary(execution) {
  return (
    execution !== null
    && typeof execution === "object"
    && !Array.isArray(execution)
    && typeof execution.lead_id === "string"
    && execution.lead_id.trim() !== ""
    && typeof execution.execution_id === "string"
    && execution.execution_id.trim() !== ""
    && typeof execution.status === "string"
    && typeof execution.trace_completeness === "string"
    && typeof execution.has_reservation === "boolean"
    && typeof execution.has_payment === "boolean"
    && typeof execution.has_public_delivery === "boolean"
    && typeof execution.has_handoff === "boolean"
  );
}

function filteredExecutions() {
  const snapshotExecutions = state.snapshot && Array.isArray(state.snapshot.executions)
    ? state.snapshot.executions
    : [];
  const executions = snapshotExecutions.filter(isExecutionSummary);
  return executions.filter((execution) => (
    (!state.leadFilter || execution.lead_id === state.leadFilter)
    && (!state.statusFilter || execution.status === state.statusFilter)
    && (!state.completenessFilter || execution.trace_completeness === state.completenessFilter)
  ));
}

function milestoneLabels(execution) {
  return [
    execution.has_reservation && "Reserva",
    execution.has_payment && "Pagamento",
    execution.has_public_delivery && "Entrega",
    execution.has_handoff && "Handoff",
  ].filter(Boolean);
}

function leadInitials(leadId) {
  const displayed = displayValue(leadId);
  const parts = displayed.split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  return displayed.slice(0, 2).toUpperCase();
}

function makeExecutionOpenButton(execution, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "execution-link row-open";
  button.title = execution.execution_id;
  button.setAttribute("aria-label", label);
  button.textContent = label;
  button.addEventListener("click", (event) => {
    openExecution(execution.execution_id, event.currentTarget).catch(handleDashboardError);
  });
  return button;
}

function appendLeadIdentity(root, execution) {
  const avatar = document.createElement("span");
  avatar.className = "lead-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = leadInitials(execution.lead_id);
  const identity = document.createElement("span");
  identity.className = "lead-identity";
  identity.textContent = execution.lead_id;
  root.append(avatar, identity);
}

function appendMilestones(root, execution) {
  const labels = milestoneLabels(execution);
  if (!labels.length) {
    root.textContent = "Nenhum registrado";
    return;
  }
  const list = document.createElement("span");
  list.className = "milestone-list";
  for (const label of labels) {
    const item = document.createElement("span");
    item.className = "milestone-label";
    item.textContent = label;
    list.append(item);
  }
  root.append(list);
}

function textCell(value, className = "") {
  const cell = document.createElement("td");
  cell.className = className;
  cell.textContent = value;
  return cell;
}

function makeDesktopExecutionRow(execution) {
  const row = document.createElement("tr");
  row.dataset.executionId = execution.execution_id;
  const lead = document.createElement("td");
  lead.className = "lead-cell";
  const leadContent = document.createElement("span");
  leadContent.className = "lead-cell-content";
  appendLeadIdentity(leadContent, execution);
  lead.append(leadContent);

  const executionId = textCell(shortExecutionId(execution.execution_id), "execution-id-cell");
  executionId.title = execution.execution_id;
  const status = document.createElement("td");
  status.append(statusChip(executionStatusPresentation(execution.status)));
  const trace = document.createElement("td");
  trace.append(statusChip(tracePresentation(execution.trace_completeness)));
  const milestones = document.createElement("td");
  appendMilestones(milestones, execution);
  const action = document.createElement("td");
  action.className = "row-action";
  action.append(makeExecutionOpenButton(execution, "Ver detalhes"));

  row.append(
    lead,
    executionId,
    textCell(formatTimestamp(execution.received_at)),
    textCell(formatDuration(execution.duration_ms)),
    status,
    trace,
    textCell(displayValue(execution.current_node_type)),
    textCell(String(Math.max(0, Number(execution.node_count) || 0))),
    milestones,
    textCell(displayValue(execution.terminal_reason)),
    action,
  );
  return row;
}

function fact(label, value) {
  const item = document.createElement("div");
  item.className = "drawer-fact";
  const heading = document.createElement("strong");
  heading.textContent = label;
  const content = document.createElement("span");
  content.textContent = value;
  item.append(heading, content);
  return item;
}

function renderDrawerSummary(executionId) {
  const executions = state.snapshot && Array.isArray(state.snapshot.executions)
    ? state.snapshot.executions
    : [];
  const execution = executions.find((item) => (
    isExecutionSummary(item) && item.execution_id === executionId
  )) || null;
  $("drawer-title").textContent = shortExecutionId(executionId || "Execução");
  $("drawer-lead").textContent = displayValue(execution && execution.lead_id);
  const status = executionStatusPresentation(execution && execution.status);
  $("drawer-status").className = `status-chip ${status[1]}`;
  $("drawer-status").textContent = status[0];
  const trace = tracePresentation(execution && execution.trace_completeness);
  $("drawer-trace").className = `status-chip ${trace[1]}`;
  $("drawer-trace").textContent = trace[0];
  $("drawer-summary").replaceChildren(
    fact("Recebida", formatTimestamp(execution && execution.received_at)),
    fact("Duração", formatDuration(execution && execution.duration_ms)),
    fact("Nó atual", displayValue(execution && execution.current_node_type)),
    fact("Nós", displayValue(execution && execution.node_count)),
    fact("Motivo terminal", displayValue(execution && execution.terminal_reason)),
    fact("Marcos", execution ? milestoneLabels(execution).join(", ") || "Nenhum registrado" : "Nenhum registrado"),
  );
}

function makeMobileFact(label, value) {
  const fact = document.createElement("div");
  fact.className = "service-mobile-fact";
  const heading = document.createElement("strong");
  heading.textContent = label;
  const content = document.createElement("span");
  content.textContent = value;
  fact.append(heading, content);
  return fact;
}

function makeMobileExecutionCard(execution) {
  const card = document.createElement("article");
  card.className = "execution-mobile-card";
  card.dataset.executionId = execution.execution_id;
  const top = document.createElement("div");
  top.className = "service-mobile-top";
  const lead = document.createElement("div");
  lead.className = "lead-cell";
  appendLeadIdentity(lead, execution);
  const executionId = document.createElement("span");
  executionId.className = "mobile-execution-id";
  executionId.title = execution.execution_id;
  executionId.textContent = shortExecutionId(execution.execution_id);
  top.append(lead, executionId);

  const chips = document.createElement("div");
  chips.className = "service-mobile-chips";
  chips.append(
    statusChip(executionStatusPresentation(execution.status)),
    statusChip(tracePresentation(execution.trace_completeness)),
  );
  const milestones = document.createElement("div");
  milestones.className = "service-mobile-fact mobile-milestones";
  const milestoneHeading = document.createElement("strong");
  milestoneHeading.textContent = "Marcos";
  const milestoneContent = document.createElement("span");
  appendMilestones(milestoneContent, execution);
  milestones.append(milestoneHeading, milestoneContent);
  const action = document.createElement("div");
  action.className = "service-mobile-action";
  action.append(makeExecutionOpenButton(execution, "Ver detalhes"));

  card.append(
    top,
    chips,
    makeMobileFact("Recebida", formatTimestamp(execution.received_at)),
    makeMobileFact("Nós", String(Math.max(0, Number(execution.node_count) || 0))),
    milestones,
    action,
  );
  return card;
}

function renderExecutionTable() {
  const tableRoot = $("execution-table-body");
  const mobileRoot = $("execution-mobile-list");
  const executions = filteredExecutions();
  $("result-count").textContent = `${executions.length} ${executions.length === 1 ? "execução" : "execuções"}`;
  tableRoot.replaceChildren(...executions.map(makeDesktopExecutionRow));
  mobileRoot.replaceChildren(...executions.map(makeMobileExecutionCard));
  const empty = $("empty-state");
  empty.hidden = executions.length !== 0;
  const sourceExecutions = state.snapshot && Array.isArray(state.snapshot.executions)
    ? state.snapshot.executions
    : [];
  empty.textContent = sourceExecutions.length
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
  renderStatusDistribution();
  renderTraceDistribution();
  renderMilestones();
  renderTopNodeTypes();
  populateFilter($("status-filter"), closedStatusDistribution(), "status", "Todos", state.statusFilter);
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

function renderTimeline() {
  const timeline = $("execution-timeline");
  timeline.replaceChildren();
  state.nodes.forEach((node, index) => {
    const ordinal = Number(node.ordinal);
    const stepNumber = Number.isInteger(ordinal) && ordinal > 0 ? ordinal : index + 1;
    const selected = node.node_id === state.selectedNode;
    const item = document.createElement("li");
    item.className = "timeline-item";
    const element = document.createElement("button");
    element.type = "button";
    element.className = "execution-step";
    element.dataset.nodeId = node.node_id;
    if (selected) element.setAttribute("aria-current", "step");

    const marker = document.createElement("span");
    marker.className = "step-marker";
    marker.textContent = String(stepNumber);
    const name = document.createElement("span");
    name.className = "step-name";
    name.textContent = displayValue(node.node_type).replaceAll("_", " ");

    element.append(marker, name);
    element.addEventListener("click", () => selectNode(node.node_id));
    item.append(element);
    timeline.append(item);
  });
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
  for (const element of $("execution-timeline").querySelectorAll(".execution-step")) {
    const selected = element.dataset.nodeId === nodeId;
    element.classList.toggle("selected", selected);
    if (selected) element.setAttribute("aria-current", "step");
    else element.removeAttribute("aria-current");
  }
}

function clearDetail(executionId = "Execução") {
  state.selectedExecution = executionId === "Execução" ? null : executionId;
  state.nodes = [];
  state.selectedNode = null;
  $("canvas-title").textContent = executionId;
  $("execution-timeline").replaceChildren();
  clearInspector();
}

function setExecutionDrawerOpen(open) {
  state.drawerOpen = Boolean(open);
  $("execution-drawer").classList.toggle("open", state.drawerOpen);
  $("execution-drawer").setAttribute("aria-hidden", String(!state.drawerOpen));
  $("drawer-backdrop").hidden = !state.drawerOpen;
  document.body.classList.toggle("drawer-open", state.drawerOpen);
}

function closeExecutionDrawer({ restoreFocus = true } = {}) {
  state.detailEpoch += 1;
  const detailTrigger = state.detailTrigger;
  state.detailTrigger = null;
  setExecutionDrawerOpen(false);
  clearDetail();
  renderDrawerSummary(null);
  $("nav-execution").removeAttribute("aria-current");
  $("nav-overview").setAttribute("aria-current", "page");
  $("range-select").value = state.range;
  $("lead-search").value = state.leadFilter;
  $("status-filter").value = state.statusFilter;
  $("completeness-filter").value = state.completenessFilter;
  renderExecutionTable();
  if (!restoreFocus || !detailTrigger) return;
  let focusTarget = detailTrigger.node instanceof HTMLElement && detailTrigger.node.isConnected
    ? detailTrigger.node
    : null;
  if (!focusTarget) {
    for (const button of document.querySelectorAll(".row-open")) {
      const container = detailTrigger.surface === "mobile"
        ? button.closest(".execution-mobile-card")
        : button.closest("#execution-table-body > tr");
      if (container && container.dataset.executionId === detailTrigger.executionId) {
        focusTarget = button;
        break;
      }
    }
  }
  if (focusTarget) focusTarget.focus();
}

async function refreshOpenExecution(executionId, epoch) {
  if (!executionId) return false;
  if (epoch !== state.detailEpoch || executionId !== state.selectedExecution) return false;
  clearDetail(executionId);
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
  renderTimeline();
  if (state.nodes.length) selectNode(state.nodes[0].node_id);
  return true;
}

async function openExecution(executionId, trigger = null) {
  const epoch = ++state.detailEpoch;
  const mobileContainer = trigger && typeof trigger.closest === "function"
    ? trigger.closest(".execution-mobile-card")
    : null;
  const desktopContainer = trigger && typeof trigger.closest === "function"
    ? trigger.closest("#execution-table-body > tr")
    : null;
  const triggerContainer = mobileContainer || desktopContainer;
  state.detailTrigger = triggerContainer && triggerContainer.dataset.executionId === executionId
    ? {
      node: trigger,
      executionId,
      surface: mobileContainer ? "mobile" : "desktop",
    }
    : null;
  clearDetail(executionId);
  renderDrawerSummary(executionId);
  setExecutionDrawerOpen(true);
  $("nav-overview").removeAttribute("aria-current");
  $("nav-execution").disabled = false;
  $("nav-execution").setAttribute("aria-current", "page");
  return refreshOpenExecution(executionId, epoch);
}

function showOverview() {
  closeExecutionDrawer();
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
