# Fase 5 — Design de comando e execução duráveis

**Data:** 2026-07-19
**Status:** aprovado conceitualmente; especificação aguardando revisão final do usuário
**Fase anterior:** Fase 4 concluída no SHA `e51259ea0d19a2d07d3d14ee086b0766776cbeab`

## 1. Objetivo

Retirar qualquer write comercial do request/turno da LLM e provar localmente,
de forma durável e reiniciável, o contrato:

```text
ReservationCommand autorizado e imutável
→ persistência atômica
→ claim com lease e fencing token
→ no máximo um dispatch possível
→ ExecutionOutcome tipado
→ reducer
→ outbox independente
```

A fase termina com um executor local demonstrável, sem conexão com Hermes,
ManyChat, Cloudbeds, Bókun, Supabase ou qualquer runtime live.

## 2. Decisões aprovadas

1. A prova executável usa SQLite por arquivo, via `sqlite3` da biblioteca padrão.
2. Não haverá Docker, PostgreSQL local nem Supabase live.
3. SQLite e PostgreSQL terão DDL gerados de um contrato comum versionado.
4. O PostgreSQL será apenas um contrato de implantação nesta fase; não haverá
   adapter PostgreSQL.
5. Uma unidade transacional persiste state, evento, comando e outbox.
6. Ledger comercial e outbox de comunicação permanecem tabelas e owners
   separados.
7. O reducer continua sendo o único owner de autorização e criação do
   `ReservationCommand`.
8. O worker nunca interpreta texto, escolhe oferta, muda payload ou cria comando.
9. Não existe adapter ou transporte default; toda capacidade externa é injetada.
10. Depois de consumido o dispatch slot, nenhuma recuperação automática pode
    invocar o adapter novamente.
11. Ambiguidade após a fronteira de dispatch é classificada como
    `called_unknown` e segue para revisão manual.
12. A Fase 6 não começa automaticamente e o rollout permanece `NO-GO`.

## 3. Abordagens rejeitadas

### 3.1 Event sourcing completo

Foi rejeitado por ampliar o escopo para projeções, snapshots e reconstrução de
agregados sem necessidade para provar o gate da Fase 5. O log de eventos será
append-only, mas o estado canônico continuará materializado com optimistic
revision.

### 3.2 Stores independentes com compensação

Foi rejeitado porque abre as janelas proibidas:

- estado `execution_queued` sem comando;
- comando sem ledger;
- outcome sem estado correspondente;
- outbox final sem outcome persistido.

### 3.3 Store somente em memória

Foi rejeitado porque não prova restart, lease abandonado, corrida multiprocesso,
constraints reais nem reconciliação depois de crash.

### 3.4 PostgreSQL descartável em Docker

Foi rejeitado para preservar o escopo sem Docker e sem infraestrutura externa.
A paridade será reduzida por contrato de schema gerado, constraints equivalentes
e uma decisão explícita de que execução PostgreSQL permanece pendente.

## 4. Arquitetura

Novo package:

```text
reservation_execution/
├── __init__.py
├── README.md
├── types.py              # DTOs operacionais fechados
├── schema.py             # contrato comum e renderizadores SQLite/PostgreSQL
├── sqlite_store.py       # UnitOfWork durável
├── adapter.py            # ExecutionAdapter e PreparationFailure
├── worker.py             # CommandWorker
├── reconciliation.py     # lease/dispatch/outcome recovery
├── outbox.py             # OutboxWorker e DeliveryPort
└── projection.py         # outbox final determinística por outcome
```

Artefatos SQL:

```text
schemas/phase5/sqlite.sql
schemas/phase5/postgresql.sql
```

Fluxo:

```text
ConfirmationReceived
→ reservation_domain.reduce
→ ReservationCommand
→ SQLiteUnitOfWork aplica evento e grava:
     workflow + event + command + ledger (+ outbox apropriada)
→ CommandWorker.claim
→ ExecutionStarted persistido com lease
→ ExecutionAdapter.prepare (puro)
→ ledger consome dispatch slot
→ ExecutionAdapter.dispatch (fake nesta fase)
→ ExecutionOutcome
→ ExecutionFinished persistido
→ outbox final
→ OutboxWorker + DeliveryPort fake
```

## 5. Ownership

