# Maya Ops — dashboard completo com dados existentes

## Estado e autoridade

- Solicitação original: visualizar o banco do agente, dados coletados, IDs de reserva, valores de reserva e pagamento e o histórico de atendimento.
- Direção funcional apresentada em 2026-08-30: refino visual, Visão por lead, Execuções, Reservas, Pagamentos, Handoffs, exportação e publicação reversível.
- Carlos Eduardo autorizou a próxima entrega em 2026-08-30.
- Esta especificação escrita aguarda a revisão final do operador antes do plano e da implementação, conforme o fluxo de design do projeto.
- Branch: `feature/maya-ops-existing-data-dashboard`.
- Worktree: `/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard`.
- Release pública de partida: `c228148e110a6b0f7aeb5ca43758db85e4b64f3e`.
- Rollback de partida: imagem e composição da release `c228148e110a` preservadas no diretório operacional.

## Objetivo

Transformar o Maya Ops de um painel centrado somente em traces em um painel operacional por lead que una, por identidades persistidas exatas:

1. dados coletados e diálogo;
2. estado comercial estruturado;
3. reservas e referências persistidas;
4. iniciações e liquidações de pagamento, sem confundi-las;
5. handoffs, quando existirem;
6. execuções, timeline vertical e Input/Output;
7. exportações CSV dos registros projetados.

A entrega continua estritamente observacional. Ela não altera o agente, não executa provider, não cria reserva ou pagamento, não reenvia mensagens e não oferece retry/replay/requeue.

## Abordagens consideradas

### A. Projeção multi-SQLite autenticada e somente leitura — escolhida

O serviço `/ops` abre somente um catálogo fechado de arquivos SQLite ativos em `mode=ro`, aplica `PRAGMA query_only=ON`, autentica tabelas/colunas esperadas e retorna DTOs públicos com campos permitidos.

Vantagens:

- dados atuais, sem esperar uma sincronização;
- joins explícitos por IDs duráveis;
- nenhuma tabela paralela para manter;
- browser não recebe SQL nem caminhos de banco;
- estados novos aparecem assim que são persistidos.

Custo:

- o snapshot entre arquivos distintos é eventualmente consistente, não uma transação global;
- o container precisa montar a raiz dos stores ativos como read-only;
- o reader precisa tolerar WAL e autenticar cada schema.

### B. ETL periódico para um novo SQLite de dashboard

Copiaria dados selecionados para um banco sanitizado separado.

Não escolhida porque introduz writer, scheduler, atraso, reconciliação e uma nova fonte de verdade para uma entrega que precisa apenas observar os stores existentes.

### C. Expor um visualizador de banco genérico

Não escolhida porque exporia tabelas, payloads e metadados fora do contrato do produto, além de exigir credencial de banco e não oferecer a experiência por lead solicitada.

## Fontes autorizadas

O serviço receberá uma segunda montagem read-only em `/data/records`. O código usará nomes fixos na raiz; não percorrerá subdiretórios e não lerá stores de sandbox.

| Arquivo | Tabelas autorizadas | Uso público |
|---|---|---|
| `inbox.sqlite3` | `inbound_events` | entrada, estado e horários; nunca `payload`, `claim_token` ou lease |
| `v2-private-customer.sqlite3` | `private_customer_facts`, `private_dialogue_turns`, `private_passenger_manifests` | fatos, conversa e manifesto estruturado |
| `v2-boundary.sqlite3` | `boundary_state`, `boundary_commands`, `boundary_public_outbox` | estado do lead, comandos, rascunho e entrega pública |
| `v2-execution.sqlite3` | `reservation_commands`, `workflows`, `execution_ledger` | comando e outcome da reserva |
| `v2-payment-initiation.sqlite3` | `payment_initiations`, `stripe_reconciliations`, `stripe_step_receipts` | obrigação e preparação do método/link |
| `v2-followup.sqlite3` | `payment_workflows`, `payment_events`, `payment_commands`, `payment_ledger`, `payment_receipts`, `handoff_workflows`, `handoff_events`, `handoff_receipts` | liquidação/recibo e handoff |
| `v2-bokun-audit.sqlite3` | `bokun_audit_tasks` | `booking_id` e auditoria Bókun quando persistidos |
| `v2-cloudbeds-audit.sqlite3` | `cloudbeds_audit_tasks` | `reservation_id` e auditoria Cloudbeds quando persistidos |
| `v2-public-outbox.sqlite3` | `public_outbox` | entrega pública por `lead_id` |
| store Ops dedicado | `executions`, `nodes` | execução, timeline e Input/Output existentes |

