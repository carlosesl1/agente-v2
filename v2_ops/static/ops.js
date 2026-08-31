"use strict";

const state = {
  range: "7d",
  snapshot: null,
  records: null,
  leadDetail: null,
  activeView: "overview",
  selectedExecution: null,
  selectedLeadId: null,
  nodes: [],
  selectedNode: null,
  statusFilter: "",
  completenessFilter: "",
  leadFilter: "",
  connected: false,
  dashboardEpoch: 0,
  recordsEpoch: 0,
  leadDetailEpoch: 0,
  detailEpoch: 0,
  fullEpoch: { input: 0, output: 0 },
  dashboardError: null,
  recordsError: null,
  liveError: null,
  mobileNavigationOpen: false,
  operatorMenuOpen: false,
  dashboardLoading: false,
  drawerOpen: false,
  detailTrigger: null,
  leadListFilter: "",
};

const VIEW_DEFINITIONS = Object.freeze({
  overview: Object.freeze({
    section: "overview-view",
    navigation: "nav-overview",
    title: "Painel operacional",
    subtitle: "Fatos comerciais e execuções já registrados.",
  }),
  leads: Object.freeze({
    section: "leads-view",
    navigation: "nav-leads",
    title: "Leads",
    subtitle: "Perfil factual e histórico por Lead ID persistido.",
  }),
  executions: Object.freeze({
    section: "executions-view",
    navigation: "nav-execution",
    title: "Execuções",
    subtitle: "Traces técnicos do período selecionado.",
  }),
  reservations: Object.freeze({
    section: "reservations-view",
    navigation: "nav-reservations",
    title: "Reservas",
    subtitle: "Rascunhos, workflows, outcomes e IDs finais registrados.",
  }),
  payments: Object.freeze({
    section: "payments-view",
    navigation: "nav-payments",
    title: "Pagamentos",
    subtitle: "Iniciação, link preparado e liquidação mantidos separados.",
  }),
  handoffs: Object.freeze({
    section: "handoffs-view",
    navigation: "nav-handoffs",
    title: "Handoffs",
    subtitle: "Somente handoffs e vínculos persistidos.",
  }),
});

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
  const messages = [state.dashboardError, state.recordsError, state.liveError].filter(Boolean);
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

function isRecordObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeCount(value) {
  const count = Number(value);
  return Number.isInteger(count) && count >= 0 ? count : 0;
}

function formatMoney(minor, currency) {
  if (!Number.isInteger(minor) || minor < 0 || typeof currency !== "string" || !currency) {
    return "Não registrado";
  }
  try {
    return new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency,
    }).format(minor / 100);
  } catch (_error) {
    return `${currency} ${(minor / 100).toFixed(2)}`;
  }
}