| Componente | Decide | Não decide |
|---|---|---|
| `reservation_domain` | autorização, identidade e payload do comando, estado comercial | persistência, lease, retry, transporte |
| `SQLiteUnitOfWork` | transação, revision, deduplicação e constraints | autorização ou conteúdo comercial |
| `execution_ledger` | claim, lease, fencing, dispatch slot e outcome | mensagem pública ou interpretação |
| `CommandWorker` | ordem operacional do protocolo | alterar comando, escolher provider ou retry pós-dispatch |
| `ExecutionAdapter` | preparar request e normalizar resposta | confirmação, idempotência global ou outbox |
| `OutcomeProjection` | template/outbox determinística por outcome | executar provider ou interpretar texto |
| `OutboxWorker` | claim e entrega eventual | reservar, reabrir ledger ou mudar outcome |
| `DeliveryPort` | transportar envelope pronto | construir mensagem comercial |

## 6. Tipos operacionais fechados

### 6.1 Enums

```python
class LedgerStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DISPATCH_FENCED = "dispatch_fenced"
    OUTCOME_RECORDED = "outcome_recorded"
    MANUAL_REVIEW = "manual_review"

class OutboxStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    DELIVERED = "delivered"

class OutboxKind(str, Enum):
    SUMMARY_PRESENTED = "summary_presented"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED_NO_EFFECT = "execution_failed_no_effect"
    EXECUTION_NOT_CALLED = "execution_not_called"
    EXECUTION_MANUAL_REVIEW = "execution_manual_review"
```

Nenhum enum aceita aliases.

### 6.2 Lease

```python
@dataclass(frozen=True, slots=True)
class Lease:
    owner: str
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime
```

Regras:

- UTC canônico;
- `expires_at > acquired_at`;
- token inteiro positivo;
- owner é ID opaco;
- somente o token vigente pode concluir ou renovar o claim.

### 6.3 CommandClaim

```python
@dataclass(frozen=True, slots=True)
class CommandClaim:
    command: ReservationCommand
    workflow_revision: int
    lease: Lease
    claim_count: int
    preparation_failures: int
```

O claim transporta uma cópia imutável do comando persistido. O worker não recebe
um builder mutável nem state parcial.

### 6.4 DispatchRequest e DispatchPermit

```python
@dataclass(frozen=True, slots=True)
class DispatchRequest:
    command_id: str
    idempotency_key: str
    operation: ReservationOperation
    canonical_payload: str
    payload_hash: str

@dataclass(frozen=True, slots=True)
class DispatchPermit:
    command_id: str
    lease: Lease
    dispatch_slot: int
    request_hash: str
    fenced_at: datetime
```

Regras:

- `dispatch_slot` é exatamente `1`;
- request hash é recomputado antes do fence;
- permit é persistido antes de chamar `dispatch`;
- token ou request divergente falha fechado.

### 6.5 OutboxMessage

```python
@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_id: str
    idempotency_key: str
    workflow_id: str
    command_id: str | None
    kind: OutboxKind
    template_id: str
    canonical_payload: str
    payload_hash: str
    created_at: datetime
```

A mensagem não contém segredo, token, auth, payload bruto de provider nem
referência técnica privada destinada ao cliente. O payload persistido pode conter
fatos pessoais necessários ao uso futuro, mas fixtures/evidências desta fase são
exclusivamente sintéticas e nunca são copiadas para relatórios.

## 7. Ports sem implementação default

### 7.1 ExecutionAdapter

```python
class ExecutionAdapter(Protocol):
    adapter_id: str
    adapter_version: int

    def prepare(self, command: ReservationCommand) -> DispatchRequest: ...

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome: ...
```

`prepare` deve ser pura e não pode abrir socket. Um adapter que precise de I/O
para preparação não satisfaz o contrato.

`dispatch` é a única fronteira considerada provider-call-capable. Nesta fase,
somente adapters fakes/scripted nos testes a implementam.

### 7.2 PreparationFailure

```python
@dataclass(frozen=True, slots=True)
class PreparationFailure(Exception):
    reason: str
    retryable: bool
    evidence: tuple[str, ...]
```

A falha ocorre antes do dispatch fence. Falha retryable libera o claim mantendo o
mesmo comando; falha definitiva ou orçamento esgotado produz outcome
`not_called`.

`release_preparation_failure` retorna `REQUEUED` somente quando `retryable` e o
orçamento ainda não foi consumido. Nos demais casos, o próprio método cria
`ExecutionFinished(not_called)`, aplica o reducer, persiste state/outcome/outbox
na mesma transação e retorna `TERMINAL_NOT_CALLED`. Não é necessário
`DispatchPermit` para um outcome comprovadamente anterior ao dispatch.

