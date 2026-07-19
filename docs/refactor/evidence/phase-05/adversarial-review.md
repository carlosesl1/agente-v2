# Revisão adversarial inline — Fase 5

Escopo: store, worker, reconciler, outbox, properties, faults, mutations e evidência local da Fase 5.

Veredito material:

- Critical: nenhum.
- Important: nenhum.
- Rollout: `NO-GO`.
- Fase 6: não iniciada.

### 1. Existe caminho de command sem confirmação?

Não. `ReservationCommand` continua com owner único no reducer; properties começam em `new_workflow`, atravessam resumo e confirmação posterior e contam qualquer command não autorizado como safety failure.

### 2. Existe commit parcial state/command/ledger?

Não nos contratos executados. A UnitOfWork persiste state, evento, command, ledger e outbox na mesma transação; statement faults e reopen exigem rollback bilateral e `partial_transactions=0`.

### 3. Token antigo consegue fence/outcome/receipt?

Não. Claims comerciais e de outbox exigem owner, token e lease correntes; testes de stale token cobrem fence, outcome, release e receipt.

### 4. Crash pós-fence consegue redispatch?

Não. O slot é consumido antes do dispatch; restart pós-fence reconcilia para `called_unknown`/manual review, e um worker posterior retorna `idle` sem novo provider call.

### 5. `dispatch` pode retornar `not_called` e requeue?

Não. `not_called` retornado após fencing é violação de contrato e é promovido para `called_unknown`; retry automático só existe antes da fronteira do provider.

### 6. Unknown chega a manual review?

Sim. Outcome, state, eventos e outbox de revisão manual são persistidos atomicamente; properties exigem counters positivos de unknown e manual review.

### 7. Falha de outbox altera ledger/provider count?

Não. Claim/fencing da outbox são separados; failure libera somente a mensagem. Properties e contention exigem delta comercial de provider igual a zero.

### 8. DB/hash/event/command/outcome adulterado falha antes de uso?

Sim. Consistency gate, hashes canônicos, replay idempotente e probes digest-only falham fechados antes de dispatch/delivery subsequente.

### 9. Properties começam em `new_workflow` e atravessam ambos adapters?

Sim. Cada índice passa por Cloudbeds ou Bókun read adapter com transport sintético, FSM real, Fase 4 e SQLite temporário; provider totals e outcomes somam exatamente `cases`.

### 10. Mutantes são materiais, determinísticos e temporários?

Sim. O catálogo fechado possui 20 mutantes, baseline verde obrigatório, target count 1, loader error separado de kill e execução apenas em cópias temporárias; `PYTHONHASHSEED` é coberto.

### 11. PostgreSQL não executado está declarado sem overclaim?

Sim. O DDL PostgreSQL é contrato estático regenerável. `schema-manifest.json`, entry baseline e este closeout mantêm `postgresql_executed=false`.

### 12. Há qualquer rede/runtime/default adapter?

Não. Não existe transport default nem adapter externo no package. AST/capability scan proíbe HTTP/SDK/env/auth/subprocess em `reservation_execution`; nenhum Hermes, LLM, provider, delivery, Supabase, Docker ou banco live foi executado.

### 13. O orçamento de CI de 15 minutos é executável sem reduzir os gates?

Sim por job. Properties, faults/restart/contention, suíte, mutations e validação
estática rodam em jobs independentes, todos com `timeout-minutes: 15`; o check
terminal `phase5-gate` depende do sucesso de todos. O paralelismo não altera
20.000 casos, 2.000 schedules, 50 rounds, 17 fault points ou 20 mutantes.

### 14. O validator pode aprovar envelopes vazios ou catálogos reduzidos?

Não. `operational-gate-contract.json` fixa identidades e valores sem importar
os runners. O validator exige schemas e tipos exatos, reconstrói as 17 linhas,
a sequência determinística dos 2.000 restarts e os 50 pares command/outbox, e
compara o catálogo material de 20 mutantes com o contrato independente.

### 15. A chamada do child pós-dispatch pode ser confundida com redispatch?

Não. Cada schedule registra `provider_calls_setup_baseline`, baseline depois do
exit 91, final e `provider_calls_during_recovery`. Em `during_dispatch` e
`after_dispatch_before_outcome`, os valores exatos são setup 0, baseline 1,
final 1 e delta de recovery 0.

### 16. Métricas ou REDs podem trocar inteiro por boolean/float/string ou omitir campos?

Não. O contrato versiona schemas recursivos fechados para os dois envelopes de
métricas e os 13 arquivos RED. O validator exige identidade exata de tipos e
chaves em todos os níveis, inteiro zero para counters live/network, booleanos
exatos para flags negativas e proveniência completa para cada `exit_code`.
