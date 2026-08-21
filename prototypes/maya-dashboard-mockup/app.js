"use strict";

const JOURNEY_LABELS = Object.freeze([
  "Mensagem recebida",
  "Interpretação da Maya",
  "Consulta",
  "Opção apresentada",
  "Coleta de dados",
  "Confirmação",
  "Reserva",
  "Pagamento",
  "Entrega ou handoff",
]);

const journey = (...statuses) => JOURNEY_LABELS.map((label, index) => ({
  label,
  status: statuses[index] || "pending",
}));

const SERVICES = Object.freeze([
  {
    id: "demo-001", leadLabel: "Lead demonstrativo 01", interest: "Passeio", stage: "Qualificação",
    lastActivity: "há 2 min", mayaStatus: "Ativo", reservationStatus: "Não iniciada", paymentStatus: "Não iniciado",
    handoffStatus: "Não necessário", attention: "Normal", language: "PT", summary: "Lead avaliando passeios para um final de semana na Chapada.",
    nextStep: "Entender perfil e data preferida", reason: null,
    conversation: [{ speaker: "Lead", text: "Quero conhecer uma cachoeira no fim de semana." }, { speaker: "Maya", text: "Que tipo de experiência você prefere e para qual data?" }],
    journey: journey("completed", "active"),
  },
  {
    id: "demo-002", leadLabel: "Lead demonstrativo 02", interest: "Hospedagem", stage: "Consultando opções",
    lastActivity: "há 5 min", mayaStatus: "Ativo", reservationStatus: "Não iniciada", paymentStatus: "Não iniciado",
    handoffStatus: "Não necessário", attention: "Normal", language: "PT", summary: "Casal buscando quarto privativo por três noites.",
    nextStep: "Apresentar opções disponíveis", reason: null,
    conversation: [{ speaker: "Lead", text: "Vocês têm quarto para casal?" }, { speaker: "Maya", text: "Vou verificar as opções para o período informado." }],
    journey: journey("completed", "completed", "active"),
  },
  {
    id: "demo-003", leadLabel: "Lead demonstrativo 03", interest: "Pacote", stage: "Opção escolhida",
    lastActivity: "há 9 min", mayaStatus: "Aguardando", reservationStatus: "Preparada", paymentStatus: "Não iniciado",
    handoffStatus: "Não necessário", attention: "Normal", language: "PT", summary: "Pacote de hospedagem com passeio escolhido; aguardando dados do titular.",
    nextStep: "Coletar dados do titular e participantes", reason: null,
    conversation: [{ speaker: "Maya", text: "Essa combinação atende ao período que você informou." }, { speaker: "Lead", text: "Gostei dessa opção." }],
    journey: journey("completed", "completed", "completed", "completed", "active"),
  },
  {
    id: "demo-004", leadLabel: "Lead demonstrativo 04", interest: "Passeio", stage: "Aguardando confirmação",
    lastActivity: "há 14 min", mayaStatus: "Aguardando", reservationStatus: "Preparada", paymentStatus: "Não iniciado",
    handoffStatus: "Não necessário", attention: "Atenção", language: "PT", summary: "Todos os dados foram reunidos e o resumo foi apresentado.",
    nextStep: "Aguardar confirmação natural do lead", reason: "Resumo enviado há 14 minutos.",
    conversation: [{ speaker: "Maya", text: "Confira o resumo da sua solicitação. Posso seguir?" }],
    journey: journey("completed", "completed", "completed", "completed", "completed", "active"),
  },
  {
    id: "demo-005", leadLabel: "Lead demonstrativo 05", interest: "Passeio", stage: "Pagamento pendente",
    lastActivity: "há 18 min", mayaStatus: "Aguardando", reservationStatus: "Confirmada", paymentStatus: "Pendente",
    handoffStatus: "Não necessário", attention: "Atenção", language: "PT", summary: "Reserva demonstrativa confirmada; sinal por cartão ainda pendente.",
    nextStep: "Aguardar conclusão do pagamento", reason: "Link demonstrativo ainda não concluído.",
    conversation: [{ speaker: "Maya", text: "Sua reserva está preparada. Falta apenas concluir o pagamento." }],
    journey: journey("completed", "completed", "completed", "completed", "completed", "completed", "completed", "active"),
  },
  {
    id: "demo-006", leadLabel: "Lead demonstrativo 06", interest: "Hospedagem", stage: "Concluído",
    lastActivity: "há 27 min", mayaStatus: "Concluído", reservationStatus: "Confirmada", paymentStatus: "Pago",
    handoffStatus: "Não necessário", attention: "Normal", language: "PT", summary: "Atendimento demonstrativo concluído sem intervenção humana.",
    nextStep: "Nenhuma ação necessária", reason: null,
    conversation: [{ speaker: "Maya", text: "Tudo certo. Sua solicitação foi concluída." }, { speaker: "Lead", text: "Obrigado!" }],
    journey: journey("completed", "completed", "completed", "completed", "completed", "completed", "completed", "completed", "completed"),
  },
  {
    id: "demo-007", leadLabel: "Lead demonstrativo 07", interest: "Passeio", stage: "Handoff preparado",
    lastActivity: "há 31 min", mayaStatus: "Concluído", reservationStatus: "Preparada", paymentStatus: "Não iniciado",
    handoffStatus: "Solicitado", attention: "Atenção", language: "PT", summary: "Triagem completa antes da negociação de condição especial.",
    nextStep: "Equipe humana finaliza a negociação", reason: "Lead solicitou uma condição comercial fora da regra padrão.",
    conversation: [{ speaker: "Lead", text: "Agora que passei os dados, consigo uma condição diferente?" }, { speaker: "Maya", text: "Já temos tudo para realizar sua reserva. Uma pessoa da equipe continuará por aqui." }],
    journey: journey("completed", "completed", "completed", "completed", "completed", "completed", "skipped", "skipped", "handoff"),
  },
  {
    id: "demo-008", leadLabel: "Lead demonstrativo 08", interest: "Hospedagem", stage: "Handoff imediato",
    lastActivity: "há 36 min", mayaStatus: "Concluído", reservationStatus: "Não iniciada", paymentStatus: "Não iniciado",
    handoffStatus: "Concluído", attention: "Normal", language: "PT", summary: "Lead pediu explicitamente para falar com uma pessoa.",
    nextStep: "Atendimento assumido pela equipe", reason: "Pedido humano explícito.",
    conversation: [{ speaker: "Lead", text: "Quero falar com uma pessoa agora." }, { speaker: "Maya", text: "Vou chamar alguém da nossa equipe para ajudar você por aqui." }],
    journey: journey("completed", "completed", "skipped", "skipped", "skipped", "skipped", "skipped", "skipped", "handoff"),
  },
  {
    id: "demo-009", leadLabel: "Lead demonstrativo 09", interest: "Passeio", stage: "Falha na consulta",
    lastActivity: "há 42 min", mayaStatus: "Falha", reservationStatus: "Não iniciada", paymentStatus: "Não iniciado",
    handoffStatus: "Triagem em andamento", attention: "Crítico", language: "PT", summary: "A consulta de opções não pôde ser concluída, mas a Maya continua coletando dados úteis.",
    nextStep: "Coletar período alternativo e tentar consulta segura", reason: "Integração demonstrativa temporariamente indisponível.",
    conversation: [{ speaker: "Maya", text: "Enquanto verifico isso, você tem flexibilidade de data?" }],
    journey: journey("completed", "completed", "failed"),
  },
  {
    id: "demo-010", leadLabel: "Lead demonstrativo 10", interest: "Pacote", stage: "Falha no pagamento",
    lastActivity: "há 49 min", mayaStatus: "Falha", reservationStatus: "Confirmada", paymentStatus: "Falha",
    handoffStatus: "Solicitado", attention: "Crítico", language: "PT", summary: "Reserva preservada após dificuldade operacional na etapa financeira.",
    nextStep: "Equipe ajuda a concluir o pagamento", reason: "Segunda tentativa demonstrativa de pagamento falhou.",
    conversation: [{ speaker: "Lead", text: "O pagamento deu erro de novo." }, { speaker: "Maya", text: "Sua reserva está preparada. A equipe continuará a etapa de pagamento por aqui." }],
    journey: journey("completed", "completed", "completed", "completed", "completed", "completed", "completed", "failed", "handoff"),
  },
  {
    id: "demo-011", leadLabel: "Lead demonstrativo 11", interest: "Hospedagem", stage: "Consultando opções",
    lastActivity: "há 54 min", mayaStatus: "Ativo", reservationStatus: "Não iniciada", paymentStatus: "Não iniciado",
    handoffStatus: "Não necessário", attention: "Normal", language: "EN", summary: "International guest looking for a shared room for two nights.",
    nextStep: "Present matching room options", reason: null,
    conversation: [{ speaker: "Lead", text: "Do you have a shared room for two nights?" }, { speaker: "Maya", text: "I’ll check the available options for your dates." }],
    journey: journey("completed", "completed", "active"),
  },
  {
    id: "demo-012", leadLabel: "Lead demonstrativo 12", interest: "Passeio", stage: "Concluído",
    lastActivity: "há 1 h", mayaStatus: "Concluído", reservationStatus: "Confirmada", paymentStatus: "Pago",
    handoffStatus: "Não necessário", attention: "Normal", language: "PT", summary: "Jornada autônoma concluída do primeiro contato ao pagamento.",
    nextStep: "Nenhuma ação necessária", reason: null,
    conversation: [{ speaker: "Lead", text: "Pagamento concluído." }, { speaker: "Maya", text: "Tudo certo. Sua reserva está confirmada." }],
    journey: journey("completed", "completed", "completed", "completed", "completed", "completed", "completed", "completed", "completed"),
  },
]);