### Campos explicitamente excluídos

A projeção não retorna:

- credenciais, chaves, hashes de senha ou configuração secreta;
- claim tokens, owners, fencing tokens ou leases;
- ciphertext, nonce ou conteúdo criptografado;
- payload bruto de webhook;
- headers ou URLs assinadas de provider;
- prompts internos;
- stores ou dados de sandbox;
- campos não declarados nos DTOs públicos.

Mensagem do cliente, resposta da Maya e fatos fornecidos pelo cliente fazem parte da visão autenticada de atendimento solicitada. Eles não serão inferidos nem reclassificados.

## Semântica de identidade e joins

### Lead

A identidade pública é o `lead_id`/`lead_key` persistido. O catálogo de leads é a união exata de identidades presentes em:

- trace `executions.lead_id`;
- `inbound_events.lead_id`;
- `private_dialogue_turns.lead_id`;
- `private_customer_facts.lead_id`;
- `boundary_state.lead_key`;
- `boundary_commands.lead_key`;
- `public_outbox.lead_id`.

Strings diferentes nunca são unidas por semelhança, telefone, horário, valor ou conteúdo textual.

### Reserva

A cadeia autenticada é:

```text
boundary_commands.command_id
  = reservation_commands.command_id
  -> reservation_commands.workflow_id
  = workflows.workflow_id
  -> execution_ledger.command_id
  -> bokun_audit_tasks.command_id | cloudbeds_audit_tasks.command_id
```

`boundary_commands.lead_key` liga a reserva ao lead.

Rótulos permitidos:

- **Em preparação**: rascunho/comando sem outcome terminal confirmado;
- **Outcome registrado**: ledger terminal que não satisfaz a confirmação fechada;
- **Confirmada**: `certainty=effect_confirmed` e `normalized_status=confirmed` persistidos;
- **ID Bókun registrado**: somente `bokun_audit_tasks.booking_id` não vazio;
- **ID Cloudbeds registrado**: somente `cloudbeds_audit_tasks.reservation_id` não vazio;
- **Referência do provider**: `provider_reference` genérica do outcome, sem renomeá-la como Bókun ou Cloudbeds.

### Pagamento

Iniciação e liquidação são superfícies distintas:

- `payment_initiations.status=completed` informa que a iniciação terminou;
- `stripe_step_receipts.step=payment_link,status=accepted` permite o rótulo **Link Stripe preparado**;
- nenhum desses fatos significa que o cliente pagou;
- valor pago, liquidação e recibo vêm exclusivamente dos stores `payment_*` do follow-up;
- ausência de settlement é **Não registrado**, nunca `R$ 0,00` e nunca `Pago`.

A iniciação só aparece dentro de um lead se houver chave persistida idêntica entre o estado desse lead e `payment_id` ou `reservation_anchor_id`. Igualdade de valor, serviço, data ou horário não autoriza o vínculo.

### Handoff

Handoff só aparece dentro de um lead quando o estado persistido contém uma identidade exata que faça o vínculo. `lead_key_hash` sozinho não será revertido nem associado por tentativa.

## Estado factual testemunhado em 2026-08-30

A inspeção read-only atual provou:

- diálogo e fatos do cliente persistidos;
- um comando de reserva ligado a workflow e ledger;
- outcome de reserva com `certainty=effect_confirmed`, `normalized_status=confirmed` e referência genérica de provider;
- tabelas de auditoria Bókun e Cloudbeds sem ID final persistido no momento da inspeção;
- uma iniciação Stripe `completed`, reconciliação `matched` e etapas `product`, `price` e `payment_link` aceitas;
- nenhuma liquidação/recibo de pagamento no follow-up no momento da inspeção;
- nenhum handoff persistido no momento da inspeção.

