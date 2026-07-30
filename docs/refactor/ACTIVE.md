# Controle de execução ativo — Fast-track Agente V2

## Autoridade

- Estado: `BOKUN_MULTI_PASSENGER_IMPLEMENTATION`
- Branch obrigatória: `maya-v2-operational-readiness`
- Worktree obrigatória: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`
- Especificação ativa: `docs/superpowers/specs/2026-07-30-maya-v2-bokun-multi-passenger-design.md`
- Plano ativo: `docs/superpowers/plans/2026-07-30-maya-v2-bokun-multi-passenger.md`
- Base funcional anterior ao reparo: `71ff137e9d9d35cc8f8cd1211ceca0744dd0d6dc`
- Commit da especificação: `4381c74dd35c4da5208b112c91894ffa36711fd3`
- Rollout: `DARK_CANARY_PENDING`
- Provider writes reais: `BLOQUEADOS POR GATES INDEPENDENTES`
- ManyChat público real: `BLOQUEADO ATÉ NOVA AUTORIDADE ASSINADA`

## REPAROS OPERACIONAIS AUTORIZADOS

Carlos autorizou em 2026-07-27 a correção de conversa pré-reserva, relógio de consultas, fallback de protocolo, conhecimento comercial e controle operacional, além da preparação de todas as funções sob testes limitados.

O candidato está qualificado localmente. O próximo avanço autorizado é: revisão final, commit/push do branch, CI no SHA exato, imagem imutável e dark canary com todos os efeitos externos fechados. Provider writes e entrega ManyChat só podem abrir um por vez, para o subscriber `1873018537`, com janela finita, autoridade assinada, read-back e rollback/fallback ao legado.

## REPARO BÓKUN MULTI-PASSAGEIRO ATIVO

Carlos aprovou em 2026-07-30 paridade completa com o V1 para grupos de adultos e crianças, com dados individuais por passageiro. O reparo precisa preservar manifesto privado, proposta assinada, cotação por categoria, idempotência, submit único e read-back exato. Não está autorizado nenhum write real de provider durante a implementação.

| Task do reparo | Estado | Commit |
|---|---|---|
| 1. Ativar cadeia de autoridade | `DONE` | `63dffe5cee86dd2117fcd2b6703f47ded181dca3` |
| 2. Domínio assinado de passageiros | `DONE` | `4adbdf43222ca0851dcd16c4e80585ad61bd7b61` |
| 3. Protocolo v6 e manifesto privado | `DONE` | `8dccad1b33aab7135498ba2cfcab2b5b2265e9ec` |
| 4. Reads por composição adulto/criança | `DONE` | `4883bd2f46ad5026bac6a2f710bc0e45cd054071` |
| 5. Coleta e seleção de grupos | `DONE` | `6f39fcf7bf76feff9f43a94c6f3d98b783e9b7c6` |
| 6. Dispatch Bókun v2 | `DONE` | `9f38fb7f616b368d97d40e6ef819889936d948e3` |
| 7. Transport e read-back exato | `IN_PROGRESS` | `PENDING` |
| 8. Regressões e qualificação local | `PENDING` | `PENDING` |
| 9. Revisão terminal e candidata | `PENDING` | `PENDING` |

- NEXT: `Task 7 — transport e read-back exato`

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
