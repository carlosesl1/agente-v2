# Controle de execução ativo — Fast-track Agente V2

## Autoridade

- Estado: `BOKUN_BOOKING_ID_CONFIRMATION_CANDIDATE`
- Branch obrigatória: `maya-v2-operational-readiness`
- Worktree obrigatória: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`
- Especificação ativa: `docs/superpowers/specs/2026-07-31-bokun-submit-id-confirmation-design.md`
- Plano ativo: `docs/superpowers/plans/2026-07-31-bokun-booking-id-confirmation.md`
- Base funcional anterior ao reparo: `16d4d90e45c2ceb6bba12565f1862e092bcb9c56`
- Commit da especificação: `01336c0c4ad420404c465382c78f4959eedfdba9`
- Rollout: `LOCAL_QUALIFIED_REVIEW_PENDING`
- Provider writes reais: `BLOQUEADOS POR GATES INDEPENDENTES`
- ManyChat público real: `BLOQUEADO ATÉ NOVA AUTORIDADE ASSINADA`

## REPAROS OPERACIONAIS AUTORIZADOS

Carlos autorizou em 2026-07-27 a correção de conversa pré-reserva, relógio de consultas, fallback de protocolo, conhecimento comercial e controle operacional, além da preparação de todas as funções sob testes limitados.

O candidato está qualificado localmente. O próximo avanço autorizado é: revisão final, push do branch, CI no SHA exato, imagem imutável e dark canary com todos os efeitos externos fechados. Provider writes e entrega ManyChat continuam bloqueados até qualificação e autoridade separadas.

## CONFIRMAÇÃO BÓKUN POR BOOKING ID ATIVA

Carlos autorizou em 2026-07-31 que um submit Bókun V2 aceito por HTTP 2xx sem marcador explícito de falha, com um único booking ID principal e sem aliases conflitantes, seja evidência monotônica de criação. O caminho síncrono V2 não executa mais GET depois de obter esse ID: retorna `confirmed`, o adapter grava somente o fingerprint opaco como `EFFECT_CONFIRMED`, o ledger consome um único slot e o completion projector materializa `Seu passeio foi confirmado.` uma única vez.

Permanece fail-closed:

- resposta sem booking ID, IDs principais conflitantes, status não-2xx mesmo com ID, `success:false` mesmo com HTTP 2xx + ID, timeout ou erro ambíguo após submit continuam `CALLED_UNKNOWN`, sem retry;
- activity/passenger booking IDs não contam como booking ID principal;
- produto, data, horário, rate, composição adulto/criança, manifesto e economia BRL continuam vinculados antes do submit;
- o caminho Bókun legado e Cloudbeds não mudaram;
- ManyChat, pagamento, cancelamento, e-mail e handoff continuam fechados;
- auditoria GET posterior, se adicionada, será observador read-only separado e nunca poderá rebaixar uma criação já confirmada nem repetir submit.

Evidência local sobre o código funcional `b23998f798b81acb2c1898ff143801b22cadadff` e as provas duráveis no HEAD sucessor:

- RED reproduziu o quarto GET indevido depois do submit com `booking-123`;
- revisão do primeiro candidato reproduziu `409/422 + booking ID` sendo aceito indevidamente; o SHA foi invalidado e o novo RED exige status HTTP 2xx;
- escrutínio subsequente reproduziu `HTTP 200 + booking ID + success:false`; o SHA sucessor também foi invalidado e o RED exige ausência de marcador explícito de falha;
- transporte Bókun completo: `50 passed`;
- jornada transporte/reservas/outcome/completion: `81 passed`;
- regressão causal de Bókun, conversa, horário, 2+1 e produção: `215 passed`;
- catálogo comercial fechado: `1 passed`;
- comando oficial de CI local: `1326 passed, 7 deselected, 2940 subtests passed`;
- Ruff, `fasttrack-boundaries` e `git diff --check`: verdes;
- nenhum provider, ManyChat, pagamento, cancelamento, e-mail ou handoff foi chamado para qualificar esta correção.

- NEXT: obter `APPROVE` independente sobre o SHA exato do HEAD; depois publicar o mesmo SHA, exigir CI remoto verde, produzir imagem OCI imutável e atestar dark canary com efeitos fechados antes de qualquer tráfego de clientes.

## REPARO BÓKUN MULTI-PASSAGEIRO ATIVO

Carlos aprovou em 2026-07-30 paridade completa com o V1 para grupos de adultos e crianças, com dados individuais por passageiro. O reparo preserva manifesto privado, proposta assinada, cotação por categoria, idempotência, submit único e booking ID principal inequívoco. Não está autorizado nenhum write real de provider durante a implementação.

| Task do reparo | Estado | Commit |
|---|---|---|
| 1. Ativar cadeia de autoridade | `DONE` | `63dffe5cee86dd2117fcd2b6703f47ded181dca3` |
| 2. Domínio assinado de passageiros | `DONE` | `4adbdf43222ca0851dcd16c4e80585ad61bd7b61` |
| 3. Protocolo v6 e manifesto privado | `DONE` | `8dccad1b33aab7135498ba2cfcab2b5b2265e9ec` |
| 4. Reads por composição adulto/criança | `DONE` | `4883bd2f46ad5026bac6a2f710bc0e45cd054071` |
| 5. Coleta e seleção de grupos | `DONE` | `6f39fcf7bf76feff9f43a94c6f3d98b783e9b7c6` |
| 6. Dispatch Bókun v2 | `DONE` | `9f38fb7f616b368d97d40e6ef819889936d948e3` |
| 7. Transport e read-back exato | `DONE` | `d9935f28049b19316de38dbf0973cea91e236c7a` |
| 8. Regressões e qualificação local | `DONE` | `3cb84200aaab3d76344cba06623a7eaa70790865`, `c5ee69d83345901b9ab16a36b5d18d0904f1ba50` |
| 9. Revisão terminal e candidata | `CANDIDATE_FROZEN` | código `5b5dc5c88783e1c286d64b3fc915b600ea74442a`; evidência e controle no HEAD |
| 10. Prova conversacional de grupo e boundary 2+1 | `DONE` | `373221f1326701bea9e1581720ffe95ab6f886be` |

- NEXT: obter `APPROVE` independente sobre o SHA exato do HEAD, incluindo o reparo conversacional `373221f1326701bea9e1581720ffe95ab6f886be`; somente então publicar o sucessor, executar CI, produzir nova imagem OCI imutável e repetir o dark canary sem relay.

### Candidata multi-passageiro congelada

A candidata incorpora os findings reproduzíveis das revisões independentes e do primeiro dark canary, sem executar writes reais:

- correção explícita de passageiro revoga resumo e autoridade assinada anteriores;
- intents não corretivos não podem persistir mutações do manifesto;
- valores privados persistidos não retornam ao modelo; somente presença allowlisted é exposta;
- `DispatchRequest`, argumento externo e comando assinado exigem a mesma idempotency key antes do provider;
- categorias adulta/infantil exigem moeda BRL completa e a moeda, o valor e o `rate_id` publicados vêm da mesma rate selecionada;
- categorias públicas do Bókun são selecionadas somente quando há um único ID elegível: aliases internos com `ageQualified=false` são ignorados, valores malformados ou múltiplas categorias públicas falham fechados;
- quote/cart/read-back rejeitam aliases e bindings conflitantes de oferta, status, valor, moeda, composição e booking ID;
- IDs internos de activity/passenger booking não são confundidos com o ID principal da reserva;
- `409` e qualquer ambiguidade após submit sem booking ID inequívoco permanecem `CALLED_UNKNOWN`; não há retry otimista;
- no caminho V2 atual, um submit aceito com booking ID principal inequívoco confirma a criação imediatamente; read-back não bloqueia a confirmação.

Evidência local no código `5b5dc5c88783e1c286d64b3fc915b600ea74442a`:

- transporte/reservas focados: `81 passed`;
- suíte V2 e contrato do prompt v6: `368 passed`;
- comando oficial de CI: `1314 passed, 7 deselected, 2940 subtests passed`;
- Ruff, compileall, `fasttrack-boundaries`, `git diff --check`, ausência de `uv.lock` e árvore limpa: verdes;
- witness real somente leitura: metadata com um `ADULT ageQualified=true` e três aliases internos `ageQualified=false`; o código corrigido selecionou a categoria pública com `3 GET`, sem POST ou write;
- a imagem do SHA `400e0f4ae2a5c76a1899b7f95490f170fd1690bf` passou CI, mas seu dark canary falhou fechado nesse witness; os containers foram removidos e o audit registrou zero eventos, comandos, outbox ou efeitos;
- branch remoto permanece em `400e0f4ae2a5c76a1899b7f95490f170fd1690bf` e o runtime dark anterior permanece em `71ff137e9d9d35cc8f8cd1211ceca0744dd0d6dc` até nova aprovação e qualificação operacional.

### Evidência conversacional de grupo 2+1

O teste com modelo real encontrou e fechou duas lacunas antes de qualquer efeito externo: o extrator explícito zerava crianças ao reconhecer adultos, e o boundary de leitura legado colapsava a composição para participantes adultos. O reparo `373221f1326701bea9e1581720ffe95ab6f886be` preserva `adults=2`, `children=1` num wire interno aditivo, sem alterar o catálogo público fechado da Fase 7, e expõe essa composição no resumo confirmado pelo lead.

Evidência local sanitizada sobre os mesmos bytes do commit funcional:

- conversa natural de dois turnos com o `HermesModelAdapter` real: resumo e confirmação;
- resumo público explícito: `2 adultos e 1 criança`;
- comando durável: um `book_activity`, três passageiros tipados, uma relay row e idempotency key estável;
- replay da confirmação: mesmo receipt, sem nova chamada ao modelo e sem nova leitura;
- provider write adapter ausente, `0` submit Bókun, `0` ManyChat e `0` pagamento; todos os gates reais continuaram fechados;
- comando oficial de CI local: `1319 passed, 7 deselected, 2940 subtests passed`;
- Ruff, compileall, `fasttrack-boundaries`, `git diff --check` e ausência de `uv.lock`: verdes;
- as sete deselections históricas foram reproduzidas sem o patch no SHA base `08f00b3a601f86d7e4fe5171f28b82ad61ef0cb4`.

### Decisão de topologia da Task 7

Task 7 implementa somente `CompletionPolicy` pura e outbox público local/durável. Task 9 correlaciona os receipts dos stores existentes no composition root. Não será criado store global paralelo de completion.

## Regra para não confundir novo e antigo

### Único produto novo

O produto é o host próprio do V2 nesta worktree. O caminho produtivo começa em `v2_host` e usa `v2_application`, `v2_contracts`, `v2_adapters` e a cápsula existente `reservation_*`.

### Fonte antiga somente leitura

`/home/ubuntu/chapada-leads-hermes` pode ser lido apenas para extrair comportamento técnico e testes sanitizados dos providers. É proibido:

- editar ou commitar nesse repositório durante o fast-track;
- importá-lo via pacote, caminho, `PYTHONPATH`, subprocesso ou container;
- chamar seu `app`, planner, agente, `LeadState`, orchestrator ou executor genérico;
- usar seu banco como estado do V2;
- delegar a ele decisão comercial, reserva, pagamento ou mensagem.

### Documentos históricos

`docs/refactor/04-phased-delivery-plan.md`, documentos antigos da Fase 8, manifests e evidências continuam preservados, mas não são planos executáveis. Só esta cadeia autoriza trabalho:

```text
AGENTS.md
→ docs/refactor/ACTIVE.md
→ especificação ativa
→ plano ativo
```

Se houver conflito, o documento anterior nessa cadeia vence. O agente não tenta conciliar por conta própria.

## Loop obrigatório de execução

Para cada task do plano:

1. confirmar branch, worktree e `git status`;
2. ler apenas a task `NEXT` e suas interfaces de entrada;
3. escrever o teste RED indicado;
4. executar somente o selector focado e confirmar a falha causal;
5. implementar o mínimo para GREEN;
6. executar selector focado, regressões diretamente afetadas e guard de fronteiras;
7. revisar o diff da task e executar `git diff --check`;
8. commitar a implementação da task isoladamente;
9. atualizar neste arquivo: task concluída, SHA do commit funcional e nova `NEXT`;
10. commitar essa atualização de controle separadamente, sem código funcional;
11. continuar automaticamente para a próxima task.

A execução só pode parar quando:

- um teste/gate material permanece vermelho;
- o contrato aceito é insuficiente ou contraditório;
- falta credencial/configuração indispensável que não pode ser descoberta;
- o próximo passo realizaria provider write, mensagem pública, deploy ou rollout sem autorização específica;
- existe outcome incerto que exige reconciliação/handoff.

Não parar apenas para narrar progresso entre tasks verdes.

## Perfil de velocidade

- execução inline pelo controller;
- sem subagentes de mapeamento;
- RED/GREEN focado por task;
- uma regressão proporcional por task;
- uma suíte integral e uma revisão final no candidato congelado;
- sem repetir gates pesados quando os bytes relevantes não mudaram;
- nenhum atalho em idempotência, receipts, claims, fencing ou separação LLM/kernel.

## Baselines históricas excluídas dos gates ativos

- `tests/test_phase7_package.py` já falha no commit-base `8f73ee8b4bf40d6ea458a7fac3394aab756c1d88`: o artefato histórico exige metadata `0.7.0`, enquanto a branch já declarava `0.8.0`. O fast-track preserva os seis pacotes de `[tool.phase7-wheel]` e usa `[tool.v2-fasttrack]`; não altera esse teste histórico.
- `tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only` mantém o mesmo pin histórico `0.7.0`.
- `tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts` exige `closed_imports` e `package_version` da Phase 7 anterior à composição V2.
- `tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed` exige a frase de índice pré-fast-track `design aprovado`, substituída pelo estado canônico de fast-track ativo.

## Estados de task

| Task | Estado | Commit |
|---|---|---|
| 1. Control plane e pacotes | `DONE` | `c9f19c131ce9ff80020e1c0c0a8d8262a821cbfb` |
| 2. ManyChat ingress e inbox | `DONE` | `a2d57c4fa2938345c5ba745cdbb79c26bc292eec` |
| 3. Turno canônico e consultas | `DONE` | `b495b7c919046192210988ff5e5749cfa063c80b` |
| 4. Reservas Cloudbeds/Bókun | `DONE` | `f2e6d3bd309381319dd6f2a2dd78b6aa14c14014` |
| 5. Iniciação Stripe/Wise/Pix | `DONE` | `728403a64a3e590c24a5c4840a7009959101c359` |
| 6. Evidência e settlement | `DONE` | `73a40bb0f3717d30a51bc2dced7c4c870b9e0ea6` |
| 7. Pós-pagamento e conclusão | `DONE` | `85a2eab93b9b6d812226f3ec1c9e526563c3ef4d` |
| 8. Pacote, recuperação e handoff | `DONE` | `d948717533f4081239ebf91c948db1ebbb9f0f83` |
| 9. Composição, E2E e qualificação | `DONE` | `45b2fe4c488653c9ea0d3dd7432bdd3c3bca39cd`; imagem `sha256:f3247f69bd16bccc623fbc0508db2acb1ab44e23631a11dc07737282ba742ece` |

## Gate de publicação

Nenhuma task individual autoriza uso público. A primeira liberação externa exige cumulativamente:

- Tasks 1–9 concluídas;
- suíte ativa integral verde no mesmo commit, com exclusões históricas exatas registradas acima;
- imagem única construída do commit aprovado;
- writes e delivery fechados no dark canary;
- conversas completas de hospedagem, passeio e pacote com providers fake;
- reads reais verdes;
- autorização separada para provider write controlado;
- autorização separada para ManyChat allowlisted;
- teste humano de Carlos;
- decisão explícita de rollout.