const RANGE_MODELS = Object.freeze({
  today: {
    kpis: [["Leads atendidos", "34", "+8%", "positive", "◌"], ["Atendimentos ativos", "7", "2 aguardando", "attention", "◎"], ["Reservas confirmadas", "5", "+1 hoje", "positive", "▣"], ["Receita potencial", "R$ 8,4 mil", "+6%", "positive", "↗"], ["Taxa de conversão", "14,7%", "+1,2 p.p.", "positive", "⌁"], ["Handoffs", "1", "triagem completa", "neutral", "↗"], ["Pagamentos pendentes", "2", "requer atenção", "attention", "◇"], ["Falhas com atenção", "1", "operacional", "attention", "!" ]],
    funnel: [["Novos leads",34],["Qualificados",23],["Opção escolhida",15],["Reserva",5],["Pagamento",4]],
    volume: [18,26,31,24,34,29,34], interest: [52,31,17], handoffs: [["Negociação",55],["Pedido humano",30],["Operacional",15]],
  },
  "7days": {
    kpis: [["Leads atendidos", "248", "+12%", "positive", "◌"], ["Atendimentos ativos", "18", "4 aguardando", "attention", "◎"], ["Reservas confirmadas", "31", "+9%", "positive", "▣"], ["Receita potencial", "R$ 42,8 mil", "+18%", "positive", "↗"], ["Taxa de conversão", "12,5%", "+2,1 p.p.", "positive", "⌁"], ["Handoffs", "9", "3,6% dos leads", "neutral", "↗"], ["Pagamentos pendentes", "4", "R$ 1,9 mil", "attention", "◇"], ["Falhas com atenção", "2", "ver agora", "attention", "!" ]],
    funnel: [["Novos leads",248],["Qualificados",176],["Opção escolhida",93],["Reserva",31],["Pagamento",26]],
    volume: [28,35,31,44,38,29,43], interest: [48,30,22], handoffs: [["Negociação",44],["Pedido humano",33],["Falha operacional",23]],
  },
  "30days": {
    kpis: [["Leads atendidos", "1.084", "+16%", "positive", "◌"], ["Atendimentos ativos", "18", "estável", "neutral", "◎"], ["Reservas confirmadas", "146", "+21%", "positive", "▣"], ["Receita potencial", "R$ 198 mil", "+24%", "positive", "↗"], ["Taxa de conversão", "13,5%", "+1,8 p.p.", "positive", "⌁"], ["Handoffs", "37", "3,4% dos leads", "neutral", "↗"], ["Pagamentos pendentes", "12", "R$ 7,2 mil", "attention", "◇"], ["Falhas com atenção", "5", "-2 no período", "positive", "!" ]],
    funnel: [["Novos leads",1084],["Qualificados",786],["Opção escolhida",421],["Reserva",146],["Pagamento",129]],
    volume: [132,149,151,162,177,145,168], interest: [46,33,21], handoffs: [["Negociação",46],["Pedido humano",35],["Falha operacional",19]],
  },
});

