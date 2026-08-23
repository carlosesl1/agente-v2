# Maya Ops Visual Alignment and Lucide Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir a geometria dos controles, o indicador de atividade duplicado e as quebras ruins dos KPIs, substituindo pictogramas improvisados por um subconjunto local e rastreável de Lucide, e publicar somente o serviço `/ops` após Chromium e revisão independentes.

**Architecture:** O frontend continuará sem build step e sem dependência de rede. Um sprite SVG local, contendo somente símbolos oficiais Lucide e sua atribuição/proveniência, ficará no HTML já autenticado; `ops.js` criará instâncias com `<svg><use>` via DOM seguro. CSS adotará tokens explícitos para altura dos controles e áreas flexíveis dos KPIs; os gates browser-real medirão a geometria em desktop/mobile.

**Tech Stack:** HTML5, CSS, JavaScript DOM seguro, Lucide SVG icons vendorizados localmente, pytest, Playwright/Chromium, FastAPI existente, Docker Compose.

## Global Constraints

- Seletor de período, botão Atualizar e botão do operador usam uma altura-base comum de 48 px.
- Exibir exatamente um ponto verde no indicador de atividade.
- Os oito cards KPI mantêm altura, padding e hierarquia uniformes.
- “Execuções no período” não pode quebrar palavra por palavra.
- Usar somente ícones oficiais Lucide empacotados localmente; nenhuma CDN ou dependência de rede em runtime.
- Não desenhar paths SVG ad hoc; registrar versão, licença e origem do subconjunto Lucide.
- Sem mudança de contrato em API, autenticação, SQLite, agente, runtime, providers ou V3.
- A aplicação permanece autenticada e read-only.
- Sem `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` ou handlers inline.
- Preservar um único startup, um único `EventSource`, um único `MediaQueryList('(max-width: 720px)')` e um listener de breakpoint.
- Chromium real usa exatamente 1 worker e zero skips.
- O deploy recria somente `v2-ops`, preserva SQLite read-only e mantém rollback para `616b926`.

---

### Task 1: Provas causais e subconjunto local Lucide

**Files:**
- Modify: `tests/test_v2_ops_ui.py`
- Modify: `v2_ops/static/index.html`
- Modify: `v2_ops/static/ops.js`
- Create: `v2_ops/static/LUCIDE-NOTICE.txt`

**Interfaces:**
- Consumes: `renderKpis()`, DOM estático do shell e a rota autenticada já existente `/ops/`.
- Produces: `createLucideIcon(name: string, className?: string): SVGElement` e símbolos com ids `lucide-<name>` no sprite local.

- [ ] **Step 1: Escrever testes estáticos RED para proveniência, biblioteca local e remoção de pictogramas.**

Adicionar testes que leem `index.html`, `ops.js` e `LUCIDE-NOTICE.txt` e exigem:

```python
assert '<svg class="lucide-sprite"' in html
assert 'id="lucide-refresh-cw"' in html
assert 'id="lucide-search"' in html
assert 'function createLucideIcon(name, className = "")' in js
assert 'setAttribute("href", `#lucide-${name}`)' in js
assert 'Lucide' in notice and 'ISC License' in notice
for improvised in ('⌂', '⌘', '☰', '↻', '⌕', '◎', '◇', '◌', '◷'):
    assert improvised not in html + js
```

Também exigir que `KPI_DEFINITIONS` use nomes de ícones Lucide e que o sprite contenha exatamente o conjunto referenciado pelo shell e pelos oito KPIs.

- [ ] **Step 2: Rodar o teste focal e autenticar RED.**

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_ops_ui.py -k 'lucide or visual_geometry'
```

Expected: FAIL porque o sprite, notice e helper ainda não existem e os pictogramas ainda estão presentes.

- [ ] **Step 3: Vendorizar somente os ícones necessários a partir do pacote oficial Lucide.**

Usar um diretório temporário fora do repo para instalar/baixar uma versão fixada de `lucide`/`lucide-static`, copiar os elementos oficiais necessários e registrar em `LUCIDE-NOTICE.txt`:

```text
Lucide Icons <VERSION>
Source: https://github.com/lucide-icons/lucide
License: ISC License
Vendored subset: <sorted icon names>
```

No topo do sprite, manter comentário de proveniência. O sprite usa `<symbol id="lucide-..." viewBox="0 0 24 24">` com os nodes oficiais, `stroke="currentColor"`, `fill="none"`, `stroke-width="2"`, `stroke-linecap="round"` e `stroke-linejoin="round"`.