O orçamento é fixo:

```text
MAX_PREPARATION_FAILURES = 3
```

Ele conta falhas pré-dispatch, não tentativas de provider.

### 7.3 DeliveryPort

```python
class DeliveryPort(Protocol):
    delivery_id: str
    delivery_version: int

    def deliver(self, message: OutboxMessage) -> DeliveryReceipt: ...
```

Não há implementação ManyChat, WhatsApp, e-mail ou rede nesta fase.

## 8. Schema comum

`schema.py` define um contrato declarativo fechado e renderiza DDL determinístico
para SQLite e PostgreSQL. Os artefatos SQL versionados precisam ser byte a byte
iguais ao resultado do gerador.

### 8.1 `schema_migrations`

- `version` PK;
- `schema_hash` SHA-256;
- `applied_at` UTC;
- versão inicial da fase: `5`.

Abrir banco com versão desconhecida, hash divergente ou migração parcial falha
fechado.

### 8.2 `workflows`

Campos mínimos:

```text
workflow_id PK
revision >= 0
state_type
state_json
state_hash SHA-256
created_at UTC
updated_at UTC
```

Constraints:

- state JSON é serialização canônica do domínio;
- hash é recomputado na leitura;
- revision deve coincidir com `state.meta.revision`;
- update usa `WHERE workflow_id=? AND revision=?` e exige uma linha alterada.

### 8.3 `domain_events`

```text
event_id PK
workflow_id FK
revision
occurred_at UTC
event_type
event_json
event_hash SHA-256
UNIQUE(workflow_id, revision)
```

Mesmo `event_id` com mesmo hash é duplicata idempotente. Mesmo ID com hash
diferente é conflito permanente.

### 8.4 `reservation_commands`

```text
command_id PK
idempotency_key UNIQUE
workflow_id FK UNIQUE
draft_id
draft_version >= 1
subject_signature
operation
command_json
command_hash SHA-256
created_at UTC
UNIQUE(workflow_id, draft_id, draft_version, operation)
```

Não existe `UPDATE` de payload, identity ou operation. Qualquer divergência para
a mesma identidade falha fechado.

### 8.5 `execution_ledger`

```text
command_id PK/FK
status
claim_owner nullable
fencing_token >= 0
lease_acquired_at nullable
lease_expires_at nullable
claim_count >= 0
preparation_failures between 0 and 3
dispatch_slots_consumed between 0 and 1
dispatch_request_hash nullable
dispatch_fenced_at nullable
outcome_json nullable
outcome_hash nullable
updated_at UTC
```

Constraints cruzadas do store:

- `QUEUED`: sem lease, sem dispatch, sem outcome;
- `PREPARING`: lease vigente, zero dispatch, sem outcome;
- `DISPATCH_FENCED`: exatamente um dispatch slot e request hash;
- `OUTCOME_RECORDED`: exatamente um outcome válido;
- `MANUAL_REVIEW`: outcome `called_unknown`;
- outcome é imutável após persistido.

### 8.6 `outbox_messages`

```text
message_id PK
idempotency_key UNIQUE
workflow_id FK
command_id nullable FK
kind
template_id
payload_json
payload_hash
status
claim_owner nullable
fencing_token >= 0
lease_acquired_at nullable
lease_expires_at nullable
delivery_attempts >= 0
delivered_at nullable
receipt_hash nullable
created_at UTC
updated_at UTC
```

A outbox não possui FK ou trigger capaz de alterar `execution_ledger`.

## 9. Unidade transacional

API pública pretendida:

```python
class SQLiteUnitOfWork:
    @classmethod
    def open(cls, path: Path) -> SQLiteUnitOfWork: ...

    def create_workflow(self, state: State) -> None: ...

    def load_workflow(self, workflow_id: str) -> State: ...

    def apply_event(
        self,
        workflow_id: str,
        expected_revision: int,
        event: Event,
        *,
        outbox: tuple[OutboxMessage, ...] = (),
    ) -> PersistedTransition: ...

    def claim_command(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> CommandClaim | None: ...

    def release_preparation_failure(
        self,
        claim: CommandClaim,
        failure: PreparationFailure,
        *,
        now: datetime,
    ) -> PreparationDisposition: ...

    def fence_dispatch(
        self,
        claim: CommandClaim,
        request: DispatchRequest,
        *,
        now: datetime,
    ) -> DispatchPermit: ...

    def record_outcome(
        self,
        permit: DispatchPermit,
        outcome: ExecutionOutcome,
        *,
        now: datetime,
    ) -> PersistedTransition: ...
```