function setActiveView(viewName, closeMobile = true) {
  if (!Object.prototype.hasOwnProperty.call(VIEW_DEFINITIONS, viewName)) return false;
  state.activeView = viewName;
  for (const [name, definition] of Object.entries(VIEW_DEFINITIONS)) {
    const selected = name === viewName;
    $(definition.section).hidden = !selected;
    const navigation = $(definition.navigation);
    navigation.classList.toggle("active", selected);
    if (selected) navigation.setAttribute("aria-current", "page");
    else navigation.removeAttribute("aria-current");
  }
  const definition = VIEW_DEFINITIONS[viewName];
  $("header-title").textContent = definition.title;
  $("header-subtitle").textContent = definition.subtitle;
  document.querySelector(".range-control").hidden = !["overview", "executions"].includes(viewName);
  if (closeMobile && state.mobileNavigationOpen) setMobileNavigationOpen(false, true);
  return true;
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

function recordCollection(name, validator) {
  const source = state.records && Array.isArray(state.records[name]) ? state.records[name] : [];
  return source.filter(validator);
}

function isLeadRecord(value) {
  return isRecordObject(value)
    && typeof value.lead_id === "string"
    && value.lead_id.trim() !== ""
    && typeof value.state_code === "string"
    && typeof value.state_label === "string";
}

function isReservationRecord(value) {
  return isRecordObject(value)
    && typeof value.command_id === "string"
    && typeof value.status_code === "string"
    && typeof value.status_label === "string"
    && Array.isArray(value.components)
    && isRecordObject(value.customer);
}

function isPaymentRecord(value) {
  return isRecordObject(value)
    && typeof value.record_id === "string"
    && typeof value.phase === "string"
    && typeof value.status_code === "string"
    && typeof value.status_label === "string"
    && Array.isArray(value.steps);
}

function isHandoffRecord(value) {
  return isRecordObject(value)
    && typeof value.handoff_id === "string"
    && typeof value.status_code === "string"
    && typeof value.status_label === "string";
}

function recordStatusPresentation(code, label) {
  const tone = Object.freeze({
    confirmed: "completed",
    payment_settled: "completed",
    settled: "completed",
    link_ready: "active",
    payment_link_ready: "active",
    reservation_preparing: "pending",
    preparing: "pending",
    in_progress: "pending",
    failed: "failed",
    handoff_registered: "handoff",
  })[String(code)] || "neutral";
  return [displayValue(label), tone];
}

function summaryCard(label, value, note, tone = "neutral") {
  const card = document.createElement("article");
  card.className = `record-summary-card ${tone}`;
  const heading = document.createElement("span");
  heading.textContent = label;
  const amount = document.createElement("strong");
  amount.textContent = String(value);
  const description = document.createElement("small");
  description.textContent = note;
  card.append(heading, amount, description);
  return card;
}

function renderRecordSummary() {
  const leads = recordCollection("leads", isLeadRecord);
  const reservations = recordCollection("reservations", isReservationRecord);
  const payments = recordCollection("payments", isPaymentRecord);
  const handoffs = recordCollection("handoffs", isHandoffRecord);
  const confirmedReservations = reservations.filter((record) => record.status_code === "confirmed").length;
  const linksPrepared = payments.filter((record) => record.payment_link_prepared === true).length;
  const settledPayments = payments.filter((record) => record.settled === true).length;
  const root = $("record-summary-grid");
  root.replaceChildren(
    summaryCard("Leads exibidos", leads.length, "IDs persistidos retornados"),
    summaryCard("Reservas registradas", reservations.length, "Rascunhos e workflows retornados"),
    summaryCard("Reservas confirmadas", confirmedReservations, "Outcome persistido confirmado", "success"),
    summaryCard("Links preparados", linksPrepared, "Sem promover para valor pago", "active"),
    summaryCard("Pagamentos liquidados", settledPayments, "Somente efeitos persistidos", "success"),
    summaryCard("Handoffs registrados", handoffs.length, "Vínculos exatos quando disponíveis", "warning"),
  );
  if (state.records && state.records.truncated === true) {
    const notice = document.createElement("p");
    notice.className = "truncated-note";
    notice.textContent = "A resposta atingiu o limite operacional; as listas exibidas são parciais.";
    root.append(notice);
  }
}

function leadOpenButton(lead, label = "Ver lead") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "execution-link lead-open";
  button.textContent = label;
  button.setAttribute("aria-label", `${label}: ${lead.lead_id}`);
  button.addEventListener("click", (event) => {
    setActiveView("leads");
    openLead(lead.lead_id, event.currentTarget).catch(handleDashboardError);
  });
  return button;
}

function renderOverviewLeads() {
  const root = $("overview-lead-list");
  const leads = recordCollection("leads", isLeadRecord).slice(0, 6);
  if (!leads.length) {
    emptyMessage(root, "Nenhum lead registrado nas fontes ativas.");
    return;
  }
  const cards = leads.map((lead) => {
    const card = document.createElement("article");
    card.className = "overview-lead-card";
    const identity = document.createElement("div");
    identity.className = "lead-cell-content";
    appendLeadIdentity(identity, lead);
    const status = statusChip(recordStatusPresentation(lead.state_code, lead.state_label));
    const activity = document.createElement("small");
    activity.textContent = `Último registro: ${formatTimestamp(lead.last_activity_at)}`;
    const counts = document.createElement("span");
    counts.textContent = `${safeCount(lead.reservation_count)} reserva(s) · ${safeCount(lead.payment_count)} pagamento(s)`;
    card.append(identity, status, activity, counts, leadOpenButton(lead));
    return card;
  });
  root.replaceChildren(...cards);
}

