# Registro de riscos

Escala: probabilidade e impacto de 1 (baixo) a 5 (crítico).

| ID | Risco | P | I | Sinal | Mitigação | Owner | Estado |
|---|---|---:|---:|---|---|---|---|
| R01 | Patches continuam sendo aplicados no legado durante refatoração | 4 | 5 | novos guards/status | freeze de mudanças não críticas; RCA antes de exceção | tech lead | aberto |
| R02 | Migração big bang quebra estados ativos | 3 | 5 | conversões sem fallback | dual-read/single-write e shadow | domain | aberto |
| R03 | Kernel novo replica bugs do legado | 3 | 5 | testes portados literalmente | testes por invariantes e incidentes, não implementação | QA/domain | aberto |
| R04 | Provider chamado duas vezes após crash | 3 | 5 | attempt > 1 | comando durável + ledger + outcome incerto | execution | aberto |
| R05 | Label volta a controlar identidade | 3 | 4 | matching por nome | `offer_id` opaco e tests metamórficos | lookup | aberto |
| R06 | Canary não corresponde ao deploy | 4 | 5 | rebuild/diff de hash | promover mesmo digest e manifesto | release | aberto |
| R07 | Segredo/PII entra no novo repo | 2 | 5 | scanners/grep | `.gitignore`, validator e revisão | security | aberto |
| R08 | Outbox perde mensagem após efeito comercial | 3 | 5 | backlog/stuck lease | store durável, lease/recovery, fault injection | messaging | aberto |
| R09 | E-mail opcional bloqueia handoff | 3 | 3 | tag aplicada sem reply | matriz required/optional por configuração | handoff | aberto |
| R10 | Timeout continua distribuído | 4 | 4 | budgets diferentes | `TurnCoordinator` único; worker independente | runtime | aberto |
| R11 | Estado tipado vira outro metadata bag | 3 | 4 | `dict[str, Any]` em domínio | DTOs fechados e schema versionado | domain | aberto |
| R12 | Testes verdes por mocks permissivos | 4 | 5 | fake aceita payload impossível | provider snapshots reais sanitizados | QA | aberto |
| R13 | Rollout abre mais de um workflow de uma vez | 2 | 5 | gates múltiplos alterados | canary por workflow/provider | release | aberto |
| R14 | Pagamento acopla novamente confirmação da reserva | 3 | 4 | mudança de método reabre draft | workflow financeiro posterior e separado | payments | aberto |
| R15 | Legado nunca é removido | 4 | 4 | dual paths permanentes | Fase 9 obrigatória, métricas de remoção | tech lead | aberto |
| R16 | Disco/capacidade interrompe evidências/workers | 3 | 4 | uso >85%, backlog | quotas, alertas e backend externo | operations | aberto |
| R17 | Corpus de witness é interpretado como E2E do legado | 3 | 4 | claim “runtime reproduzido” | classificação explícita, source map e limites documentados | QA | monitorado |
| R18 | Working tree legado muda e invalida o source map | 3 | 4 | HEAD/status canônico diverge | verificação read-only de HEAD/status e símbolos no closeout | tech lead | monitorado |
| R19 | Trace sintético codifica uma premissa incorreta | 3 | 5 | cenário sem owner/fonte verificável | revisão independente, símbolo validado e futuro replay no boundary real | QA/domain | aberto |

## Processo

- Todo novo risco recebe ID, owner, sinal observável e mitigação.
- Risco não é fechado por opinião; exige evidência.
- Risco crítico aberto bloqueia GO se atingir autorização, duplicidade, evidência ou identidade do artefato.
- A revisão de riscos é obrigatória no encerramento de cada fase.