- [ ] **Step 4: Implementar instanciação DOM-safe e substituir pictogramas.**

Adicionar em `ops.js`:

```javascript
function createLucideIcon(name, className = "") {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("class", `lucide ${className}`.trim());
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NAMESPACE, "use");
  use.setAttribute("href", `#lucide-${name}`);
  svg.append(use);
  return svg;
}
```

Trocar `KPI_DEFINITIONS` para nomes Lucide e trocar `iconElement.textContent = icon` por `iconElement.append(createLucideIcon(icon))`. No HTML, trocar os caracteres do menu, refresh, busca e fechar por `<svg class="lucide" ...><use href="#lucide-..."></use></svg>`; manter todo texto acessível nos labels existentes.

- [ ] **Step 5: Rodar testes focais e contratos safe-DOM.**

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_ops_ui.py -k 'lucide or safe or javascript_contract or interactive'
```

Expected: PASS, sem novos sinks inseguros e sem mudança no inventário de controles.

- [ ] **Step 6: Commit.**

```bash
git add tests/test_v2_ops_ui.py v2_ops/static/index.html v2_ops/static/ops.js v2_ops/static/LUCIDE-NOTICE.txt
git commit -m "fix(v2-ops): adopt local lucide icon subset"
```

---

### Task 2: Geometria consistente em desktop e mobile

**Files:**
- Modify: `tests/test_v2_ops_ui.py`
- Modify: `tests/browser/ops_dashboard_smoke.py`
- Modify: `v2_ops/static/ops.css`

**Interfaces:**
- Consumes: classes `.range-control`, `.icon-button`, `.operator-button`, `.health-pill`, `.kpi-card`, `.kpi-foot`, `.kpi-series`, `.sparkline` e os oito cards produzidos por `renderKpis()`.
- Produces: testemunhos browser-real para `headerGeometry`, `healthIndicatorCount`, `kpiGeometry` e `horizontalOverflow` em 1440×1000 e 390×844.

- [ ] **Step 1: Escrever prova browser RED da geometria observada nas capturas.**

No smoke Playwright, medir com `getBoundingClientRect()` e exigir:

```javascript
const heights = await page.locator('#range-select, #refresh-dashboard, #operator-menu')
  .evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
if (!heights.every(height => Math.abs(height - 48) <= 1)) throw new Error('header control height');
if (await page.locator('.health-pill .online-dot').count() !== 1) throw new Error('health dot count');
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
if (overflow > 1) throw new Error('horizontal overflow');
```

Para KPIs, exigir oito cards, diferença máxima de altura ≤ 1 px por linha, containment integral de `.kpi-top`, `.kpi-value`, `.kpi-foot`, `.kpi-series` e `.sparkline`, e verificar que o texto “Execuções no período” não possui retângulos de linha com largura equivalente a uma única palavra em sequência. Rodar nos dois viewports.

- [ ] **Step 2: Rodar Chromium focal e autenticar RED.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected: FAIL no candidato atual por altura divergente dos controles, ponto duplicado ou quebra/containment do texto.

- [ ] **Step 3: Implementar CSS mínimo para os controles e indicador.**

Aplicar:

```css
:root { --header-control-height: 48px; }
.header-actions { align-items: flex-end; }
.range-control { display: grid; grid-template-rows: auto var(--header-control-height); }
.range-control select,
.icon-button,
.operator-button { height: var(--header-control-height); min-height: var(--header-control-height); }
.icon-button, .operator-button { align-self: end; }
#live-state::before { content: none; }
.health-pill { grid-template-columns: 8px minmax(0, 1fr); min-height: 48px; }
.health-pill .online-dot { grid-row: 1 / 3; align-self: center; }
.lucide { width: 18px; height: 18px; stroke: currentColor; fill: none; }
```

No breakpoint mobile, manter os controles visíveis com 48 px e ocultar somente cópia auxiliar já prevista, sem mudar o touch target.

- [ ] **Step 4: Implementar CSS mínimo para KPIs.**

Aplicar layout vertical previsível:

```css
.kpi-card { display: flex; min-width: 0; flex-direction: column; }
.kpi-card .kpi-value { flex: 0 0 auto; }
.kpi-card .kpi-foot { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; }
.kpi-card .kpi-foot > span { min-width: 0; overflow-wrap: normal; word-break: normal; }
.kpi-series { display: grid; grid-template-columns: minmax(74px, auto) 66px; align-items: end; }
.kpi-series > span { max-width: none; white-space: normal; overflow-wrap: normal; word-break: keep-all; }
.sparkline { overflow: hidden; }
```