function filteredLeadRecords() {
  const leads = recordCollection("leads", isLeadRecord);
  const filter = state.leadListFilter.toLocaleLowerCase("pt-BR");
  if (!filter) return leads;
  return leads.filter((lead) => lead.lead_id.toLocaleLowerCase("pt-BR").includes(filter));
}

function makeLeadRow(lead) {
  const row = document.createElement("tr");
  row.dataset.leadId = lead.lead_id;
  const identity = document.createElement("td");
  const content = document.createElement("span");
  content.className = "lead-cell-content";
  appendLeadIdentity(content, lead);
  identity.append(content);
  const status = document.createElement("td");
  status.append(statusChip(recordStatusPresentation(lead.state_code, lead.state_label)));
  const action = document.createElement("td");
  action.className = "row-action";
  action.append(leadOpenButton(lead));
  row.append(
    identity,
    status,
    textCell(formatTimestamp(lead.first_activity_at)),
    textCell(formatTimestamp(lead.last_activity_at)),
    textCell(String(safeCount(lead.fact_count))),
    textCell(String(safeCount(lead.dialogue_turn_count))),
    textCell(String(safeCount(lead.execution_count))),
    textCell(String(safeCount(lead.reservation_count))),
    textCell(String(safeCount(lead.payment_count))),
    textCell(String(safeCount(lead.handoff_count))),
    action,
  );
  return row;
}

function makeLeadMobileCard(lead) {
  const card = document.createElement("article");
  card.className = "record-mobile-card";
  card.dataset.leadId = lead.lead_id;
  const top = document.createElement("div");
  top.className = "service-mobile-top";
  const identity = document.createElement("div");
  identity.className = "lead-cell";
  appendLeadIdentity(identity, lead);
  top.append(identity, statusChip(recordStatusPresentation(lead.state_code, lead.state_label)));
  card.append(
    top,
    makeMobileFact("Último registro", formatTimestamp(lead.last_activity_at)),
    makeMobileFact("Fatos", String(safeCount(lead.fact_count))),
    makeMobileFact("Execuções", String(safeCount(lead.execution_count))),
    makeMobileFact("Reservas", String(safeCount(lead.reservation_count))),
    makeMobileFact("Pagamentos", String(safeCount(lead.payment_count))),
  );
  const action = document.createElement("div");
  action.className = "service-mobile-action";
  action.append(leadOpenButton(lead));
  card.append(action);
  return card;
}

function renderLeads() {
  const leads = filteredLeadRecords();
  $("lead-result-count").textContent = `${leads.length} ${leads.length === 1 ? "lead" : "leads"}`;
  $("lead-table-body").replaceChildren(...leads.map(makeLeadRow));
  $("lead-mobile-list").replaceChildren(...leads.map(makeLeadMobileCard));
  const empty = $("lead-empty-state");
  empty.hidden = leads.length !== 0;
  empty.textContent = state.leadListFilter
    ? "Nenhum Lead ID corresponde à busca."
    : "Nenhum lead registrado nas fontes ativas.";
}

function reservationService(reservation) {
  const labels = reservation.components.filter(isRecordObject).map((component) => (
    component.kind === "activity"
      ? displayValue(component.title || component.activity_id)
      : [component.hotel_code, component.room_type].filter(Boolean).join(" · ") || "Hospedagem"
  ));
  return labels.length ? labels.join(" + ") : "Não registrado";
}

function reservationPeriod(reservation) {
  const periods = reservation.components.filter(isRecordObject).map((component) => {
    const start = displayValue(component.start_date);
    return component.end_date ? `${start} a ${component.end_date}` : start;
  });
  return periods.length ? periods.join(" · ") : "Não registrado";
}

function reservationParty(reservation) {
  const components = reservation.components.filter(isRecordObject);
  if (!components.length) return "Não registrado";
  const adults = components.reduce((total, component) => total + safeCount(component.adults), 0);
  const children = components.reduce((total, component) => total + safeCount(component.children), 0);
  return `${adults} adulto(s) · ${children} criança(s)`;
}

function reservationReferences(reservation) {
  const references = [
    reservation.draft_id && `Rascunho: ${reservation.draft_id}`,
    reservation.workflow_id && `Workflow: ${reservation.workflow_id}`,
    reservation.provider_reference && `Referência retornada: ${reservation.provider_reference}`,
    reservation.bokun_booking_id && `ID final Bókun: ${reservation.bokun_booking_id}`,
    reservation.cloudbeds_reservation_id && `ID final Cloudbeds: ${reservation.cloudbeds_reservation_id}`,
  ].filter(Boolean);
  return references.length ? references.join(" · ") : "Não registrado";
}