Essas contagens são evidência de descoberta, não fixtures do produto. A UI e os testes públicos derivarão cardinalidade dos payloads autenticados atuais.

## Modelo público

### `LeadSummary`

- `lead_id`;
- `first_activity_at` e `last_activity_at`, derivados apenas de timestamps persistidos;
- `inbound_event_count`;
- `dialogue_turn_count`;
- `customer_fact_count`;
- `execution_count`;
- `reservation_count`;
- `payment_initiation_count`;
- `settled_payment_count`;
- `handoff_count`;
- `latest_execution_status` ou `null`.

Não há score, qualificação, conversão, valor potencial, interesse ou próximo passo inferido.

### `LeadDetail`

- resumo do lead;
- fatos com `fact_name`, valor persistido, origem e timestamp;
- perfil estruturado do payload de reserva quando existir: nome, telefone, e-mail, nascimento, gênero, país e `customer_ref`;
- turnos com mensagem do cliente e chunks da Maya;
- eventos inbound com estado e timestamp, sem payload bruto;
- entregas públicas com estado e timestamp;
- reservas ligadas por ID;
- pagamentos/handoffs ligados por ID exato;
- execuções do trace, em ordem cronológica, com botão para o drawer existente.

### `ReservationRecord`

- `lead_id`, `command_id`, `workflow_id`, `draft_id` e versão;
- operação, timestamps e status factual;
- componentes: serviço, rótulo público, data inicial/final, horário, adultos, crianças, disponibilidade registrada, `offer_id`, `provider_ref`, `lookup_id`, valor e moeda;
- customer e método de pagamento estruturados;
- outcome: certeza, status normalizado e referência genérica;
- IDs Bókun/Cloudbeds somente quando presentes nas tabelas dedicadas.

### `PaymentRecord`

Duas categorias no mesmo contrato fechado:

1. `kind=initiation`: `initiation_id`, `payment_id`, `reservation_anchor_id`, método, valor devido, moeda, `due_kind`, estado da iniciação, reconciliação e etapas;
2. `kind=settlement`: `payment_id`, versão, estado do workflow/ledger, valor pago quando persistido, certeza do outcome, recibo e timestamps.

`lead_id` pode ser `null` quando não houver join exato.

### `HandoffRecord`

- IDs do handoff/incidente;
- estado e timestamps persistidos;
- recibos/eventos permitidos;
- `lead_id` somente com vínculo exato; caso contrário `null`.

## API

Novos endpoints GET autenticados:

```text
GET /ops/api/records
GET /ops/api/leads/{lead_id}
GET /ops/api/exports/{dataset}.csv
```

`dataset` é fechado em:

```text
leads | executions | reservations | payments | handoffs | lead-history
```

`lead-history` exige `lead_id`. Todas as outras consultas rejeitam parâmetros desconhecidos.

### Contratos comuns

- autenticação acontece antes de parse, read ou export;
- payloads JSON possuem allowlist recursiva;
- listas são limitadas a 200 itens por coleção e retornam `truncated=true` quando aplicável;
- `generated_at` é UTC e não representa atomicidade global entre arquivos;
- ETag é derivado do corpo público exato;
- `If-None-Match` retorna `304`;
- cache local de no máximo 2 segundos;
- erro de schema, I/O ou JSON vira `503 {"status":"records_source_unavailable"}` sem causa interna;
- nenhum endpoint POST/PUT/PATCH/DELETE novo;
- conteúdo CSV usa UTF-8 com BOM, RFC 4180, colunas fechadas e neutralização de células iniciadas por `=`, `+`, `-` ou `@`;
- exportações não contêm blobs, hashes, tokens, ciphertext ou payload bruto.

## Reader multi-store

Criar `v2_ops/records.py` com responsabilidade exclusiva por:

