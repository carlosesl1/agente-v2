# Controle de execução ativo — Fast-track Agente V2

## Autoridade

- Estado: `IMPLEMENTING`
- Branch obrigatória: `phase8-shadow-canary-rollout`
- Worktree obrigatória: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`
- Especificação ativa: `docs/superpowers/specs/2026-07-23-fasttrack-complete-agent-design.md`
- Plano ativo: `docs/superpowers/plans/2026-07-23-v2-fasttrack-runtime.md`
- Base funcional anterior ao plano: `9fded4a7949cfade8b1d3dfc0e2e3dd023ca6543`
- Rollout: `NO-GO`
- Provider writes reais: `BLOQUEADOS`
- ManyChat público real: `BLOQUEADO`

## NEXT

`Task 6 — Evidência financeira, claims e settlement`

Nenhuma tarefa posterior está autorizada antes de Task 6 ficar verde e ser commitada.

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

## Estados de task

| Task | Estado | Commit |
|---|---|---|
| 1. Control plane e pacotes | `DONE` | `c9f19c131ce9ff80020e1c0c0a8d8262a821cbfb` |
| 2. ManyChat ingress e inbox | `DONE` | `a2d57c4fa2938345c5ba745cdbb79c26bc292eec` |
| 3. Turno canônico e consultas | `DONE` | `b495b7c919046192210988ff5e5749cfa063c80b` |
| 4. Reservas Cloudbeds/Bókun | `DONE` | `f2e6d3bd309381319dd6f2a2dd78b6aa14c14014` |
| 5. Iniciação Stripe/Wise/Pix | `DONE` | `728403a64a3e590c24a5c4840a7009959101c359` |
| 6. Evidência e settlement | `NEXT` | — |
| 7. Pós-pagamento e conclusão | `BLOCKED_BY_ORDER` | — |
| 8. Pacote, recuperação e handoff | `BLOCKED_BY_ORDER` | — |
| 9. Composição, E2E e qualificação | `BLOCKED_BY_ORDER` | — |

## Gate de publicação

Nenhuma task individual autoriza uso público. A primeira liberação externa exige cumulativamente:

- Tasks 1–9 concluídas;
- suíte integral verde no mesmo commit;
- imagem única construída do commit aprovado;
- writes e delivery fechados no dark canary;
- conversas completas de hospedagem, passeio e pacote com providers fake;
- reads reais verdes;
- autorização separada para provider write controlado;
- autorização separada para ManyChat allowlisted;
- teste humano de Carlos;
- decisão explícita de rollout.
