# Plano faseado de entrega

Cada fase tem objetivo, entregáveis, não objetivos, gate e rollback. Somente uma fase fica ativa.

## Fase 0 — Baseline e governança

**Objetivo:** criar trilha limpa, reproduzível e auditável.

**Entregáveis:**

- repositório `agente-v2`;
- charter, baseline, taxonomia, arquitetura, roadmap, validação e riscos;
- evidência sanitizada do código/runtime atual;
- ADRs iniciais;
- validador local e CI;
- primeiro commit remoto.

**Não faz:** alteração do runtime, deploy, WhatsApp ou provider.

**Gate:** documentação válida, evidências hashadas, segredo/PII ausente, commit e remoto verificados.

## Fase 1 — Caracterização e corpus de incidentes

**Objetivo:** fazer o legado falhar de forma reproduzível antes da refatoração.

**Entregáveis:**

- fixtures sanitizadas de ManyChat e provider reads;
- harness desde payload bruto e estado vazio;
- cenário por F01–F22 aplicável;
- testes concorrentes e temporais;
- relatório de cobertura de incidentes;
- baseline de comportamento aceito/não aceito.

**Obrigatório:** incluir `n°/nº`, confirmação dupla, state ausente, lookup vencido, 120/300, handoff sem e-mail, outcome composto, webhook duplicado, crash em fronteiras.

**Gate:** cada incidente reproduzido ou justificado como não reproduzível; nenhum teste começa injetando a condição que deveria construir.

**Rollback:** somente testes/fixtures; sem integração live.

## Fase 2 — Domínio tipado e reducer puro

**Objetivo:** implementar FSM sem FastAPI, Hermes ou provider.

**Entregáveis:**

- tipos discriminados;
- reducer total;
- assinatura canônica;
- invariantes e property-based tests;
- tabela completa estado/evento;
- serializer versionado.

**Gate:** zero sequência produz write prematuro ou segundo comando; eventos duplicados/fora de ordem são seguros; 100 mil sequências property-based.

## Fase 3 — Consultas e seleção por `OfferSnapshot`

**Objetivo:** remover labels e payload da LLM da identidade técnica.

**Entregáveis:**

- adapter Cloudbeds → `OfferSnapshot`;
- adapter Bókun → `OfferSnapshot`;
- `offer_id` opaco;
- evidence TTL/provenance;
- invalidação por mudança;
- snapshots read-only sanitizados.

**Gate:** ID/label divergente não autoriza; label tipograficamente equivalente não quebra seleção; zero/múltiplos matches falham fechados.

## Fase 4 — Resumo e confirmação únicos

**Objetivo:** provar uma versão, um resumo e uma confirmação posterior.

**Entregáveis:**

- renderer determinístico;
- persistência `SummaryPresented`;
- classificador de `ConfirmationDecision`;
- transição para comando sem provider;
- replays explícito/coloquial/contextual/negativo/ambíguo/ajuste.

**Gate:** resumo tem zero claim/provider; confirmação válida cria um comando; alteração cria nova versão; duplicata cria zero comandos adicionais.

## Fase 5 — Comando e execução duráveis

**Objetivo:** retirar writes do request/turno da LLM.

**Entregáveis:**

- store de comandos;
- worker com lease;
- ledger integrado;
- `ExecutionOutcome` tipado;
- reconciliação de crash;
- outbox desacoplada do request.

**Gate:** fault injection completo; `provider_calls <= 1`; falha de mensagem não repete reserva; `called_unknown` vai para revisão.

## Fase 6 — Handoff e pagamentos separados

**Objetivo:** separar ciclo de reserva, financeiro e atendimento humano.

**Entregáveis:**

- `HandoffWorkflow`;
- `PaymentWorkflow`;
- efeitos obrigatórios/opcionais por configuração;
- Pix/Wise/Stripe como eventos posteriores à reserva;
- outboxes e idempotências independentes.

**Gate:** e-mail desativado não bloqueia cliente; pagamento não reabre confirmação da reserva salvo alteração econômica; partial failures são recuperáveis.

## Fase 7 — Migração das fronteiras

**Objetivo:** fazer runner, plugin e executor usarem o mesmo kernel.

**Entregáveis:**

- `TurnCoordinator`;
- `ToolDispatch` único;
- plugin fino;
- dual-read/single-write;
- shadow comparison;
- remoção progressiva de budgets/guards paralelos.

**Gate:** decisão antiga/nova classificada; divergências críticas zero; fronteiras reais passam; estado legado ativo é migrável.

## Fase 8 — Shadow, canary e rollout

**Objetivo:** fechar a fronteira operacional upstream e o composition root antes de
construir, então provar o mesmo child manifest OCI em condições progressivamente
reais.

**Entregáveis:**

- plano/quarentena aprovados e contract lock;
- kernel 0.8.0 com reply/replay, relays, authorities e qualification duráveis;
- composition root canônica e runtime F/E revisados;
- wheel e source/runtime F/E autenticados;
- release contract acíclico e decisão separada GO/NO-GO de build;
- imagem OCI fixada pelo child manifest `linux/arm64`;
- manifesto comum de release e attestation por instância;
- dark canary com reads reais e writes fechados;
- ingress ManyChat real;
- uma canary E2E autorizada;
- rollout 1% → 5% → 25% → 100%.

**Gate:** design → plano/quarentena → upstream F/E → wheel → runtime F/E → release
contract → GO de build → build → dark → ingress → conversa humana → E2E → rollout.
Cada seta é uma decisão independente, definida em `05-validation-and-rollout.md`.

**Rollback:** pelo mesmo child manifest digest, sem rebuild; efeitos incertos são
reconciliados antes de qualquer nova tentativa.

## Fase 9 — Remoção do legado

**Objetivo:** realizar a redução real de complexidade.

**Entregáveis:**

- remover fases/candidatos antigos;
- remover JSONL como estado comercial;
- remover aliases de write;
- remover policies duplicadas;
- remover leitura legada após janela;
- atualizar SOT/runbooks;
- relatório de LOC/decisões antes/depois.

**Gate:** nenhum workflow ativo usa schema legado; rollback testado; suíte/replay/canary final verdes.

## Política de estimativa

Estimativas são recalculadas ao encerrar cada fase. Baseline inicial:

- Fases 0–1: 3–5 dias úteis;
- Fases 2–4: 7–12 dias úteis;
- Fases 5–7: 8–15 dias úteis;
- Fases 8–9: 4–8 dias úteis.

Faixa total inicial: 3–5 semanas de trabalho focado. Velocidade nunca reduz os gates de segurança/evidência.
