# Maya Ops — verificação final do redesenho de fidelidade ao mockup

**Data:** 2026-08-22
**Decisão:** **IMPLEMENTATION VERIFIED — NOT DEPLOYED**

## 1. Autoridade, identidade e escopo

- Worktree: `/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard`.
- Branch: `feature/maya-ops-existing-data-dashboard`.
- Base/spec commit: `37d82b1290661382f18dda713a1db20732cd99f8`; tree `276ed214e70931665da6c11696418c9ef39432c1`; subject `docs(v2-ops): specify mockup-fidelity redesign`.
- Implementation SHA autenticado e exercitado antes deste commit documental: `2ed7c1558f93c27e3b8e6dfc775ac36e3b2598f0`.
- Implementation tree: `2e4d68736301aa17324a4b42f7a082d8e3bedd6a`.
- Subject do candidato: `fix(v2-ops): close visual smoke hygiene contracts`.
- Estado inicial da Task 7: worktree limpa, index vazio e zero temporários atribuíveis ao smoke.
- O commit final desta Task 7 é deliberadamente documental. Seu SHA não pode constar dentro de seus próprios bytes; a identidade deve ser autenticada por `git show --format=%H` após o commit.

O conjunto funcional/de controle do redesenho, conforme a autoridade, é exatamente:

```text
docs/refactor/ACTIVE.md
tests/browser/ops_dashboard_smoke.py
tests/test_v2_ops_ui.py
v2_ops/static/index.html
v2_ops/static/ops.css
v2_ops/static/ops.js
```

O intervalo Base..implementation também contém o plano versionado `docs/superpowers/plans/2026-08-22-maya-ops-mockup-fidelity-redesign.md`, que é governança da própria mudança, não produto/backend. Este commit final adiciona somente este relatório e uma atualização em `docs/refactor/ACTIVE.md`.

Hashes SHA-256 dos cinco bytes de produto/teste no implementation SHA:

```text
74d9518bf7043119196c6bafe7751381dbc9fa3b3625b75f70058191d61656b9  tests/browser/ops_dashboard_smoke.py
7d3ae649c2dcc584afb2035a552c76fc2c93d9af0cb7123599ca08f283970c4a  tests/test_v2_ops_ui.py
cf5daa7999b051d96db3d9c6cda959f93a5808154a1131e45e06f4d743d9cadd  v2_ops/static/index.html
5a6ad16d7a883406cf3e6efd5a291b6bf53841e25681d38b3359efdd16986288  v2_ops/static/ops.css
9aeb8fac5093f220eba57e3ff14b019dd81b19ad83f32a27c6461e31c5f75daa  v2_ops/static/ops.js
```

## 2. Regressão fresh pré-commit

### Probe bruto sem qualificação

O comando repository-wide literal foi executado uma única vez e preservado como evidência de contratos históricos incompatíveis:

```text
7 failed, 2023 passed, 1 warning, 2953 subtests passed in 238.28s
exit=1
```

Os sete nós são exatamente os sete contratos Phase 7/entry explicitamente deselectados pelo workflow canônico `.github/workflows/phase8.yml`: três em `tests/test_phase7_closeout.py`, três em `tests/test_phase7_package.py` e um em `tests/test_phase8_entry.py`. Eles não são descritos como passing, skipped ou corrigidos.

### Gate canônico Phase 8 qualificado em clean env

Com os sete node IDs literais do brief deselected:

```text
2023 passed, 7 deselected, 1 warning, 2953 subtests passed in 229.56s
exit=0
```

Warning único e preexistente: `StarletteDeprecationWarning` de `fastapi.testclient` sobre `httpx`/`httpx2`.

### No-effect/deploy e Chromium determinístico

```text
venv/bin/python -m pytest tests/test_v2_ops_no_effect_surface.py tests/test_v2_ops_deploy_contract.py -q
7 passed in 0.52s
exit=0

venv/bin/python -m pytest tests/test_v2_ops_ui.py::test_javascript_runs_adversarial_interleavings_in_real_chromium -q -s
Running 24 tests using 1 worker
24 passed
exit=0

venv/bin/python tests/browser/ops_dashboard_smoke.py
ops_dashboard_smoke=PASS
exit=0
```

O Chromium é real, pinado (`mcr.microsoft.com/playwright:v1.55.0-noble`, digest autenticado pela harness), hard-fail e sem skip como evidência.

## 3. Superfície HTTP read-only

A introspecção fresh de `create_ops_app` produziu:

```text
GET  /ops
GET  /ops/
GET  /ops/api/dashboard
GET  /ops/api/events
GET  /ops/api/executions
GET  /ops/api/executions/{execution_id}
GET  /ops/api/executions/{execution_id}/nodes
GET  /ops/api/executions/{execution_id}/nodes/{node_id}/full
GET  /ops/api/harness
GET  /ops/api/release
GET  /ops/healthz
GET  /ops/login
POST /ops/login
POST /ops/logout
GET  /ops/static/ops.css
GET  /ops/static/ops.js
```

Assertivas: POST existe somente em `/ops/login` e `/ops/logout`; não há PUT, PATCH ou DELETE.