Todas as mudanças usam `BEGIN IMMEDIATE`, rollback integral e foreign keys
habilitadas por conexão.

### 9.1 `create_workflow`

Aceita somente state exato, revision 0 e zero command IDs. Duplicata idêntica é
idempotente; workflow ID com state/hash diferente falha.

### 9.2 `apply_event`

Dentro da mesma transação:

1. lê state e revision;
2. valida hash e serialização;
3. compara `expected_revision`;
4. chama o reducer puro;
5. valida o próximo state;
6. insere evento;
7. atualiza state por optimistic revision;
8. se houver comando, insere comando imutável e ledger `QUEUED`;
9. valida e insere a outbox associada;
10. commit.

Nenhum provider é chamado.

Para `SummaryRecorded`, `apply_event` exige exatamente uma mensagem
`SUMMARY_PRESENTED`; o `message_id` deve ser igual ao
`event.outbox_message_id`, e payload/hash precisam corresponder ao artefato
preparado pela Fase 4. Evento sem mensagem ou mensagem divergente causa rollback.

Quando o reducer produz `ReservationCommand`, a store insere command e ledger na
mesma transação. O chamador não pode fornecer um comando alternativo.

### 9.3 Idempotência

A store persiste a resposta canônica da transição por evento. Replay idêntico
retorna o estado já persistido e zero efeitos adicionais. Divergência de event,
command, outcome, message ou hash para a mesma identidade é erro permanente.

## 10. CommandWorker

Fluxo `run_once`:

1. `claim_command`;
2. se o workflow ainda está queued, `ExecutionStarted` é aplicado na mesma
   transação do primeiro claim;
3. chama `prepare` fora da transação;
4. se falhar antes do fence, registra a falha;
5. se o orçamento permitir, volta a `QUEUED` operacionalmente com o mesmo
   command ID e zero dispatch;
6. ao esgotar ou em falha definitiva, grava `not_called`;
7. se preparar, chama `fence_dispatch`;
8. depois do commit do fence, chama `dispatch` exatamente uma vez;
9. grava outcome;
10. nunca entrega outbox no mesmo worker.

O worker aceita relógio injetado e não usa sleep interno.

## 11. Dispatch safety

O dispatch fence é a decisão durável de que uma chamada pode ocorrer. A store
incrementa `dispatch_slots_consumed` de 0 para 1 com compare-and-swap. Nenhum
código consegue obter segundo permit.

Depois do permit:

- retorno `effect_confirmed` exige provider reference e evidence não vazio;
- retorno `called_no_effect` termina sem retry;
- retorno `called_unknown` termina em revisão manual;
- retorno `not_called` do método `dispatch` é violação de contrato e é promovido
  conservadoramente para `called_unknown`;
- exception, timeout, processo morto ou resposta perdida é `called_unknown`;
- lease/token antigo não pode gravar outcome.

`provider_calls` é medido no adapter fake, não inferido do banco. O ledger mede o
limite mais conservador: dispatch slots possíveis.

## 12. Reconciliação

`Reconciler.run_once(now)` trata:

| Situação | Ação |
|---|---|
| `PREPARING`, lease expirado, zero dispatch | libera para novo claim |
| `DISPATCH_FENCED`, lease expirado, sem outcome | cria `called_unknown` sem chamar adapter |
| outcome persistido e state ainda `executing` | reaplica somente a transição local |
| `uncertain` sem manual review | aplica `ManualReviewRequested` |
| outbox pending/lease expirado | deixa para `OutboxWorker` |
| token antigo | rejeita sem mudança |

O reconciler nunca recebe `ExecutionAdapter` e, portanto, é estruturalmente
incapaz de redispatch.

## 13. Outcome e reducer

A Fase 5 endurece `ExecutionOutcome`:

- `effect_confirmed` exige provider reference e pelo menos um evidence hash;
- `not_called` não pode conter provider reference;
- `called_no_effect` pode conter referência/evidence, mas nunca gera retry;
- `called_unknown` pode conter evidence parcial;
- outcome divergente para command ID já concluído falha fechado.