const KPI_CLASSES = ["", "", "", "", "", "coral", "warning", "danger"];
const RANGE_LABELS = Object.freeze({ today: "Hoje", "7days": "Últimos 7 dias", "30days": "Últimos 30 dias" });
let activeRange = "7days";
let activeTrigger = null;

const $ = (id) => document.getElementById(id);
const create = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function getRangeModel(rangeKey) {
  const source = RANGE_MODELS[rangeKey] || RANGE_MODELS["7days"];
  return JSON.parse(JSON.stringify(source));
}

function sparkline(index) {
  const patterns = ["1,18 12,15 23,17 34,9 45,12 56,5 65,7", "1,14 12,10 23,13 34,8 45,16 56,12 65,9", "1,19 12,16 23,12 34,13 45,7 56,9 65,4"];
  return `<svg class="sparkline" viewBox="0 0 66 23" aria-hidden="true"><polyline points="${patterns[index % patterns.length]}"></polyline></svg>`;
}

function renderKpis(rangeModel) {
  const root = $("kpi-grid");
  root.replaceChildren();
  rangeModel.kpis.forEach(([label, value, trend, tone, icon], index) => {
    const card = create("article", `kpi-card ${KPI_CLASSES[index]}`.trim());
    const top = create("div", "kpi-top");
    top.append(create("span", "kpi-icon", icon));
    const labelNode = create("p", "kpi-label", label);
    const valueNode = create("strong", "kpi-value", value);
    const foot = create("div", "kpi-foot");
    foot.append(create("span", `trend ${tone}`, trend));
    const spark = create("div");
    spark.innerHTML = sparkline(index);
    foot.append(spark.firstElementChild);
    card.append(top, labelNode, valueNode, foot);
    root.append(card);
  });
}