## 4. Invariância DB/WAL/SHM

A auditoria bounded fresh usou fixture técnica temporária criada por `SQLiteOpsTraceWriter`, fechou o writer, autenticou a aplicação e registrou metadados individuais de DB, WAL e SHM. Em seguida executou:

- dashboard `24h`, `7d` e `30d` com status 200;
- revalidação ETag correspondente com status 304 em cada faixa;
- execução individual e lista de nodes com status 200;
- full Input e full Output permitidos com status 200;
- startup bounded da conexão de events.

O snapshot estruturado `existence + size + mtime_ns + SHA-256` de cada membro existente permaneceu igual antes/depois da sequência autenticada, e nenhum sidecar ausente foi materializado pela sequência de GETs. Resultado literal terminal: `db_wal_shm_invariance=PASS`.

Tentativas auxiliares posteriores de reconstruir o helper sem preservar a mesma ordem de warm-up observaram criação/mtime de sidecars pela abertura local do reader e foram descartadas como falha de harness, não como regressão de GET ou produto; não substituem a auditoria autenticada que passou antes do timeout.

## 5. Evidência visual

### Hashes históricos pré-rerun preservados

| Artefato | Dimensões | Bytes | SHA-256 |
|---|---:|---:|---|
| desktop | 1440×1543 | 239153 | `aca56358d94a331339ec86ea6ec31adac749d1a7d78143dda00c15092fa338aa` |
| mobile | 390×3209 | 190972 | `9afac55274d3c8bf6fd456136a45220f3ee35a4ea606513a062f299cb904a08a` |
| drawer desktop | 1440×1000 | 166024 | `a52e4490da0a4a39f7d8880448ba184eed0ae4220250a3e236d0cf528130dfdc` |
| drawer mobile | 390×844 | 34186 | `23782c6577a641aff0dc1e2899f80629c4aacbee2df782b0bff9f19e1c8089f6` |

### Smoke fresh pré-commit vinculado ao implementation SHA

| Viewport/arquivo | Dimensões | Bytes | SHA-256 |
|---|---:|---:|---|
| desktop — `desktop-1440x1000.png` | 1440×1543 | 239008 | `967b7075b0a08af2bd867fff65be286db334e8241cb1f2a4df3243b51b26099c` |
| mobile — `mobile-390x844.png` | 390×3209 | 190807 | `cae1f8edf196c0fff3492b20533ac536bf61fed8a9dcfe3133b37058950e35fc` |
| drawer desktop — `drawer-desktop-1440x1000.png` | 1440×1000 | 165979 | `6b9540e46e6bb837d5ac4d91fb2e61de2a1ebab18af67787921b628bf64010aa` |
| drawer mobile — `drawer-mobile-390x844.png` | 390×844 | 35825 | `afb203253e37c7308d4113f5ca88b444edf56f1b502b79f26683d31af7ab5402` |

Veredito visual: fidelidade estrutural aprovada, não mera troca de paleta. Desktop preserva sidebar/header, oito KPIs em 4+4, analytics em 2+3 e tabela operacional; mobile preserva oito KPIs em duas colunas, analytics empilhada e cards sem overflow; drawers desktop/mobile mantêm contexto, canvas e Input/Output legíveis. Não foram observados clipping, overlap ou overflow horizontal bloqueantes.

Diferenças intencionais e factuais em relação ao mockup comercial: ausência de funil, receita, conversão, interesse, reserva/pagamento e filler sintético; densidade deriva exclusivamente dos poucos registros técnicos persistidos. PNGs incluem conteúdo temporal do smoke, portanto um rerun posterior nos mesmos bytes de produto pode produzir hashes diferentes.

## 6. Conteúdo proibido, efeitos e fronteiras

O scan case-insensitive de `index.html + ops.js` rejeitou e não encontrou:

```text
Dados demonstrativos
Receita potencial
Taxa de conversão
Novos leads
Qualificados
```

Resultado: `forbidden_content=PASS`.

Não houve mudança em backend/API, agente, prompt, tool, runtime, instrumentação, schema/writer, adapter/provider, deploy ou V3. A superfície continua read-only, sem replay/retry/effect endpoint e sem provider write. Não houve trabalho ou coleta de PII.

## 7. Rollback, promoção e estado terminal

A release já publicada antes desta branch permanece o rollback operacional. Sua identidade não foi sondada nesta Task 7 porque acesso à produção estava proibido. O candidato local não foi publicado.

Ações deliberadamente não executadas:

- sem push ou PR;
- sem build de imagem;
- sem deploy/recreate/restart;
- sem acesso ou smoke de produção;
- sem reserva, pagamento, ManyChat, handoff ou outro efeito externo;
- sem V3.

O rerun pós-commit deve executar exatamente a suíte qualified clean-env e o smoke real do brief. Como o commit altera somente documentação, a validade do resultado será transportada apenas após provar byte-identidade dos cinco paths de produto/teste com `2ed7c15`; hashes de PNG pós-commit, por serem temporais, pertencem ao scratch report ignorado final e podem diferir desta tabela.

**Decisão final deste artefato:** **IMPLEMENTATION VERIFIED — NOT DEPLOYED**.