A transição `called_unknown → manual_review` é obrigatória e eventual. O closeout
não aceita ledger unknown sem state uncertain/manual review correspondente.

## 14. OutcomeProjection e outbox

A projeção é pura e usa somente outcome/state tipados. Ela produz template IDs
fechados:

```text
reservation.execution.succeeded.v1
reservation.execution.not_called.v1
reservation.execution.no_effect.v1
reservation.execution.manual_review.v1
```

Não há texto improvisado, nome/ID de provider público ou promessa sem evidence.
A mensagem final é inserida na mesma transação do outcome/state.

`OutboxWorker`:

1. reivindica uma mensagem com lease próprio;
2. chama `DeliveryPort.deliver`;
3. grava receipt hash e `DELIVERED`;
4. em falha, libera/expira somente o lease da mensagem;
5. nunca lê ou modifica ledger para reexecutar comando.

A semântica de transporte é at-least-once com idempotency key. Exactly-once de
mensagem externa não é alegado nesta fase.

## 15. Fault injection

Pontos obrigatórios:

1. antes de persistir evento;
2. depois do evento e antes do state update;
3. depois do state e antes do command;
4. depois do command e antes do ledger;
5. depois do ledger e antes do commit;
6. depois do commit e antes do claim;
7. depois do claim e antes de `prepare`;
8. durante `prepare`;
9. depois de `prepare` e antes do fence;
10. depois do fence e antes de `dispatch`;
11. durante `dispatch`;
12. depois de `dispatch` e antes do outcome;
13. depois do outcome e antes do state;
14. depois do state e antes da outbox;
15. depois da outbox e antes do commit;
16. durante delivery;
17. depois da delivery e antes do receipt.

Esperado:

```text
commands_per_workflow <= 1
dispatch_slots_consumed <= 1
provider_calls <= 1
called_unknown_redispatches == 0
outbox_failures_causing_provider_calls == 0
partial_transactions == 0
```

## 16. Concorrência e restart

Testes obrigatórios usam arquivo SQLite real:

- duas conexões concorrentes aplicam a mesma revision;
- dois processos tentam claim do mesmo comando;
- worker morre com lease ativo;
- fencing token antigo tenta concluir;
- banco é fechado/reaberto entre todas as janelas críticas;
- dois processos tentam claim da mesma outbox;
- backlog é drenado após restart.

O fake adapter registra chamadas em artefato temporário compartilhado para provar
contagem cross-process. Arquivos temporários nunca entram em evidência versionada.

## 17. Properties

O gate operacional parte de `new_workflow`, atravessa os adapters read-only
in-memory das Fases 3–4 e chega ao worker/store da Fase 5.

Cobertura mínima:

- Cloudbeds e Bókun positivos;
- outcomes nas quatro certezas;
- preparação retryable/definitiva;
- leases válidos/expirados;
- crash schedules;
- outbox success/failure/recovery;
- duplicates idênticas e conflitantes;
- SQLite memory e file-backed em grupos separados.

Oráculo bilateral:

- comando autorizado deve chegar a um terminal seguro;
- comando não autorizado nunca entra na store;
- dispatch permitido exige comando persistido/autorizado;
- nenhum schedule produz segundo dispatch/provider call;
- unknown nunca retorna a queued/preparing;
- delivery nunca muda outcome ou dispatch counters.

Workload mínimo congelado:

```text
operational_property_cases = 20_000
file_backed_restart_schedules = 2_000
multiprocess_contention_rounds = 50
seed = 2026071905
```

O CI terá modo gate separado de `--smoke`, timeout máximo de 15 minutos e
orçamento de RSS de 256 MiB para o job da Fase 5. O plano pode elevar, mas nunca
reduzir esses mínimos.

## 18. Mutation testing

Catálogo com no mínimo 20 mutantes materiais cobre:

- remover optimistic revision;
- aceitar event hash divergente;
- separar command do commit;
- remover unique idempotency;
- aceitar segundo dispatch slot;
- ignorar fencing token;
- recuperar lease pós-dispatch como retry;
- transformar exception pós-fence em `not_called`;
- permitir `dispatch` retornar `not_called`;
- reexecutar após `called_unknown`;
- fundir outbox failure com command retry;
- marcar delivered sem receipt;
- aceitar outcome divergente;
- aceitar command hash adulterado;
- reduzir workload do property gate;
- remover um fault point obrigatório.

