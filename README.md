# Agente v2 — Refatoração do fluxo Maya

Repositório dedicado à refatoração controlada do processo de atendimento e reservas da Maya/Chapada Leads.

## Objetivo

Construir um fluxo comercial estável, previsível e auditável, preservando as integrações e proteções que já funcionam, mas removendo as máquinas de estado duplicadas que hoje existem entre agente, runner, plugin, executor e guards.

Contrato central:

> Para cada assunto comercial imutável: **um resumo → uma confirmação natural posterior → no máximo um comando durável → no máximo uma execução no provider**.

## Estado

- Fase ativa: **Fase 7 — migração das fronteiras, com spec/plano aprovados e
  execução TDD econômica iniciada**.
- Fase 0: **concluída e publicada no GitHub**.
- Fase 1: **concluída e publicada no GitHub**.
- Fase 2: **concluída e publicada no GitHub**, sem integração com runtime ou providers.
- Fase 3: **concluída e remediada** no commit
  `b7c4cb2d6376d9ad3513477fc056a0ba978f77e7`; nenhuma rede real.
- Fase 4: **concluída e publicada** no commit
  `2c922d1b88eaf44412c1a808c4786e4729e8ba64`; cinco workflows remotos verdes,
  sem LLM, rede, entrega ou execução live.
- Fase 5: **concluída e publicada** no commit
  `9199b2c70fb3a26d9f12949b25d135f625b2317d`; seis workflows remotos verdes,
  sem Docker, PostgreSQL, Supabase, provider ou delivery live.
- Fase 6: **concluída e publicada** no commit
  `8f23a8376f1d226f2ada5d80a45cbb930a79429e`; aberta no commit-base terminal da Fase 5
  `6c65c2612aefce4b217dcd0308e33dd68e1dc7db`; design separa
  `HandoffWorkflow` e `PaymentWorkflow`, sem integração live. Properties,
  faults/restarts/contention, mutations, manifests, validator e suíte terminal
  passaram; o gate corrigido preservou 20.000 properties em 857,092 s, abaixo
  do budget de 900 s. O validator de pureza foi fechado contra execução de
  processos; os sete workflows remotos e os seis jobs da Fase 6 ficaram verdes.
- Fase 7: **em execução** na branch `phase7-boundary-migration`, com
  `LegacyStateImporter`, `TurnCoordinator`, `ToolDispatch` e
  `DecisionComparator` como fronteiras únicas. Quatro writes têm command owner
  nas Fases 5/6; três permanecem `BLOCKED_UNMIGRATED` e bloqueiam rollout.
- Runtime atual: apenas fonte de evidência; a árvore operacional com 80 entradas
  locais permanece somente leitura e será reproduzida em clone isolado.
- Implementação funcional concluída localmente: domínio, lookups, boundary puro
  de resumo/confirmação e execução durável no novo repositório.
- Rollout comercial: **NO-GO**; Fase 7 foi explicitamente iniciada sem
  capability live. Shadow live/canary pertencem à Fase 8, que permanece
  bloqueada.

## Navegação

- [Plano e índice da refatoração](docs/refactor/README.md)
- [Charter e invariantes](docs/refactor/00-charter.md)
- [Baseline técnico](docs/refactor/01-baseline.md)
- [Taxonomia de falhas](docs/refactor/02-failure-taxonomy.md)
- [Arquitetura-alvo](docs/refactor/03-target-architecture.md)
- [Plano faseado](docs/refactor/04-phased-delivery-plan.md)
- [Validação, evidências e rollout](docs/refactor/05-validation-and-rollout.md)
- [Registro de riscos](docs/refactor/06-risk-register.md)
- [Execução da Fase 0](docs/refactor/phases/phase-00-baseline-and-governance.md)
- [Execução da Fase 1](docs/refactor/phases/phase-01-incident-characterization.md)
- [Execução da Fase 2](docs/refactor/phases/phase-02-typed-domain-and-reducer.md)
- [Execução da Fase 3](docs/refactor/phases/phase-03-lookups-and-offer-snapshots.md)
- [Execução da Fase 4](docs/refactor/phases/phase-04-single-summary-and-confirmation.md)
- [Execução da Fase 5](docs/refactor/phases/phase-05-durable-command-execution.md)
- [Execução da Fase 6](docs/refactor/phases/phase-06-handoff-and-payments.md)
- [Execução da Fase 7](docs/refactor/phases/phase-07-boundary-migration.md)

## Regras de execução

1. Trabalhar em **uma fase por vez**.
2. Não iniciar a fase seguinte sem fechar critérios de aceite e evidências da fase atual.
3. Toda mudança funcional começa com teste de caracterização ou teste RED.
4. Nenhuma canary pode usar código/configuração diferentes do artefato que será promovido.
5. Nenhum teste pode enviar WhatsApp ou executar write comercial sem autorização explícita e escopo único.
6. Nenhum segredo, PII, mensagem bruta ou payload de provider entra no Git.
7. Nenhum deploy é feito a partir de working tree sujo.

## Validação local

```bash
python3 scripts/validate_phase0.py
python3 scripts/validate_phase1.py
python3 scripts/validate_phase2.py
python3 scripts/validate_phase3.py
python3 scripts/validate_phase4.py
python3 scripts/validate_phase5.py
python3 scripts/validate_phase6.py
```

O rollout permanece `NO-GO`; `phase7_started=true` e `phase8_started=false`.