1. resolver somente os nomes de arquivo fechados sob o diretório configurado;
2. rejeitar symlink ou caminho fora da raiz;
3. abrir cada SQLite com URI `mode=ro` e `PRAGMA query_only=ON`;
4. autenticar o subconjunto obrigatório de colunas de cada tabela;
5. materializar DTOs imutáveis;
6. validar JSON estruturado sem interpretar linguagem natural;
7. fechar conexões após cada snapshot;
8. expor um `change_token()` baseado apenas nos arquivos allowlisted e sidecars WAL/SHM.

O reader não importa adaptadores de provider, writers, workers, modelos ou módulos V3.

A consistência é por snapshot eventual: as conexões são abertas e transações de leitura iniciadas em uma janela curta, mas não se promete transação ACID entre arquivos diferentes.

## Configuração e montagem

Adicionar a `OpsWebSettings`:

```python
records_path: Path | None = None
```

Ambientes produtivos definem:

```text
V2_OPS_RECORDS_PATH=/data/records
V2_OPS_RECORDS_DATA_DIR=<raiz ativa ga-state no host>
```

O Compose monta:

```text
${V2_OPS_DATA_DIR}:/data/ops:ro
${V2_OPS_RECORDS_DATA_DIR}:/data/records:ro
```

Regras:

- root filesystem continua `read_only: true`;
- ambas as montagens têm `RW=false`;
- `/tmp` continua tmpfs limitado;
- serviço recebe zero credenciais de provider;
- API, worker e router GA não são recriados;
- código lê apenas os nomes raiz declarados, ignorando `sandbox-*`;
- healthcheck prova o serviço web; o dark smoke prova todos os endpoints e fontes.

## Interface e navegação

### Menu

1. Visão geral
2. Leads
3. Execuções
4. Reservas
5. Pagamentos
6. Handoffs

Todos são botões funcionais com `aria-current=page` único. O menu mobile fecha após a navegação e restaura foco corretamente.

### Visão geral

Preserva os oito KPIs e cinco análises técnicas existentes. O período `24h|7d|30d` continua valendo somente para Visão geral e Execuções. A tabela de execuções deixa de ser parte da Visão geral e passa para a tela própria.

### Leads

- todo o histórico registrado, independente do seletor de período;
- busca exata/parcial somente sobre IDs e campos estruturados renderizados; nenhuma classificação semântica de mensagens;
- tabela desktop e cards mobile;
- clique abre a Visão do lead no conteúdo principal;
- botão de exportação `leads`.

### Visão do lead

Ordem visual:

1. identidade e datas;
2. perfil/dados coletados com origem factual;
3. reservas e pagamentos ligados;
4. histórico de atendimentos;
5. execuções cronológicas.

Cada execução abre o drawer existente e preserva a origem de foco. O conteúdo do lead continua visível atrás do drawer.

### Execuções

- tela própria usando a tabela/cards e filtros atuais;
- período ativo;
- link para a Visão do lead;
- drawer com timeline vertical, Input e Output simultâneos, metadados e erro;
- nenhuma mudança na ordem dos nós ou no fallback ordinal.

### Reservas

- todo o histórico registrado;
- busca por lead, command/workflow/draft/provider ID e status direto;
- tabela desktop e cards mobile;
- componentes do mesmo draft agrupados visualmente;
- exportação CSV.

### Pagamentos

- seção **Iniciações** separada de **Liquidações**;
- estados vazios independentes;
- valor devido nunca é rotulado como valor pago;
- iniciações sem join aparecem como **Lead não vinculado**;
- exportação CSV.

### Handoffs

- lista factual ou estado vazio: **Nenhum handoff registrado**;
- não transforma ausência em zero de sucesso/falha;
- exportação CSV.

## Refino visual incorporado

A especificação `2026-08-23-maya-ops-conservative-visual-refinement-design.md` faz parte desta entrega:

- identidade verde/areia preservada;
- contraste de texto e borda reforçado;
- chips e fatos com tipografia mínima legível;
- hover, foco e seleção distintos;
- passo selecionado com fundo verde suave e marcador atual;
- timeline mobile sem scroll interno curto; o drawer usa scroll natural;
- densidade e espaçamento harmonizados;
- sem nova dependência frontend e sem ícone improvisado.

## Estados de erro e vazio