Mutantes executam somente em cópias temporárias. Mutante sensível a ordem precisa
ser testado sob múltiplos `PYTHONHASHSEED`.

## 19. DDL PostgreSQL

O arquivo `schemas/phase5/postgresql.sql` contém:

- tipos/checks equivalentes;
- PK/FK/unique constraints;
- optimistic revision;
- timestamps `timestamptz`;
- JSON canônico armazenado como texto mais hash nesta fase do contrato;
- nenhuma connection string, policy, role, auth ou RPC live.

Claims não permitidos:

- não prova compatibilidade de driver;
- não prova locking real do PostgreSQL;
- não prova Supabase RLS/RPC;
- não prova comportamento multiprocess PostgreSQL.

Essas provas pertencem à integração/migração posterior.

## 20. Evidência e CI

Diretório:

```text
docs/refactor/evidence/phase-05/
```

Artefatos planejados:

- `entry-baseline.json`;
- `red-result-*.json`;
- `schema-manifest.json`;
- `package-manifest.json`;
- `fault-matrix.json`;
- `property-result.json`;
- `concurrency-result.json`;
- `restart-result.json`;
- `mutation-result.json`;
- `performance-result.json`;
- `adversarial-review.md`;
- `validation-result.json`;
- `ci-result.json`;
- `SHA256SUMS`.

CI da Fase 5:

1. validadores 0–4;
2. regenerar DDL/manifests;
3. unit/contract tests;
4. restart/race/fault injection;
5. properties no workload congelado;
6. mutation catalog;
7. validator de pureza/evidência;
8. compileall e diff check.

## 21. Segurança e sanitização

Proibido versionar:

- bancos SQLite gerados;
- arquivos WAL/SHM;
- connection strings;
- tokens/segredos;
- PII real;
- payload provider real;
- logs brutos de worker/delivery;
- provider reference real;
- mensagem real.

Fixtures usam IDs, e-mails, telefones e references sintéticos reconhecíveis como
inválidos fora do teste. Evidência registra apenas contagens, hashes, statuses,
exit codes, duração e RSS.

## 22. Gate de entrada

- Fase 4 concluída no SHA `e51259ea0d19a2d07d3d14ee086b0766776cbeab`;
- `HEAD == origin/main == remote`;
- árvore limpa;
- cinco workflows do SHA final em `success`;
- validadores 0–4 em `ok`;
- legado somente leitura no fingerprint canônico;
- SQLite local autorizado;
- Docker/PostgreSQL/Supabase não autorizados;
- unidade transacional única aprovada;
- design completo aprovado pelo usuário.

## 23. Gate de saída

1. state + event + command + ledger são atômicos;
2. optimistic revision e deduplicação falham fechado;
3. comando permanece imutável e deterministicamente ligado à autorização;
4. lease abandonado antes do dispatch é recuperável;
5. stale fencing token é incapaz de concluir;
6. dispatch slot e provider call são no máximo um;
7. falha pós-fence nunca gera retry automático;
8. `called_unknown` chega a manual review;
9. falha de outbox nunca repete provider;
10. restart e corrida multiprocesso passam;
11. fault matrix completa passa;
12. DDL SQLite/PostgreSQL é regenerável do contrato comum;
13. properties e mutation catalog passam sem redução de workload;
14. validadores regressivos 0–5 passam;
15. nenhuma capacidade live existe;
16. commit remoto e CI final são verificados;
17. Fase 6 não é iniciada;
18. rollout permanece `NO-GO`.

## 24. Rollback

Reverter somente os commits da Fase 5 no repositório novo. Bancos SQLite são
artefatos temporários de teste e são descartados. Não há rollback live, provider,
ManyChat, Supabase ou deploy porque nada disso é tocado.

## 25. Riscos residuais explícitos

1. SQLite não prova semântica de locking PostgreSQL.
2. DDL PostgreSQL não executado pode conter incompatibilidade não observada.
3. Exactly-once externo depende de idempotência/reconciliação do provider;
   localmente prova-se no máximo um dispatch possível.
4. Crash imediatamente após fence e antes do socket pode gerar falso unknown;
   é custo deliberado da política safety-first.
5. Delivery externa pode ser at-least-once; esta fase não alega exactly-once de
   mensagem.
6. Fake adapter prova o protocolo do worker, não o schema técnico real do
   provider.
7. Nenhuma evidência desta fase autoriza rollout comercial.