No mobile, permitir que `.kpi-series` ocupe a largura interna, sem estreitar a coluna textual abaixo da maior palavra, e preservar containment.

- [ ] **Step 5: Rodar gates focais e Chromium GREEN.**

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_ops_ui.py
PYTHONDONTWRITEBYTECODE=1 venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected: todos os testes UI passam; smoke imprime `ops_dashboard_smoke=PASS`; Chromium usa 1 worker e zero skips; nenhum PNG/cache/SQLite temporário fica sujo.

- [ ] **Step 6: Commit.**

```bash
git add tests/test_v2_ops_ui.py tests/browser/ops_dashboard_smoke.py v2_ops/static/ops.css
git commit -m "fix(v2-ops): align controls and kpi geometry"
```

---

### Task 3: Revisão integral, publicação reversível e smoke público

**Files:**
- Modify only if evidence requires: `docs/superpowers/reports/2026-08-23-maya-ops-visual-alignment-icons-verification.md`
- Operational artifacts outside repo: `/home/ubuntu/workspace/agente-v2-ops-deploy/`

**Interfaces:**
- Consumes: candidato Git revisado, Dockerfile `Dockerfile.v2-ops`, compose/env predecessor e credenciais operacionais sem imprimi-las.
- Produces: imagem rotulada pelo SHA, manifest de deploy, rollback para `616b926`, smoke autenticado e Chromium público desktop/mobile.

- [ ] **Step 1: Executar revisão independente do diff desde `9295c9d`.**

Gerar pacote imutável com `review-package 9295c9d HEAD`. A revisão deve emitir dois veredictos: conformidade e qualidade. Corrigir e re-revisar qualquer Critical/Important antes de avançar.

- [ ] **Step 2: Executar verificação pré-deploy fresca.**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  HERMES_LEADS_AGENT_CONFIG_PATH="$PWD/config/agent.yaml" \
  venv/bin/python -m pytest -q tests/test_v2_ops_ui.py
PYTHONDONTWRITEBYTECODE=1 venv/bin/python tests/browser/ops_dashboard_smoke.py
python -m compileall -q v2_ops tests/test_v2_ops_ui.py tests/browser/ops_dashboard_smoke.py
```

Expected: UI PASS, smoke PASS, compile PASS, zero skips e worktree versionada limpa.

- [ ] **Step 3: Push autenticado e build somente da imagem `/ops`.**

Push da branch no SHA exato; build `agente-v2-ops:<sha12>` com labels `org.opencontainers.image.revision` e `created`. Não reconstruir nem reiniciar serviços GA.

- [ ] **Step 4: Dark smoke contra a fonte real read-only.**

Executar o candidato fora do roteador público usando o mesmo ambiente resolvido do predecessor, `PYTHONDONTWRITEBYTECODE=1`, rootfs read-only e mount SQLite `:ro`. Exigir health, login browser-real, assets, release SHA, dashboard 24h/7d/30d, API representativa, invariância DB/WAL/SHM e logs sem 500/503.

- [ ] **Step 5: Cutover reversível somente de `v2-ops`.**

Criar compose/env candidate-specific a partir do predecessor escapado byte a byte, alterar somente metadata allowlisted, validar renders sem warnings e instalar trap que restaura o compose/env de `616b926` se qualquer assert falhar. Executar `docker compose ... up -d --no-deps --force-recreate v2-ops`.

- [ ] **Step 6: Smoke público autenticado e Chromium.**

Em `https://hermes.chapadabackpackers.com/ops`, exigir health, login, SHA da release, CSS/JS, oito KPIs, exatamente um ponto de atividade, controles 48 px, texto sem quebra palavra por palavra, containment, zero overflow, desktop/mobile, console sem erros e logs recentes limpos.

- [ ] **Step 7: Promover ponteiros operacionais e registrar evidência.**

Somente após smoke público, alinhar `compose.yaml`, `v2-ops.env` e `deployment.json` ao candidato; manter artefatos candidate-specific e rollback estável para `616b926`. Escrever relatório com hashes, comandos/resultados, container/image IDs, identidades GA before/after e limitação dos dados observados.

- [ ] **Step 8: Commit documental final se o relatório for versionado.**

```bash
git add docs/superpowers/reports/2026-08-23-maya-ops-visual-alignment-icons-verification.md
git commit -m "docs(v2-ops): verify visual alignment release"
git push origin HEAD:feature/maya-ops-existing-data-dashboard
```