function makeRecordMobileCard(title, presentation, facts) {
  const card = document.createElement("article");
  card.className = "record-mobile-card";
  const top = document.createElement("div");
  top.className = "service-mobile-top";
  const heading = document.createElement("strong");
  heading.textContent = title;
  top.append(heading, statusChip(presentation));
  card.append(top, ...facts.map(([label, value]) => makeMobileFact(label, value)));
  return card;
}

function makeReservationRow(reservation) {
  const row = document.createElement("tr");
  row.dataset.recordId = reservation.command_id;
  const status = document.createElement("td");
  status.append(statusChip(recordStatusPresentation(reservation.status_code, reservation.status_label)));
  row.append(
    status,
    textCell(displayValue(reservation.lead_id)),
    textCell(reservationService(reservation)),
    textCell(reservationPeriod(reservation)),
    textCell(reservationParty(reservation)),
    textCell(formatMoney(reservation.total_minor, reservation.currency)),
    textCell(displayValue(reservation.payment_method)),
    textCell(reservationReferences(reservation)),
    textCell(formatTimestamp(reservation.updated_at)),
  );
  return row;
}

function makeReservationMobileCard(reservation) {
  return makeRecordMobileCard(
    reservationService(reservation),
    recordStatusPresentation(reservation.status_code, reservation.status_label),
    [
      ["Lead", displayValue(reservation.lead_id)],
      ["Período", reservationPeriod(reservation)],
      ["Pessoas", reservationParty(reservation)],
      ["Total", formatMoney(reservation.total_minor, reservation.currency)],
      ["Referências", reservationReferences(reservation)],
    ],
  );
}

function renderReservations() {
  const reservations = recordCollection("reservations", isReservationRecord);
  $("reservation-result-count").textContent = `${reservations.length} ${reservations.length === 1 ? "reserva" : "reservas"}`;
  $("reservation-table-body").replaceChildren(...reservations.map(makeReservationRow));
  $("reservation-mobile-list").replaceChildren(...reservations.map(makeReservationMobileCard));
  $("reservation-empty-state").hidden = reservations.length !== 0;
}

function paymentPhaseLabel(phase) {
  return Object.freeze({ initiation: "Iniciação", settlement: "Liquidação" })[phase] || displayValue(phase);
}

function paymentSteps(payment) {
  const steps = payment.steps.filter(isRecordObject).map((step) => (
    `${displayValue(step.step).replaceAll("_", " ")}: ${displayValue(step.status)}`
  ));
  return steps.length ? steps.join(" · ") : "Nenhuma etapa registrada";
}

function makePaymentRow(payment) {
  const row = document.createElement("tr");
  row.dataset.recordId = payment.record_id;
  const status = document.createElement("td");
  status.append(statusChip(recordStatusPresentation(payment.status_code, payment.status_label)));
  row.append(
    status,
    textCell(displayValue(payment.lead_id)),
    textCell(paymentPhaseLabel(payment.phase)),
    textCell(displayValue(payment.method)),
    textCell(formatMoney(payment.amount_due_minor, payment.currency)),
    textCell(formatMoney(payment.amount_paid_minor, payment.currency)),
    textCell(paymentSteps(payment)),
    textCell(formatTimestamp(payment.updated_at)),
  );
  return row;
}

function makePaymentMobileCard(payment) {
  return makeRecordMobileCard(
    displayValue(payment.payment_id || payment.record_id),
    recordStatusPresentation(payment.status_code, payment.status_label),
    [
      ["Lead", displayValue(payment.lead_id)],
      ["Fase", paymentPhaseLabel(payment.phase)],
      ["Método", displayValue(payment.method)],
      ["Valor previsto", formatMoney(payment.amount_due_minor, payment.currency)],
      ["Valor pago", formatMoney(payment.amount_paid_minor, payment.currency)],
      ["Etapas", paymentSteps(payment)],
    ],
  );
}