function renderFunnel(data) {
  const root = $("commercial-funnel");
  root.replaceChildren();
  const max = data[0][1];
  data.forEach(([label, value]) => {
    const row = create("div", "funnel-stage");
    const track = create("div", "funnel-track");
    const fill = create("div", "funnel-fill", `${Math.round(value / max * 100)}%`);
    fill.style.width = `${Math.max(14, value / max * 100)}%`;
    track.append(fill);
    row.append(create("label", "", label), track, create("span", "funnel-value", String(value)));
    root.append(row);
  });
}

function renderVolumeChart(data) {
  const root = $("service-volume-chart");
  const max = Math.max(...data) + 8;
  const x = (index) => 24 + index * 62;
  const y = (value) => 171 - value / max * 145;
  const points = data.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const area = `24,171 ${points} ${x(data.length - 1)},171`;
  const days = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
  root.innerHTML = `<svg viewBox="0 0 420 205" role="img" aria-label="Volume demonstrativo de atendimentos por dia"><defs><linearGradient id="volumeGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2F7D4A" stop-opacity=".22"/><stop offset="1" stop-color="#2F7D4A" stop-opacity="0"/></linearGradient></defs>${[35,80,125,170].map(n => `<line class="chart-grid" x1="24" y1="${n}" x2="396" y2="${n}"/>`).join("")}<polygon class="chart-area" points="${area}"/><polyline class="chart-line" points="${points}"/>${data.map((value,index) => `<circle class="chart-point" cx="${x(index)}" cy="${y(value)}" r="4"><title>${days[index]}: ${value} atendimentos</title></circle><text class="chart-label" x="${x(index)}" y="195" text-anchor="middle">${days[index]}</text>`).join("")}</svg>`;
}

function renderInterestMix(data) {
  const root = $("interest-mix-chart");
  root.innerHTML = `<div class="donut" role="img" aria-label="Passeios ${data[0]}%, hospedagem ${data[1]}%, pacotes ${data[2]}%"><div class="donut-center"><strong>${data[0]}%</strong><small>passeios</small></div></div><div class="chart-legend"></div>`;
  const labels = [["Passeios", "#245634"], ["Hospedagem", "#E85F67"], ["Pacotes", "#C99135"]];
  const legend = root.querySelector(".chart-legend");
  labels.forEach(([label, color], index) => {
    const row = create("div", "legend-row");
    const dot = create("i"); dot.style.background = color;
    row.append(dot, create("span", "", label), create("strong", "", `${data[index]}%`));
    legend.append(row);
  });
}

