# Agente v2 — Refatoração do fluxo Maya

Repositório dedicado à refatoração controlada do processo de atendimento e reservas da Maya/Chapada Leads.

## Objetivo

Construir um fluxo comercial estável, previsível e auditável, preservando as integrações e proteções que já funcionam, mas removendo as máquinas de estado duplicadas que hoje existem entre agente, runner, plugin, executor e guards.

Contrato central:

> Para cada assunto comercial imutável: **um resumo → uma confirmação natural posterior → no máximo um comando durável → no máximo uma execução no provider**.

## Estado

- Fase ativa: **Fase 5 — Task 12 aprovada localmente; publicação e CI remoto pendentes**.
- Fase 0: **concluída e publicada no GitHub**.
- Fase 1: **concluída e publicada no GitHub**.
- Fase 2: **concluída e publicada no GitHub**, sem integração com runtime ou providers.
- Fase 3: **concluída e remediada** no commit
  `b7c4cb2d6376d9ad3513477fc056a0ba978f77e7`; nenhuma rede real.
- Fase 4: **concluída e publicada** no commit
  `2c922d1b88eaf44412c1a808c4786e4729e8ba64`; cinco workflows remotos verdes,
  sem LLM, rede, entrega ou execução live.
- Fase 5: **Tasks 1–12 implementadas e aprovadas localmente**, com SQLite local,
  command, ledger, fencing, recovery, outbox, properties, faults, mutations e
  closeout fechado; publicação e CI remoto ainda pendentes, sem Docker,
  PostgreSQL, Supabase, provider ou delivery live.
- Runtime atual: apenas fonte de evidência; não é alterado por esta fase.
- Implementação funcional concluída localmente: domínio, lookups, boundary puro
  de resumo/confirmação e execução durável no novo repositório.
- Rollout comercial: **NO-GO**; migração, shadow e canary pertencem às fases
  posteriores. A Fase 6 não foi iniciada.

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
```