function renderPayments() {
  const payments = recordCollection("payments", isPaymentRecord);
  $("payment-result-count").textContent = `${payments.length} ${payments.length === 1 ? "pagamento" : "pagamentos"}`;
  $("payment-table-body").replaceChildren(...payments.map(makePaymentRow));
  $("payment-mobile-list").replaceChildren(...payments.map(makePaymentMobileCard));
  $("payment-empty-state").hidden = payments.length !== 0;
}

function makeHandoffRow(handoff) {
  const row = document.createElement("tr");
  row.dataset.recordId = handoff.handoff_id;
  const status = document.createElement("td");
  status.append(statusChip(recordStatusPresentation(handoff.status_code, handoff.status_label)));
  row.append(
    status,
    textCell(displayValue(handoff.lead_id)),
    textCell(handoff.handoff_id),
    textCell(displayValue(handoff.reason_code)),
    textCell(displayValue(handoff.incident_key)),
    textCell(String(safeCount(handoff.event_count))),
    textCell(formatTimestamp(handoff.created_at)),
    textCell(formatTimestamp(handoff.updated_at)),
  );
  return row;
}

function makeHandoffMobileCard(handoff) {
  return makeRecordMobileCard(
    handoff.handoff_id,
    recordStatusPresentation(handoff.status_code, handoff.status_label),
    [
      ["Lead", displayValue(handoff.lead_id)],
      ["Código de motivo", displayValue(handoff.reason_code)],
      ["Incidente", displayValue(handoff.incident_key)],
      ["Eventos", String(safeCount(handoff.event_count))],
      ["Atualizado", formatTimestamp(handoff.updated_at)],
    ],
  );
}

function renderHandoffs() {
  const handoffs = recordCollection("handoffs", isHandoffRecord);
  $("handoff-result-count").textContent = `${handoffs.length} ${handoffs.length === 1 ? "handoff" : "handoffs"}`;
  $("handoff-table-body").replaceChildren(...handoffs.map(makeHandoffRow));
  $("handoff-mobile-list").replaceChildren(...handoffs.map(makeHandoffMobileCard));
  $("handoff-empty-state").hidden = handoffs.length !== 0;
}

function detailRecord(title, value, metadata = null) {
  const record = document.createElement("article");
  record.className = "detail-record";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const content = document.createElement("p");
  content.textContent = displayValue(value);
  record.append(heading, content);
  if (metadata) {
    const note = document.createElement("small");
    note.textContent = metadata;
    record.append(note);
  }
  return record;
}

function replaceDetailList(root, records, emptyText) {
  if (!records.length) {
    emptyMessage(root, emptyText);
    return;
  }
  root.replaceChildren(...records);
}

function clearLeadDetail(leadId) {
  state.leadDetail = null;
  $("lead-detail").hidden = false;
  $("lead-detail-title").textContent = leadId;
  $("lead-detail-state").className = "status-chip neutral";
  $("lead-detail-state").textContent = "Carregando";
  $("export-lead-history").disabled = true;
  $("lead-detail-summary").replaceChildren();
  for (const id of (
    ["lead-facts", "lead-dialogue", "lead-reservations", "lead-payments", "lead-handoffs", "lead-executions"]
  )) $(id).replaceChildren();
}