- falha do trace preserva o snapshot técnico anterior e mostra alerta técnico;
- falha da fonte records preserva o último snapshot de records e mostra alerta independente;
- telas sem registros mostram estados vazios específicos, não números inventados;
- falha de uma navegação supersedida não apaga a tela atual;
- SSE continua único e dispara refresh do dashboard e do records snapshot com epochs independentes;
- detalhe de lead e detalhe de execução possuem epochs independentes;
- nenhum erro interno, path ou SQL aparece no browser.

## TDD e verificação

### Reader e projeção

Fixtures SQLite reais cobrem:

- união por lead ID exato;
- não-join de IDs apenas semelhantes;
- reserva em preparação;
- reserva confirmada por certainty/status fechados;
- referência genérica sem falso rótulo Bókun/Cloudbeds;
- iniciação Stripe concluída sem pagamento liquidado;
- pagamento liquidado somente com ledger/receipt;
- payment/handoff não vinculado quando falta ID exato;
- ausência honesta;
- schema drift e JSON inválido como erro sanitizado;
- DB/WAL/SHM inalterados por leituras repetidas.

### API

Cobrir:

- auth antes de parse/read;
- payloads allowlisted e ETag/304;
- limites e `truncated`;
- CSV fechado e neutralização de fórmula;
- 503 sanitizado;
- matriz GET-only e ausência de efeito.

### UI estática

Atualizar o inventário semântico fechado para os novos controles, views e downloads GET. Continuam proibidos:

- handlers inline;
- `innerHTML`, `insertAdjacentHTML`, `eval`;
- styles/handlers inline gerados para dados;
- write controls ou destinos externos;
- gráfico/zoom na timeline.

### Chromium real

Um único teste, um worker e zero skips, nos viewports:

- `1440x1000`;
- `390x844`.

O smoke deve:

- autenticar;
- navegar por todas as seis telas;
- derivar contagens do payload autenticado;
- abrir um lead e provar perfil, diálogo, reserva e execuções da fixture;
- abrir uma execução pela Visão do lead e pela tela Execuções;
- selecionar passos e provar Input/Output;
- validar estados de reserva confirmada, iniciação não liquidada e handoff vazio;
- baixar e inspecionar CSV;
- provar timeline mobile sem scroll interno próprio;
- provar ausência de overflow horizontal;
- falhar em `console.error`, page error ou request failure.

## Revisão e release

Antes do build:

1. revisão de aderência à especificação;
2. revisão de qualidade e ausência de claims não suportados;
3. testes focados e suíte `tests/test_v2_ops_*.py` em ambiente limpo;
4. Chromium local;
5. `compileall`, diff-check e scans de write/secret/source.

Release:

1. congelar commit/tree e buildar `Dockerfile.v2-ops` com OCI revision exata;
2. criar backups e manifesto de rollback da release `c228148e110a`;
3. dark smoke autenticado com fontes read-only e rootfs read-only;
4. comprovar mounts `RW=false` e zero alteração em snapshots offline;
5. comparar Compose com allowlist limitada ao serviço `v2-ops`;
6. recriar somente `v2-ops`;
7. provar que API, worker e router mantiveram IDs/start times;
8. smoke público autenticado desktop/mobile, downloads e APIs;
9. registrar release e rollback;
10. reverter imediatamente se qualquer gate falhar.

## Critérios de aceite

A entrega está aceita somente quando:

- o operador navega por Leads, Execuções, Reservas, Pagamentos e Handoffs;
- um lead combina dados coletados, diálogo, reservas e execuções por joins exatos;
- reserva confirmada e referência genérica são rotuladas conforme o outcome persistido;
- iniciação Stripe não é apresentada como pagamento realizado;
- valor pago permanece não registrado sem settlement/receipt;
- IDs Bókun/Cloudbeds aparecem somente nas tabelas dedicadas;
- CSVs são autenticados, fechados e baixáveis;
- nenhum banco, sidecar ou registro de negócio é modificado;
- nenhuma rota de efeito é adicionada;
- desktop/mobile/timeline/Input/Output permanecem funcionais;
- produção fica na imagem exata da nova release, com rollback imediato disponível;
- apenas o serviço `v2-ops` é recriado.
