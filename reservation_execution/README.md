# reservation_execution

Contratos operacionais imutáveis da Fase 5 para separar autorização de domínio,
coordenação durável e fronteiras de side effect. O reducer de
`reservation_domain` permanece o único owner da criação de `ReservationCommand`.
Este package, nesta task, contém apenas DTOs fechados e o `ExecutionAdapter`
fornecido pelo caller; não contém store, schema, worker, transport ou integração.

Este package não abre rede, não lê env/auth, não escolhe provider e não possui
adapter/delivery default. Instanciar worker exige ports fornecidos pelo caller.

`ExecutionAdapter.prepare` é uma operação pura. Somente `dispatch` marca a
fronteira que uma implementação futura injetada poderá tornar capaz de chamar um
provider. Nenhuma implementação com capacidade externa é oferecida aqui.

A validação falha fechado para tipos inexatos, identificadores opacos inválidos,
datetimes sem timezone, JSON não canônico, hashes divergentes e receipts
adulterados. Ledger comercial e outbox de comunicação continuam contratos
separados.

Fixtures e evidências desta fase são exclusivamente sintéticas. O rollout
permanece **NO-GO** e a Fase 6 não é iniciada.