function renderLeadDialogue(lead) {
  const entries = [];
  const inboundEvents = Array.isArray(lead.inbound_events) ? lead.inbound_events : [];
  for (const inbound of inboundEvents.filter(isRecordObject)) {
    entries.push({
      at: inbound.occurred_at || "",
      node: detailRecord(
        "Entrada recebida",
        `Estado: ${displayValue(inbound.status)}`,
        formatTimestamp(inbound.occurred_at),
      ),
    });
  }
  const turns = Array.isArray(lead.dialogue_turns) ? lead.dialogue_turns : [];
  for (const turn of turns.filter(isRecordObject)) {
    const article = document.createElement("article");
    article.className = "conversation-turn";
    const customer = document.createElement("div");
    customer.className = "conversation-message customer";
    const customerLabel = document.createElement("strong");
    customerLabel.textContent = "Cliente";
    const customerText = document.createElement("p");
    customerText.textContent = displayValue(turn.customer_message);
    customer.append(customerLabel, customerText);
    const maya = document.createElement("div");
    maya.className = "conversation-message maya";
    const mayaLabel = document.createElement("strong");
    mayaLabel.textContent = "Maya";
    const mayaText = document.createElement("p");
    const chunks = Array.isArray(turn.assistant_reply_chunks) ? turn.assistant_reply_chunks : [];
    mayaText.textContent = chunks.length ? chunks.map(String).join("\n") : "Não registrado";
    maya.append(mayaLabel, mayaText);
    const timestamp = document.createElement("small");
    timestamp.textContent = formatTimestamp(turn.committed_at);
    article.append(customer, maya, timestamp);
    entries.push({ at: turn.committed_at || "", node: article });
  }
  const replies = Array.isArray(lead.public_replies) ? lead.public_replies : [];
  for (const reply of replies.filter(isRecordObject)) {
    entries.push({
      at: reply.updated_at || "",
      node: detailRecord(
        `Saída pública · ${displayValue(reply.author)}`,
        reply.text,
        `${displayValue(reply.status)} · ${formatTimestamp(reply.updated_at)}`,
      ),
    });
  }
  entries.sort((left, right) => left.at.localeCompare(right.at));
  replaceDetailList($("lead-dialogue"), entries.map((entry) => entry.node), "Nenhum atendimento registrado para este lead.");
}

function renderLeadDetail() {
  const lead = state.leadDetail;
  if (!isRecordObject(lead) || !isLeadRecord(lead.summary)) return false;
  const summary = lead.summary;
  $("lead-detail").hidden = false;
  $("lead-detail-title").textContent = summary.lead_id;
  const presentation = recordStatusPresentation(summary.state_code, summary.state_label);
  $("lead-detail-state").className = `status-chip ${presentation[1]}`;
  $("lead-detail-state").textContent = presentation[0];
  $("export-lead-history").disabled = false;
  $("lead-detail-summary").replaceChildren(
    fact("Primeiro registro", formatTimestamp(summary.first_activity_at)),
    fact("Último registro", formatTimestamp(summary.last_activity_at)),
    fact("Fatos", String(safeCount(summary.fact_count))),
    fact("Atendimentos", String(safeCount(summary.dialogue_turn_count))),
    fact("Execuções", String(safeCount(summary.execution_count))),
    fact("Reservas", String(safeCount(summary.reservation_count))),
    fact("Iniciações de pagamento", String(safeCount(summary.payment_initiation_count))),
    fact("Pagamentos liquidados", String(safeCount(summary.settled_payment_count))),
    fact("Handoffs", String(safeCount(summary.handoff_count))),
  );

  const facts = Array.isArray(lead.facts) ? lead.facts.filter(isRecordObject).map((item) => (
    detailRecord(item.name, item.value, `Revisão ${displayValue(item.revision)} · ${formatTimestamp(item.persisted_at)}`)
  )) : [];
  const reservations = Array.isArray(lead.reservations) ? lead.reservations.filter(isReservationRecord) : [];
  const customer = reservations.find((reservation) => isRecordObject(reservation.customer));
  if (customer) {
    for (const [label, key] of (
      [["Nome", "full_name"], ["E-mail", "email"], ["Telefone", "phone"], ["País", "country"], ["Nascimento", "birth_date"], ["Gênero", "gender"], ["Referência do cliente", "customer_ref"]]
    )) {
      if (customer.customer[key]) facts.push(detailRecord(label, customer.customer[key], "Registro da reserva"));
    }
  }
  const manifests = Array.isArray(lead.passenger_manifests) ? lead.passenger_manifests : [];
  for (const manifest of manifests.filter(isRecordObject)) {
    const passengerSummary = Array.isArray(manifest.passengers)
      ? manifest.passengers.map((passenger) => [passenger.full_name, passenger.document, passenger.phone].filter(Boolean).join(" · ")).filter(Boolean).join(" | ")
      : "";
    facts.push(detailRecord(
      `Manifesto · ${safeCount(manifest.adults)} adulto(s) · ${safeCount(manifest.children)} criança(s)`,
      passengerSummary || "Sem dados individuais registrados",
      formatTimestamp(manifest.persisted_at),
    ));
  }
  replaceDetailList($("lead-facts"), facts, "Nenhum fato coletado registrado para este lead.");
  renderLeadDialogue(lead);

  replaceDetailList(
    $("lead-reservations"),
    reservations.map((reservation) => detailRecord(
      reservation.status_label,
      `${reservationService(reservation)} · ${formatMoney(reservation.total_minor, reservation.currency)}`,
      `${reservationReferences(reservation)} · ${formatTimestamp(reservation.updated_at)}`,
    )),
    "Nenhuma reserva vinculada a este Lead ID.",
  );
  const payments = Array.isArray(lead.payments) ? lead.payments.filter(isPaymentRecord) : [];
  replaceDetailList(
    $("lead-payments"),
    payments.map((payment) => detailRecord(
      payment.status_label,
      `${paymentPhaseLabel(payment.phase)} · previsto ${formatMoney(payment.amount_due_minor, payment.currency)} · pago ${formatMoney(payment.amount_paid_minor, payment.currency)}`,
      `${paymentSteps(payment)} · ${formatTimestamp(payment.updated_at)}`,
    )),
    "Nenhum pagamento vinculado a este Lead ID.",
  );
  const handoffs = Array.isArray(lead.handoffs) ? lead.handoffs.filter(isHandoffRecord) : [];
  replaceDetailList(
    $("lead-handoffs"),
    handoffs.map((handoff) => detailRecord(
      handoff.status_label,
      `${displayValue(handoff.reason_code)} · ${displayValue(handoff.incident_key)}`,
      `${handoff.handoff_id} · ${formatTimestamp(handoff.updated_at)}`,
    )),
    "Nenhum handoff vinculado a este Lead ID.",
  );
  const executions = Array.isArray(lead.executions) ? lead.executions.filter(isRecordObject) : [];
  const executionItems = executions.map((execution) => {
    const item = detailRecord(
      shortExecutionId(execution.execution_id),
      displayValue(execution.status),
      formatTimestamp(execution.received_at),
    );
    const button = document.createElement("button");
    button.type = "button";
    button.className = "execution-link lead-execution-open";
    button.textContent = "Abrir trace";
    button.addEventListener("click", (event) => {
      openExecution(execution.execution_id, event.currentTarget).catch(handleDashboardError);
    });
    item.append(button);
    return item;
  });
  replaceDetailList($("lead-executions"), executionItems, "Nenhuma execução vinculada a este Lead ID.");
  return true;
}

