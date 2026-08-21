# Maya Operational Dashboard — Visual Mockup Design

**Date:** 2026-08-15

**Artifact:** Standalone visual prototype at `/home/ubuntu/maya-dashboard-mockup`

## 1. Goal

Create a polished, interactive, light-themed operational dashboard mockup for Maya. The first screen combines executive commercial indicators with detailed operational visibility. The artifact exists only to validate visual hierarchy, navigation, interactions, terminology, and brand fit before any connection to production data.

## 2. Scope

### Included

- One standalone, browser-viewable HTML dashboard.
- High-fidelity desktop layout with usable tablet/mobile adaptation.
- Synthetic, clearly fictional operational and commercial data.
- Simulated filters, navigation, tabs, charts, table selection, and a detail drawer.
- Executive summary, commercial funnel, operational charts, recent-service table, and per-service journey timeline.
- Visual states for success, active work, pending work, handoff, and failure.
- Brand palette derived from the supplied Chapada Backpackers visual reference.
- No external frontend framework or build pipeline unless necessary to render the artifact.

### Excluded

- Database access or API integration.
- Authentication.
- Changes to the Maya V2 runtime, operational trace recorder, provider adapters, or production dashboard.
- Reservations, payments, handoffs, retries, messages, or any other effect capability.
- Production deployment.
- Real customer data, identifiers, conversations, or provider payloads.
- A dashboard-only identity or data contract.

## 3. Product Direction

The first screen is hybrid:

1. **Executive context at the top:** business and service health can be understood in a few seconds.
2. **Operational detail below:** the operator can identify which conversations, reservations, payments, or handoffs require attention.

The design should feel like a modern operational product rather than a marketing page. It may borrow interaction patterns from Stripe, Linear, and Intercom without cloning them.

## 4. Information Architecture

### 4.1 Left navigation

- Visão geral
- Atendimentos
- Reservas
- Pagamentos
- Handoffs
- Execuções
- Integrações
- Configurações

The mockup renders all destinations as interactive navigation states, but only **Visão geral** needs a fully composed page. Other items may show a lightweight “visual preview” state rather than separate complete applications.

### 4.2 Header

- Page title and short contextual subtitle.
- Date-range control.
- Search field for a fictional lead/service.
- Maya online indicator.
- Refresh control that simulates a refreshed timestamp.
- Operator avatar/menu affordance.

### 4.3 Executive KPI row

Eight compact cards:

- Leads atendidos
- Atendimentos ativos
- Reservas confirmadas
- Receita potencial
- Taxa de conversão
- Handoffs
- Pagamentos pendentes
- Falhas que exigem atenção

Each card contains:

- primary value;
- concise label;
- trend versus the previous period;
- subtle sparkline or status cue;
- accessible text that does not depend on color alone.

### 4.4 Analytics section

Primary modules:

- Commercial funnel: novos leads → qualificados → opção escolhida → reserva → pagamento.
- Service volume by day.
- Interest mix: passeios, hospedagem, pacotes.
- Reservation/payment status distribution.
- Handoff reason distribution.

Charts are rendered locally using HTML/CSS/SVG or Canvas. No remote chart dependency is required.

### 4.5 Operational table

Recent fictional services include:

- anonymous synthetic lead label;
- interest;
- current stage;
- last activity;
- Maya status;
- reservation status;
- payment status;
- handoff status;
- attention indicator.

Table interactions:

- status filter;
- interest filter;
- text search;
- selectable row;
- sorting simulation where useful;
- empty state if filters remove every record.

### 4.6 Detail drawer

Selecting a row opens a right-side drawer that preserves the dashboard context. It contains:

- synthetic service summary;
- current status and next expected step;
- high-level facts, never raw production payloads;
- visual execution journey;
- compact recent conversation preview using fictional text;
- reason for attention, when present.

Journey stages:

```text
Mensagem recebida
→ Interpretação da Maya
→ Consulta
→ Opção apresentada
→ Coleta de dados
→ Confirmação
→ Reserva
→ Pagamento
→ Entrega ou handoff
```

The mockup shows at least four journey conditions across its synthetic records:

- completed;
- currently active;
- handoff after full triage;
- operational failure requiring attention.

## 5. Visual System

### 5.1 Brand palette

