# Agente v2 — Refatoração do fluxo Maya

Repositório dedicado à refatoração controlada do processo de atendimento e reservas da Maya/Chapada Leads.

## Objetivo

Construir um fluxo comercial estável, previsível e auditável, preservando as integrações e proteções que já funcionam, mas removendo as máquinas de estado duplicadas que hoje existem entre agente, runner, plugin, executor e guards.

Contrato central:

> Para cada assunto comercial imutável: **um resumo → uma confirmação natural posterior → no máximo um comando durável → no máximo uma execução no provider**.

## Estado

- Fase ativa: **Fase 8 — design/plano publicados e entrada autenticada**.
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
  `8f23a8376f1d226f2ada5d80a45cbb930a79429e`; seus sete workflows remotos,
  properties, faults/restarts/contention, mutations, manifests, validator e
  suíte terminal ficaram verdes, sem capability live.
- Fase 7: **concluída e publicada** no closeout
  `93682024b4867d3e313324339a7060d5351dcd3d`, tree
  `b779e35c671f3050d056c6ef3c8c0700f5b13f35`. O candidato funcional
  `2c99be11b1bdc1b66d14bd7a19c510ec50d502d4` foi autenticado pelo snapshot
  terminal `73904070dfcb52a3183459bc97abbc87595e1efe`, revisão 3/3 e run remoto
  `29804123764` com seis jobs verdes. A integração pós-merge passou 762/762.
- Fase 8: **ativa** desde 2026-07-21 sobre o closeout da Fase 7. A spec
  `0dbc9cb9722762dfc4f24a3ea73bfce974835a84`, o plano corrigido
  `49b4930d5c5df48eb85cb58c73d5ceded876259a` e a entrada autenticada abrem a
  escada controlada até o gate conversacional, sem antecipar provider write,
  rollout ou Fase 9.
- Runtime atual: somente fonte de evidência read-only no HEAD
  `57408d8b2040399bc25ee7957505208079458884`, tree
  `67b5fe18d4685281778e41cd61cd584dd063ea60`. A réplica limpa aprovada está em
  `183fb41d645e1bb04e237c986988309a28e42b34`, tree
  `e546e9d88093c09a245502bcca3d119e2e450672`.
- Rollout comercial: **NO-GO**. A Fase 8 está iniciada, mas build, canaries,
  provider write, promoção e Fase 9 continuam bloqueados pelos gates próprios.

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
- [Execução da Fase 8](docs/refactor/phases/phase-08-shadow-canary-rollout.md)

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
python3 scripts/generate_phase7_manifest.py --check
python3 scripts/validate_phase7.py --terminal
```

O rollout permanece `NO-GO`; `phase8_started=true` e `phase9_started=false`.