async function openLead(leadId, trigger = null, preserve = false) {
  if (typeof leadId !== "string" || !leadId) return false;
  const epoch = ++state.leadDetailEpoch;
  state.selectedLeadId = leadId;
  if (!preserve) clearLeadDetail(leadId);
  setActiveView("leads");
  let payload;
  try {
    payload = await getJSON(`/ops/api/leads/${encodeURIComponent(leadId)}`);
  } catch (error) {
    if (epoch !== state.leadDetailEpoch || leadId !== state.selectedLeadId) {
      error.superseded = true;
      throw error;
    }
    if (error && error.message !== "authentication_required") {
      state.recordsError = error.code === "source_unavailable"
        ? "Fontes comerciais indisponíveis. Os últimos dados recebidos permanecem visíveis."
        : "Não foi possível carregar o detalhe do lead.";
      renderAlert();
      error.dashboardHandled = true;
    }
    throw error;
  }
  if (epoch !== state.leadDetailEpoch || leadId !== state.selectedLeadId) return false;
  if (
    !isRecordObject(payload)
    || !isRecordObject(payload.lead)
    || !isRecordObject(payload.lead.summary)
    || payload.lead.summary.lead_id !== leadId
  ) {
    const error = new Error("invalid_lead_payload");
    state.recordsError = "Não foi possível carregar o detalhe do lead.";
    renderAlert();
    error.dashboardHandled = true;
    throw error;
  }
  state.leadDetail = payload.lead;
  state.recordsError = null;
  renderAlert();
  renderLeadDetail();
  if (trigger instanceof HTMLElement && trigger.isConnected && !preserve) {
    $("lead-detail").scrollIntoView({ block: "start", behavior: "smooth" });
  }
  return true;
}

function renderRecords() {
  if (!state.records) return;
  renderRecordSummary();
  renderOverviewLeads();
  renderLeads();
  renderReservations();
  renderPayments();
  renderHandoffs();
}