| Token | Color | Use |
|---|---:|---|
| `brand-700` | `#245634` | Sidebar, primary brand surfaces |
| `brand-800` | `#173A27` | Strong text and dark brand accents |
| `brand-600` | `#005A2A` | Primary actions and active states |
| `sand-100` | `#F4E8D7` | App background |
| `ivory-50` | `#FFFCF7` | Cards, tables, drawer |
| `sand-300` | `#E4D5C0` | Borders and separators |
| `text-muted` | `#66766B` | Secondary text |
| `coral-500` | `#E85F67` | Brand accent and handoff |
| `success-500` | `#2F7D4A` | Success |
| `info-500` | `#327E8F` | Active/in progress |
| `warning-500` | `#C99135` | Pending/attention |
| `danger-600` | `#B7443E` | Failure/critical attention |
| `neutral-500` | `#A99C8A` | Neutral/disabled |

### 5.2 Typography

- Interface/body/numbers: a highly legible modern sans-serif available through a safe local/system stack.
- Display headings: a restrained serif or characterful fallback may be used sparingly to echo the brand.
- Numbers use tabular figures where supported.
- No remote font is required for the mockup to remain portable.

### 5.3 Shape and density

- Medium corner radius, approximately 12–16px for cards.
- Fine warm-beige borders.
- Minimal shadows.
- Generous but operationally efficient spacing.
- Strong hierarchy from size, weight, spacing, and grouping—not decoration.
- Line icons or lightweight inline SVG.

### 5.4 State semantics

- Success: green plus label/icon.
- Active: blue-teal plus motion or pulse used sparingly.
- Pending: mustard plus label/icon.
- Handoff: coral plus label/icon.
- Failure: earthy red plus label/icon.
- Neutral: warm gray plus label/icon.

Color is never the only signal.

## 6. Responsive Behavior

### Desktop

- Persistent sidebar.
- KPI grid.
- Two-column analytics section.
- Full operational table.
- Right-side detail drawer.

### Tablet

- Collapsible sidebar.
- KPI cards in two or four columns depending on width.
- Charts stack when necessary.
- Detail drawer overlays the page.

### Mobile

- Compact top navigation.
- KPI cards become a horizontally scrollable or two-column set.
- Charts stack vertically.
- Operational table becomes a card list.
- Journey becomes a vertical stage list.

Desktop quality is the primary approval surface, but the artifact must not break at narrower widths.

## 7. Interaction Contract

The mockup includes:

- sidebar active-state switching;
- range selector changing visible synthetic totals;
- search and filters affecting the synthetic service list;
- row selection opening the detail drawer;
- drawer close action and Escape support;
- chart hover/focus affordances where practical;
- simulated refresh timestamp;
- no network requests for business data;
- no forms that imply operational mutation.

Buttons and interactive elements must have visible focus states and meaningful labels.

## 8. Synthetic Dataset

Use a deterministic local dataset covering at least twelve fictional services and a mix of:

- Portuguese and English service labels where useful;
- lodging, activity, and package interests;
- active qualification;
- option selected;
- awaiting confirmation;
- booking confirmed;
- payment pending;
- completed payment;
- handoff after full triage;
- provider/operational failure;
- manual attention.

The UI must explicitly label the dataset as **Dados demonstrativos**. No real names, IDs, phone numbers, e-mails, messages, booking references, or payment links are used.

## 9. Future Data Integration Boundary

After visual approval, a separate specification will map components to the existing Maya V2 read-only operational projection. The future flow is:

```text
Authoritative V2 stores / typed operational trace
→ sanitized read-only projection API
→ dashboard view model
→ approved visual components
```

Rules for the future phase:

- The browser never opens SQLite directly.
- The existing V2 identity chain remains authoritative.
- The dashboard remains observation-only.
- Commercial KPIs must receive explicit definitions before implementation.
- Missing data is labeled unavailable rather than invented.
- Synthetic rows remain isolated from live history.

This mockup does not decide those contracts.

## 10. Acceptance Criteria

The visual mockup is approved when:

1. It opens as a standalone artifact without a production backend.
2. It clearly resembles the Chapada Backpackers brand while remaining operationally legible.
3. The executive summary is understandable within a few seconds.
4. Operators can move from a KPI/alert to a fictional service and inspect its journey.
5. Handoff, pending payment, reservation success, active work, and failure are visually distinct.
6. Filters, row selection, drawer, and refresh simulation work.
7. The layout remains usable on desktop and does not break on tablet/mobile.
8. Every record and metric is visibly demonstrative/synthetic.
9. The browser makes no business-data network request and exposes no effect action.
10. No Maya V2 runtime or production dashboard file is modified during mockup creation.

## 11. Deliverables

- `index.html`
- `styles.css`
- `app.js`
- `README.md` with local preview instructions
- optional local SVG/icon assets
- browser verification evidence or screenshots generated from synthetic data only

The implementation should favor a small, dependency-free artifact over a frontend framework or build system.