function renderHandoffReasons(data) {
  const root = $("handoff-reasons-chart");
  root.replaceChildren();
  data.forEach(([label, value]) => {
    const row = create("div", "bar-row");
    const heading = create("div", "bar-label");
    heading.append(create("span", "", label), create("strong", "", `${value}%`));
    const track = create("div", "bar-track");
    const fill = create("div", "bar-fill"); fill.style.width = `${value}%`;
    track.append(fill); row.append(heading, track); root.append(row);
  });
}

function renderCharts(rangeModel) {
  $("analytics-range-label").textContent = RANGE_LABELS[activeRange];
  renderFunnel(rangeModel.funnel);
  renderVolumeChart(rangeModel.volume);
  renderInterestMix(rangeModel.interest);
  renderHandoffReasons(rangeModel.handoffs);
}

function filterServices({ query = "", status = "", interest = "" } = {}) {
  const term = query.trim().toLocaleLowerCase("pt-BR");
  return SERVICES.filter((service) => {
    const haystack = `${service.leadLabel} ${service.interest} ${service.stage} ${service.summary}`.toLocaleLowerCase("pt-BR");
    return (!term || haystack.includes(term)) && (!status || service.mayaStatus === status) && (!interest || service.interest === interest);
  });
}

function chipTone(value) {
  if (["Confirmada", "Pago", "Concluído"].includes(value)) return "success";
  if (["Ativo"].includes(value)) return "active";
  if (["Aguardando", "Pendente", "Preparada", "Triagem em andamento"].includes(value)) return "pending";
  if (["Solicitado"].includes(value)) return "handoff";
  if (["Falha"].includes(value)) return "failed";
  return "neutral";
}

function statusChip(value) {
  return create("span", `status-chip ${chipTone(value)}`, value);
}

function renderServices(services) {
  const table = $("services-table-body");
  const mobile = $("services-mobile-list");
  table.replaceChildren(); mobile.replaceChildren();
  $("result-count").textContent = `${services.length} atendimentos demonstrativos`;
  if (!services.length) {
    const row = create("tr"); const cell = create("td", "empty-state", "Nenhum atendimento demonstrativo corresponde aos filtros."); cell.colSpan = 8; row.append(cell); table.append(row);
    mobile.append(create("div", "empty-state", "Nenhum atendimento demonstrativo corresponde aos filtros."));
    return;
  }
  services.forEach((service) => {
    const row = create("tr"); row.dataset.serviceId = service.id;
    const leadCell = create("td");
    const lead = create("div", "lead-cell");
    const avatar = create("span", "lead-avatar", service.id.slice(-2));
    const name = create("div"); name.append(create("strong", "", service.leadLabel), create("small", "", `${service.language} · ${service.attention}`));
    lead.append(avatar, name); leadCell.append(lead);
    const interest = create("td", "", service.interest);
    const stage = create("td");
    if (service.attention !== "Normal") stage.append(create("span", `attention-dot ${service.attention === "Crítico" ? "critical" : ""}`));
    stage.append(document.createTextNode(service.stage));
    const maya = create("td"); maya.append(statusChip(service.mayaStatus));
    const reservation = create("td"); reservation.append(statusChip(service.reservationStatus));
    const payment = create("td"); payment.append(statusChip(service.paymentStatus));
    const activity = create("td", "", service.lastActivity);
    const action = create("td"); const button = create("button", "row-open", "→"); button.type = "button"; button.ariaLabel = `Abrir ${service.leadLabel}`; button.addEventListener("click", () => { activeTrigger = button; openServiceDrawer(service.id); }); action.append(button);
    row.append(leadCell, interest, stage, maya, reservation, payment, activity, action); table.append(row);

    const card = create("button", "service-mobile-card"); card.type = "button"; card.addEventListener("click", () => { activeTrigger = card; openServiceDrawer(service.id); });
    const top = create("div", "service-mobile-top"); top.append(create("strong", "", service.leadLabel), statusChip(service.mayaStatus));
    const meta = create("div", "service-mobile-meta"); meta.append(statusChip(service.interest), statusChip(service.reservationStatus), statusChip(service.paymentStatus));
    card.append(top, create("p", "", service.stage), meta); mobile.append(card);
  });
}

function journeyStateLabel(status) {
  return ({ completed: "Concluído", active: "Em andamento", pending: "Pendente", handoff: "Handoff", failed: "Falha", skipped: "Não aplicável" })[status];
}