async function loadRecords() {
  const epoch = ++state.recordsEpoch;
  let payload;
  try {
    payload = await getJSON("/ops/api/records");
  } catch (error) {
    if (epoch !== state.recordsEpoch) {
      error.superseded = true;
      throw error;
    }
    if (error && error.message !== "authentication_required") {
      state.recordsError = error.code === "source_unavailable"
        ? "Fontes comerciais indisponíveis. Os últimos dados recebidos permanecem visíveis."
        : "Não foi possível atualizar os dados comerciais.";
      renderAlert();
      error.dashboardHandled = true;
    }
    throw error;
  }
  if (epoch !== state.recordsEpoch) return false;
  if (
    !isRecordObject(payload)
    || !Array.isArray(payload.leads)
    || !Array.isArray(payload.reservations)
    || !Array.isArray(payload.payments)
    || !Array.isArray(payload.handoffs)
  ) {
    const error = new Error("invalid_records_payload");
    state.recordsError = "Não foi possível atualizar os dados comerciais.";
    renderAlert();
    error.dashboardHandled = true;
    throw error;
  }
  state.records = payload;
  state.recordsError = null;
  renderAlert();
  renderRecords();
  return true;
}

function downloadDataset(dataset, leadId = null) {
  const datasets = new Set(["leads", "executions", "reservations", "payments", "handoffs", "lead-history"]);
  if (!datasets.has(dataset)) return false;
  let path = `/ops/api/exports/${dataset}.csv`;
  if (dataset === "lead-history") {
    if (typeof leadId !== "string" || !leadId) return false;
    path += `?lead_id=${encodeURIComponent(leadId)}`;
  }
  window.location.assign(path);
  return true;
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
    const results = await Promise.allSettled([loadDashboard(), loadRecords()]);
    for (const result of results) {
      if (result.status === "rejected") handleDashboardError(result.reason);
    }
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
  state.detailTrigger = trigger instanceof HTMLElement
    ? {
      node: trigger,
      executionId,
      surface: mobileContainer ? "mobile" : desktopContainer ? "desktop" : "detail",
    }
    : null;
  clearDetail(executionId);
  renderDrawerSummary(executionId);
  setExecutionDrawerOpen(true);
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
    Promise.allSettled([loadDashboard(), loadRecords()])
      .then(async (results) => {
        for (const result of results) {
          if (result.status === "rejected") handleDashboardError(result.reason);
        }
        const detailRefreshes = [];
        if (results[0].status === "fulfilled" && results[0].value && state.selectedExecution) {
          const executionId = state.selectedExecution;
          const epoch = ++state.detailEpoch;
          detailRefreshes.push(refreshOpenExecution(executionId, epoch));
        }
        if (results[1].status === "fulfilled" && results[1].value && state.selectedLeadId) {
          detailRefreshes.push(openLead(state.selectedLeadId, null, true));
        }
        const details = await Promise.allSettled(detailRefreshes);
        for (const result of details) {
          if (result.status === "rejected") handleDashboardError(result.reason);
        }
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
$("lead-list-search").addEventListener("input", (event) => {
  state.leadListFilter = event.target.value.trim().toLocaleLowerCase("pt-BR");
  renderLeads();
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
  setActiveView("overview");
});
$("nav-execution").addEventListener("click", () => {
  setActiveView("executions");
});
$("nav-leads").addEventListener("click", () => setActiveView("leads"));
$("nav-reservations").addEventListener("click", () => setActiveView("reservations"));
$("nav-payments").addEventListener("click", () => setActiveView("payments"));
$("nav-handoffs").addEventListener("click", () => setActiveView("handoffs"));
$("export-leads").addEventListener("click", () => downloadDataset("leads"));
$("export-executions").addEventListener("click", () => downloadDataset("executions"));
$("export-reservations").addEventListener("click", () => downloadDataset("reservations"));
$("export-payments").addEventListener("click", () => downloadDataset("payments"));
$("export-handoffs").addEventListener("click", () => downloadDataset("handoffs"));
$("export-lead-history").addEventListener("click", () => {
  downloadDataset("lead-history", state.selectedLeadId);
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
setActiveView("overview", false);
Promise.allSettled([loadDashboard(), loadRecords()]).then((results) => {
  for (const result of results) {
    if (result.status === "rejected") handleDashboardError(result.reason);
  }
});
connectLive();