function openServiceDrawer(serviceId) {
  const service = SERVICES.find((item) => item.id === serviceId);
  if (!service) return;
  $("drawer-title").textContent = service.leadLabel;
  const statusRoot = $("drawer-status"); statusRoot.replaceChildren(statusChip(service.mayaStatus), create("span", "status-chip neutral", service.interest));
  $("drawer-summary").textContent = service.summary;
  $("drawer-next-step").textContent = service.nextStep;
  $("drawer-reason").textContent = service.reason || "Fluxo dentro do esperado; nenhuma atenção adicional necessária.";
  const facts = $("drawer-facts"); facts.replaceChildren();
  [["Etapa",service.stage],["Reserva",service.reservationStatus],["Pagamento",service.paymentStatus],["Handoff",service.handoffStatus]].forEach(([label,value]) => { const fact = create("div", "fact"); fact.append(create("small", "", label), create("strong", "", value)); facts.append(fact); });
  const journeyRoot = $("journey-list"); journeyRoot.replaceChildren();
  service.journey.forEach((step,index) => { const item = create("li", `journey-item ${step.status}`); const icon = create("span", "journey-icon", step.status === "completed" ? "✓" : step.status === "failed" ? "!" : index + 1); const copy = create("div", "journey-copy"); copy.append(create("strong", "", step.label), create("small", "", step.status === "active" ? "Maya atuando nesta etapa" : journeyStateLabel(step.status))); item.append(icon, copy, create("span", "journey-state", journeyStateLabel(step.status))); journeyRoot.append(item); });
  const messages = $("drawer-conversation"); messages.replaceChildren();
  service.conversation.forEach(({ speaker, text }) => { const message = create("div", `message ${speaker === "Maya" ? "maya" : ""}`); message.append(create("small", "", speaker), document.createTextNode(text)); messages.append(message); });
  const drawer = $("service-drawer"); drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); $("drawer-backdrop").hidden = false; $("close-drawer").focus();
  document.querySelectorAll("tbody tr").forEach((row) => row.classList.toggle("selected", row.dataset.serviceId === serviceId));
}

function closeServiceDrawer() {
  const drawer = $("service-drawer");
  if (!drawer.classList.contains("open")) return;
  drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); $("drawer-backdrop").hidden = true;
  document.querySelectorAll("tbody tr").forEach((row) => row.classList.remove("selected"));
  if (activeTrigger) activeTrigger.focus();
}

function applyFilters() {
  renderServices(filterServices({ query: $("service-search").value, status: $("status-filter").value, interest: $("interest-filter").value }));
}

function setActiveNavigation(section) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.section === section));
  const preview = $("section-preview");
  if (section === "overview") { preview.hidden = true; return; }
  const active = document.querySelector(`.nav-item[data-section="${section}"] span:nth-child(2)`);
  preview.textContent = `${active ? active.textContent : "Seção"}: prévia visual. A visão detalhada será definida somente após aprovação e conexão read-only.`;
  preview.hidden = false;
}

function simulateRefresh() {
  const button = $("refresh-button"); const label = $("refresh-label");
  button.disabled = true; label.textContent = "Atualizando…";
  window.setTimeout(() => { renderKpis(getRangeModel(activeRange)); renderCharts(getRangeModel(activeRange)); label.textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`; button.disabled = false; }, 450);
}

function renderDashboard() {
  const model = getRangeModel(activeRange);
  renderKpis(model); renderCharts(model); renderServices(SERVICES);
  $("range-select").addEventListener("change", (event) => { activeRange = event.target.value; const range = getRangeModel(activeRange); renderKpis(range); renderCharts(range); });
  ["service-search", "status-filter", "interest-filter"].forEach((id) => $(id).addEventListener(id === "service-search" ? "input" : "change", applyFilters));
  $("refresh-button").addEventListener("click", simulateRefresh);
  $("close-drawer").addEventListener("click", closeServiceDrawer);
  $("drawer-backdrop").addEventListener("click", closeServiceDrawer);
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => setActiveNavigation(item.dataset.section)));
  $("mobile-menu").addEventListener("click", () => $("app-sidebar").classList.toggle("mobile-open"));
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeServiceDrawer();
});

document.addEventListener("DOMContentLoaded", renderDashboard);
