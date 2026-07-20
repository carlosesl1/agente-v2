# Fase 7 — Migração das fronteiras: design vinculante

**Status:** aprovado por Carlos em 2026-07-20; implementação ainda não iniciada

**Branch:** `phase7-boundary-migration`

**Commit de entrada:** `4169c6149f76e8bf4f30a26ee9d0bfbc43a58984`

**Owner canônico:** repositório `agente-v2`

**Runtime consumidor observado:** `/home/ubuntu/chapada-leads-hermes`

## 1. Objetivo

Fazer runner, plugin e executor consumirem os mesmos contratos tipados publicados
pelas Fases 2–6, sem permitir que LLM, plugin, prompt, alias legado ou timeout
paralelo volte a autorizar efeitos comerciais.

A Fase 7 entrega quatro fronteiras únicas:

1. `LegacyStateImporter` para dual-read/single-write;
2. `TurnCoordinator` para lock, deadline, ordem e persistência do turno;
3. `ToolDispatch` para traduzir tools em reads ou comandos tipados;
4. `DecisionComparator` para comparação antiga/nova offline e fail-closed.

O resultado é um candidato integrável e testado contra uma réplica autenticada
do runtime. Não há deploy, shadow live, provider live, ManyChat live, canary ou
rollout nesta fase.

## 2. Entrada autenticada

A Fase 6 está fechada com:

- closeout documental `4169c6149f76e8bf4f30a26ee9d0bfbc43a58984`;
- implementação `8f23a8376f1d226f2ada5d80a45cbb930a79429e`;
- tree de implementação `5af8f650c457c61acbe9e5355f83d92e0a866135`;
- sete workflows remotos em `success`;
- seis jobs da Fase 6 em `success`;
- rollout `NO-GO`;
- `phase7_started=false` até a autorização explícita recebida em 2026-07-20.

Baseline econômico do worktree da Fase 7:

- `tests.test_phase6_closeout`: 14/14;
- validator da Fase 6: `passed`;
- manifest da Fase 6: `passed`;
- Python `3.12.13`;
- SQLite `3.46.1`;
- working tree limpa.

## 3. Problema atual

O runtime observado distribui ownership entre superfícies grandes e parcialmente
sobrepostas:

- `app.py::_process_event` carrega estado, chama planner, sanitiza, persiste,
  executa handoff, cria outbox e envia;
- `domain/hermes_native_runner.py::NativeHermesAgentRunner` mantém rounds,
  budgets, confirmação, tool execution, guards, rota e derivação de estado;
- `domain/tool_executor.py::ToolExecutor` mistura classificação, preenchimento,
  confirmação, ordem, cache, idempotência, provider normalization e claims;
- `.hermes/plugins/chapada_leads_tools/__init__.py` registra schemas e abre um
  subprocesso por tool;
- `LeadState.metadata` ainda carrega decisões, fases, seleções e tool order em
  dicionários permissivos.

A árvore operacional observada possui 80 entradas no status Git: 61 modificadas,
13 removidas e seis caminhos untracked. Ela não pode receber patches oportunistas
nem ser tomada como uma base limpa.

## 4. Decisão arquitetural

Adotar migração **contract-first**, com `agente-v2` como owner e o runtime como
consumidor por adapters.

Não extrair a arquitetura nova a partir das classes monolíticas do runtime. Os
adapters compatibilizam o runtime com contratos novos; não copiam seus guards
para o kernel.

### 4.1 Topologia de repositórios

#### `agente-v2`

Contém:

- packages puros das Fases 2–6;
- novo package `reservation_boundary`;
- distribuição Python offline `chapada-reservation-kernel`;
- fixtures sanitizadas de contratos da fronteira;
- manifests, validator, properties, faults e CI da Fase 7;
- patch de integração produzido contra a réplica autenticada do runtime.

#### Runtime observado

`/home/ubuntu/chapada-leads-hermes` permanece intocado durante toda a Fase 7.

Uma réplica descartável é reconstruída a partir de:

1. commit-base `57408d8b2040399bc25ee7957505208079458884`;
2. patch tracked gerado por `git diff --binary --full-index HEAD`;
3. allowlist de paths untracked:
   - `docs/architecture/`;
   - `domain/agent_tool_feedback.py`;
   - `qa/model_benchmark/`;
   - `tests/test_agent_tool_feedback.py`;
   - `tests/test_manychat_single_confirmation_flow.py`;
   - `tests/test_model_benchmark.py`;
4. manifesto SHA-256 de todos os inputs;
5. scanner de segredo, PII, bancos, logs e artefatos de runtime antes da cópia.

A réplica não inclui `.env`, backups de credenciais, bancos, Redis dumps, logs,
comprovantes, screenshots, caches, `venv`, outputs de runs ou payloads brutos.

A árvore original é autenticada antes e depois da reconstrução. Qualquer drift
invalida a réplica e bloqueia a integração.

### 4.2 Artefato Python

O repositório passa a declarar uma distribuição Python:

```text
name: chapada-reservation-kernel
version: 0.7.0
python: >=3.12
runtime dependencies: nenhuma
packages:
  reservation_domain
  reservation_lookup
  reservation_confirmation
  reservation_execution
  reservation_followup
  reservation_boundary
```

O ambiente de entrada não contém `setuptools` nem `wheel`. Portanto, o build não
pode depender de download, build isolation ou backend externo.

`scripts/build_phase7_wheel.py` usa apenas stdlib (`zipfile`, `hashlib`,
`base64`, `csv`) para produzir um wheel `py3-none-any` válido, com:

- paths ordenados;
- timestamp ZIP fixo em `1980-01-01T00:00:00`;
- permissões canônicas;
- `METADATA`, `WHEEL`, `top_level.txt` e `RECORD` determinísticos;
- hashes `sha256=` URL-safe no `RECORD`;
- nenhum arquivo fora dos seis packages allowlisted.

O build é executado duas vezes em diretórios temporários e os bytes devem ser
idênticos. A réplica instala o wheel com:

```bash
python3 -m pip install --no-index --no-deps \
  --target /tmp/phase7-wheel-install \
  dist/chapada_reservation_kernel-0.7.0-py3-none-any.whl
```

O smoke import remove o checkout do `sys.path`, importa apenas de
`/tmp/phase7-wheel-install` e confirma commit/tree/package manifest.
`PYTHONPATH` não vale como prova terminal de integração. O wheel não contém
testes, evidências, docs ou arquivos de runtime.

## 5. Package `reservation_boundary`

Estrutura prevista:

```text
reservation_boundary/
  __init__.py
  types.py
  serialization.py
  legacy_state.py
  coordinator.py
  dispatch.py
  shadow.py
  properties.py
  faults.py
```

Cada módulo possui um único owner.

### 5.1 Tipos fechados

`types.py` define enums/dataclasses frozen. Dicionários `Any` não atravessam a
fronteira pública.

```python
class ImportDisposition(Enum):
    MIGRATED = "migrated"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"

class DispatchKind(Enum):
    READ = "read"
    COMMAND = "command"
    STATE_COMMIT = "state_commit"

class DivergenceSeverity(Enum):
    EQUIVALENT = "equivalent"
    NONCRITICAL = "noncritical"
    CRITICAL = "critical"
```

Contratos principais:

```python
@dataclass(frozen=True)
class LegacyLeadSnapshot:
    schema_version: int
    lead_key: str
    version: int
    stage: str
    ai_status: str
    language: str
    desired_services: tuple[str, ...]
    missing_slots: tuple[str, ...]
    collected_slots: tuple[SlotValue, ...]
    hostel_reservations: tuple[LegacyReservationRef, ...]
    agency_bookings: tuple[LegacyReservationRef, ...]
    decision_metadata: LegacyDecisionMetadata

@dataclass(frozen=True)
class ImportResult:
    disposition: ImportDisposition
    boundary_state: BoundaryState | None
    reason: ImportReason
    legacy_fingerprint: str

@dataclass(frozen=True)
class TurnEnvelope:
    schema_version: int
    event_id: str
    lead_key: str
    expected_version: int
    received_at: datetime
    deadline_at: datetime
    message: NormalizedMessage

@dataclass(frozen=True)
class TurnPlan:
    state: BoundaryState
    commands: tuple[ReservationCommand, ...]
    outbox: tuple[OutboxMessage, ...]
    dispatch_requests: tuple[ToolDispatchRequest, ...]
    handoff_required: bool
    reason: TurnPlanReason
```

`SlotValue` é uma union discriminada de string, inteiro exato, decimal canônico,
data, datetime e booleano. `bool` nunca passa como `int`.

Tipos auxiliares também são fechados:

```python
class ConversationIntentKind(Enum):
    PROVIDE_FACTS = "provide_facts"
    CHOOSE_OFFER = "choose_offer"
    CONFIRM = "confirm"
    REJECT = "reject"
    ADJUST = "adjust"
    ASK = "ask"
    HANDOFF = "handoff"

class ImportReason(Enum):
    COMPLETE = "complete"
    COLLECTING_SAFE = "collecting_safe"
    MISSING_CANONICAL_IDENTITY = "missing_canonical_identity"
    CONFLICTING_IDENTITY = "conflicting_identity"
    UNPROVEN_EFFECT = "unproven_effect"
    UNKNOWN_SCHEMA = "unknown_schema"
    AMBIGUOUS_SUBJECT = "ambiguous_subject"

class TurnPlanReason(Enum):
    APPLIED = "applied"
    DUPLICATE_EVENT = "duplicate_event"
    MANUAL_REVIEW = "manual_review"
    IMPORT_REJECTED = "import_rejected"
    DEADLINE_EXPIRED = "deadline_expired"
    VERSION_CONFLICT = "version_conflict"
    INVALID_INTENT = "invalid_intent"

@dataclass(frozen=True)
class ConversationIntent:
    kind: ConversationIntentKind
    source_event_id: str
    facts: tuple[TypedFact, ...] = ()
    offer_id: str | None = None
    target_draft_version: int | None = None

@dataclass(frozen=True)
class BoundaryState:
    schema_version: int
    lead_key: str
    version: int
    reservation: WorkflowState | None
    handoff: HandoffWorkflow | None
    payments: tuple[PaymentWorkflow, ...]
    processed_event_ids: tuple[str, ...]

@dataclass(frozen=True)
class NormalizedMessage:
    text: str
    language: str
    channel: str

@dataclass(frozen=True)
class IntentRequest:
    event_id: str
    state: BoundaryState
    message: NormalizedMessage
    deadline_at: datetime

@dataclass(frozen=True)
class ToolDispatchRequest:
    event_id: str
    lead_key: str
    tool_name: str
    arguments: ToolArguments
    deadline_at: datetime

@dataclass(frozen=True)
class KernelDecision:
    next_state: BoundaryState
    commands: tuple[ReservationCommand, ...]
    outbox: tuple[OutboxMessage, ...]
    dispatch_requests: tuple[ToolDispatchRequest, ...]
    handoff_required: bool
    reason: TurnPlanReason

@dataclass(frozen=True)
class TurnLease:
    lead_key: str
    event_id: str
    fencing_token: int
    expires_at: datetime

@dataclass(frozen=True)
class VersionedBoundaryState:
    state: BoundaryState
    state_hash: str

@dataclass(frozen=True)
class BoundaryCommit:
    lead_key: str
    expected_version: int
    fencing_token: int
    event_id: str
    next_state: BoundaryState
    commands: tuple[ReservationCommand, ...]
    outbox: tuple[OutboxMessage, ...]
```

`TypedFact` e `ToolArguments` são unions discriminadas e fechadas. Cada variante
possui apenas escalares canônicos e IDs opacos. `WorkflowState` é a projeção
fechada dos tipos já publicados em `reservation_domain`; `HandoffWorkflow` e
`PaymentWorkflow` são usados diretamente de `reservation_followup`.

Invariantes de construção:

- `schema_version == 1`;
- `lead_key`, `event_id` e IDs não vazios;
- `version >= 0`, `expected_version >= 0`, `fencing_token > 0`;
- `deadline_at`, `received_at` e `expires_at` timezone-aware em UTC;
- `processed_event_ids`, commands, outbox e payments sem duplicatas;
- `ConversationIntentKind.CONFIRM` exige `target_draft_version` inteiro exato;
- `CHOOSE_OFFER` exige `offer_id`; outros kinds rejeitam `offer_id` indevido;
- `KernelDecision.next_state.version == input.version + 1` apenas quando existe
  mudança persistível;
- commands/outbox do commit devem ser exatamente os emitidos pelo reducer.

### 5.2 Serialização

`serialization.py` implementa JSON wire versionado com:

- schema version exata `1`;
- chaves fechadas;
- rejection de chaves duplicadas;
- escalares canônicos;
- datas/datetimes em ISO canônico;
- decimal como string canônica;
- arrays ordenados quando a ordem é semântica;
- round-trip byte-estável;
- semantic hash SHA-256.

Dados pessoais e provider refs privados não entram em evidência versionada.

## 6. `LegacyStateImporter`

### 6.1 Regra dual-read/single-write

Ordem obrigatória:

1. procurar `BoundaryState` novo por `lead_key`;
2. se existir, usá-lo e não ler estado legado;
3. se não existir, ler um único snapshot legado;
4. converter deterministicamente;
5. persistir uma gênese nova com compare-and-swap;
6. nunca escrever `LeadState` legado;
7. se outra instância vencer a gênese, reler apenas o estado novo.

Não existe dual-write, merge last-write-wins ou fallback do novo para o legado.

### 6.2 Identidade

- `lead_key` é obrigatório e opaco;
- telefone, subscriber ID, nome e label não geram identidade nova;
- offer/product/room/tour IDs vêm apenas de seleção/evidência canônica;
- nomes públicos nunca recompõem IDs;
- command IDs e idempotency keys são recalculados dos contratos tipados;
- divergência entre ID persistido e evidência produz `MANUAL_REVIEW`.

### 6.3 Mapeamento de estados

- `new`, `recepcionista`, `hostel`, `agency/agencia` e `fechamento` podem migrar
  para o workflow de reserva se os fatos necessários forem tipáveis;
- `handoff` cria/recupera `HandoffWorkflow`, preservando precedência terminal;
- reservation refs pagas/pendentes só criam `PaymentWorkflow` quando existe
  `ConfirmedReservationAnchor` recomponível;
- resumo/aceite só migram quando draft version, subject signature e resumo
  apresentado são coerentes;
- lookup só migra com provenance, TTL, provider snapshot hash e `offer_id`;
- estado sem material suficiente para autorização pode migrar para coleta, mas
  nunca para confirmação/comando;
- estado que alega efeito sem outcome/evidência vai a `MANUAL_REVIEW`;
- schema desconhecido, tipo ambíguo, duplicidade de sujeito ou identidade
  conflitante resulta em `REJECTED`.

### 6.4 Não inferência

É proibido inferir de texto, label ou memória livre:

- `offer_id`;
- `tour_product_id`;
- `room_type_id`;
- confirmação atual;
- reservation/booking/payment success;
- provider reference;
- evidence trust;
- dispatch certainty.

## 7. `TurnCoordinator`

### 7.1 Ownership

O coordinator decide somente:

- lock/fencing do turno;
- event identity e dedupe;
- deadline conversacional;
- ordem de leitura/importação/interpretação/reducer/persistência;
- compare-and-swap de versão;
- commit atômico de estado, comando e outbox;
- handoff fail-closed em erro de fronteira.

Não decide regra comercial, texto público, provider, retry de efeito ou
confirmação.

### 7.2 Ports

```python
class TurnLockPort(Protocol):
    def claim(self, *, lead_key: str, event_id: str, now: datetime) -> TurnLease: ...

class BoundaryStorePort(Protocol):
    def load(self, lead_key: str) -> VersionedBoundaryState | None: ...
    def commit(self, transition: BoundaryCommit) -> VersionedBoundaryState: ...

class LegacyStateReadPort(Protocol):
    def load_snapshot(self, lead_key: str) -> LegacyLeadSnapshot | None: ...

class IntentPort(Protocol):
    def interpret(self, request: IntentRequest) -> ConversationIntent: ...

class KernelPort(Protocol):
    def reduce(self, state: BoundaryState, intent: ConversationIntent) -> KernelDecision: ...
```

Os ports são caller-supplied. O package não lê ambiente, auth, filesystem,
rede, Supabase, Redis, ManyChat ou providers.

### 7.3 Algoritmo

1. validar tipos e deadline;
2. claim do turno;
3. rejeitar/deduplicar event ID;
4. carregar estado novo ou importar legado;
5. construir `IntentRequest` mínimo;
6. obter `ConversationIntent` tipada;
7. chamar o kernel puro;
8. produzir estado/comandos/outbox;
9. persistir uma única transação CAS;
10. retornar `TurnPlan` já persistido.

Nenhum tool/provider write ocorre dentro de `coordinate()`.

### 7.4 Timeout e falhas

- deadline vencida antes do commit: zero write;
- import `MANUAL_REVIEW`: commit apenas do estado de revisão + outbox de handoff;
- import `REJECTED`: zero migração e handoff técnico;
- intent inválida: zero comando;
- CAS conflitante: reler estado novo e deduplicar; não reler legado;
- falha após commit: reentrega usa outbox e não recalcula Maya;
- comando já persistido nunca é substituído por tool call divergente.

## 8. `ToolDispatch`

### 8.1 Regra central

A Maya pode solicitar uma tool; apenas `ToolDispatch` classifica e traduz.

- `READ`: converte para request de adapter read-only e retorna `LookupResult` ou
  FAQ tipada;
- `COMMAND`: converte a intenção em evento/comando do kernel; não chama provider
  no turno;
- `STATE_COMMIT`: converte commits conversacionais permitidos em
  `ConversationIntent`/facts; não escreve diretamente o store;
- nome não catalogado: rejeição fechada;
- aliases legados não podem alterar categoria ou identidade.

### 8.2 Catálogo

O catálogo é fechado, versionado e definido em um único módulo. Cada entrada
possui:

- nome público da proxy tool;
- `DispatchKind`;
- schema de argumentos tipado;
- owner de normalização;
- command/event resultante;
- claim evidence permitida;
- política de cache apenas para reads;
- requisito de revalidation antes de command.

### 8.3 Proibições

`ToolDispatch` não:

- importa SDK/provider;
- lê env/auth;
- abre subprocesso;
- executa HTTP;
- escreve Supabase/Redis;
- envia ManyChat/e-mail;
- cria comando a partir de booleano da LLM;
- aceita nome/label como produto;
- permite retry automático de outcome `called_unknown`.

## 9. Integração da réplica do runtime

O patch de integração é mínimo e consumível.

### 9.1 Arquivos novos previstos

```text
domain/turn_coordinator_adapter.py
domain/tool_dispatch_adapter.py
tests/test_phase7_turn_coordinator_adapter.py
tests/test_phase7_tool_dispatch_adapter.py
tests/test_phase7_runtime_boundary.py
```

### 9.2 Arquivos modificados previstos

```text
app.py
domain/hermes_native_runner.py
domain/tool_executor.py
domain/chapada_native_tools.py
.hermes/plugins/chapada_leads_tools/__init__.py
pyproject.toml
```

### 9.3 Responsabilidades finais

- `app.py`: ingress/channel wiring e chamada do coordinator adapter;
- `NativeHermesAgentRunner`: Maya/intent/text e transporte de resultados;
- plugin: registro de schemas + marshal/unmarshal, sem regra comercial;
- `ToolExecutor`: provider adapter compatível, atrás de `ToolDispatch`;
- `reservation_boundary`: decisão de fronteira e contratos;
- workers Fases 5/6: únicos executores de commands/effects.

### 9.4 Compatibilidade

A rota atual permanece disponível somente como oracle de comparação em testes.
Ela não recebe side effects e não é fallback de produção.

O patch não ativa feature flag live, não altera Docker/deploy, não configura
credenciais e não muda ManyChat.

## 10. `DecisionComparator`

### 10.1 Entrada

```python
@dataclass(frozen=True)
class DecisionObservation:
    route: RouteClass
    public_reply_kind: PublicReplyKind
    handoff_required: bool
    subject_signature: str | None
    command_identities: tuple[str, ...]
    dispatch_kinds: tuple[DispatchKind, ...]
    effect_certainties: tuple[str, ...]
    claim_evidence: tuple[str, ...]
```

### 10.2 Classificação

`CRITICAL` quando divergir em:

- allow/block de command ou side effect;
- identidade/subject/offer/target;
- número ou identidade de comandos;
- confirmação requerida;
- handoff terminal;
- payment/effect certainty;
- provider retry safety;
- claim pública factual;
- persistência antes de entrega.

`NONCRITICAL` somente para:

- route label equivalente;
- texto/copy sem alteração factual;
- tags diagnósticas;
- ordenação não semântica de observabilidade.

`EQUIVALENT` quando a projeção canônica for byte-equivalente.

### 10.3 Gate

- divergências críticas: exatamente zero;
- toda divergência noncritical: catalogada e reconstruível;
- comparator não pode importar reducers antigos ou novos para derivar o oracle;
- totais são reconstruídos das rows;
- JSON duplicado/tipo falso falha fechado.

## 11. Persistência de teste

A Fase 7 implementa stores de contrato em SQLite local/temporário:

- `boundary_state`;
- `boundary_events`;
- `boundary_commands`;
- `boundary_outbox`;
- `legacy_import_claims`;
- `decision_comparisons`.

SQLite usa `STRICT`, foreign keys, uniqueness e CAS. PostgreSQL permanece apenas
DDL estático regenerável e não é executado.

Nenhuma tabela replica ledgers/outboxes das Fases 5/6; ela referencia suas
identidades canônicas.

## 12. Segurança e capabilities

Durante toda a Fase 7:

- rede da aplicação: zero;
- providers/LLM/ManyChat/e-mail live: zero;
- Supabase/Redis/PostgreSQL/Docker: não executados;
- SQLite: somente `:memory:` ou diretório temporário;
- runtime original: somente leitura;
- raw messages, PII e provider payloads: não versionados;
- secrets/tokens/auth: não lidos nem copiados;
- artifacts: manifests/hashes/contagens e fixtures sintéticas.

O validator percorre AST/call graph e rejeita imports/calls de rede, provider,
env/auth, processo e execução live no package puro.

## 13. Estratégia TDD econômica

### 13.1 Desenvolvimento

Para cada tarefa:

1. um RED focused causal;
2. implementação mínima;
3. GREEN focused;
4. regressão somente dos packages/fixtures atingidos;
5. commit pequeno;
6. nenhuma revisão/subagente por rotina.

### 13.2 Blast radius

- mudança só em importer: importer/serialization/store tests;
- mudança só em coordinator: coordinator + atomicity/deadline faults;
- mudança só em dispatch: dispatch + affected adapter tests;
- mudança só no runtime adapter: integration focused da réplica;
- mudança só em docs/manifests: validator/manifests;
- package público/tipos/wire/schema: regressão dos consumidores diretos.

### 13.3 Candidato congelado

Uma única **janela de validação pesada** é aberta quando:

- todos os focused tests estão verdes;
- tree funcional está congelada;
- manifests e patch da réplica estão fechados;
- não existe delta unstaged;
- a branch ainda não foi publicada.

A janela possui dois estágios não sobrepostos. Nenhum gate roda nos dois.

#### Estágio local privado — réplica do runtime

Executado uma vez porque o CI público não recebe o source snapshot operacional:

1. suíte integral da réplica do runtime, com env limpo e config explícita;
2. aplicação e reversão do patch de integração;
3. wheel build duplo, instalação offline e import pela réplica;
4. tests de adapter runner/plugin/executor;
5. autenticação do runtime original sem drift.

Este estágio não executa a suíte integral nem properties do `agente-v2`.

#### Estágio remoto — candidato `agente-v2`

Depois do estágio privado verde, a branch congelada é publicada uma vez. O
primeiro push desse branch dispara um único ciclo de `phase7.yml`, que executa:

1. suíte integral do `agente-v2`;
2. properties de import/dual-read/shadow;
3. fault matrix de CAS/deadline/crash;
4. restart/contention para gênese single-write;
5. mutation catalog material da fronteira;
6. validators 0–7;
7. wheel reproducibility e manifest checks;
8. compile, checksums e scans.

Os workflows históricos não escutam o branch da Fase 7; seus validators estáticos
necessários rodam dentro de `phase7.yml`, sem repetir workloads pesados. Depois do
ciclo verde e da revisão terminal, a integração em `main` usa merge commit com
`[skip ci]` somente se seu tree for byte-idêntico ao tree do candidato aprovado;
qualquer delta funcional invalida essa dispensa. O closeout documental também
usa `[skip ci]` quando não contém delta funcional.

Após congelamento, correção sem delta de produção repete apenas o focused e o
gate afetado. Mudança material em package público, wire, schema, algoritmo de
coordinator/import/dispatch ou patch funcional cria **novo candidato**, fecha a
janela anterior e permite uma nova janela pesada. Não existe rerun apenas para
“refrescar” evidência.

## 14. Revisão econômica

Não há fan-out durante cada tarefa.

No candidato congelado, uma única rodada de revisão independente cobre escopos
não sobrepostos:

1. identidade, importação e dual-read/single-write;
2. coordinator, dispatch, deadline e side-effect boundary;
3. réplica/proveniência, patch, comparator, CI e claims documentais.

Revisor deve autenticar o mesmo commit/tree/package. Timeout, summary ausente,
`Needs fixes` ou finding Critical/Important invalida a rodada. Revisão repetida
só é permitida após correção que produza informação nova.

## 15. CI da Fase 7

Workflow `phase7.yml` contém jobs paralelos:

1. `static-validation`;
2. `full-suite`;
3. `boundary-properties-faults`;
4. `package-runtime-contract`;
5. `phase7-gate`.

Triggers fechados:

```yaml
on:
  push:
    branches: [phase7-boundary-migration]
  workflow_dispatch:
```

Não há trigger de `pull_request` ou `main`. O branch é publicado somente depois
do estágio privado e nunca recebe push sem nova tree funcional. O merge em
`main` usa `[skip ci]` e tree idêntico, evitando um segundo ciclo pesado.

O CI do `agente-v2` é autocontido: não clona o repositório operacional e não usa
credenciais cross-repo. Ele valida:

- package/wheel;
- fixtures sanitizadas;
- runtime contract manifest;
- patch de integração;
- evidência local autenticada da réplica;
- workloads determinísticos do `agente-v2`.

A execução integral da réplica ocorre uma vez no gate local congelado e é
registrada por hash/command/exit code. CI não falseia essa execução como live.

## 16. Evidências e manifests

Diretório:

```text
docs/refactor/evidence/phase-07/
```

Artefatos obrigatórios:

- `entry-baseline.json`;
- `runtime-source-manifest.json`;
- `runtime-integration.patch`;
- `runtime-contract-manifest.json`;
- `migration-property-result.json`;
- `shadow-comparison-result.json`;
- `fault-matrix.json`;
- `restart-result.json`;
- `contention-result.json`;
- `mutation-result.json`;
- `validation-result.json`;
- `performance-result.json`;
- `package-manifest.json`;
- `schema-manifest.json`;
- `wheel-manifest.json`;
- `adversarial-review.md`;
- `SHA256SUMS`;
- `ci-result.json` somente após CI remoto real.

Raw outputs ficam em `/tmp` e não são versionados.

## 17. Gates de aceite

A Fase 7 só pode fechar quando:

1. `main/origin/main/remote` de entrada estão autenticados;
2. runtime original está byte/estado equivalente antes e depois;
3. réplica reconstrói o candidato observado por manifest;
4. estado legado ativo é classificado sem inferência por label/texto;
5. dual-read gera no máximo uma gênese nova;
6. nenhuma escrita retorna ao estado legado;
7. `TurnCoordinator` é o único owner de ordem/deadline/persistência;
8. `ToolDispatch` é o único owner de classificação/tradução de tool;
9. plugin não contém regra comercial, budget, confirmação ou retry;
10. runner não autoriza side effect;
11. executor fica atrás dos ports tipados;
12. commands Fases 5/6 continuam únicos e imutáveis;
13. divergências críticas no corpus são exatamente zero;
14. estado não migrável termina em manual review/handoff;
15. wheel local instala e é consumido pela réplica;
16. patch aplica/reverte sem drift;
17. validação pesada única passa no candidato congelado;
18. revisão terminal agrega informação nova e aprova o mesmo candidato;
19. commit é publicado e CI remoto fica verde;
20. rollout permanece `NO-GO`;
21. `phase8_started=false`.

## 18. Não objetivos

A Fase 7 não:

- faz deploy;
- altera container/compose/ingress;
- liga feature flag live;
- usa ManyChat real;
- chama Cloudbeds/Bókun/Wise/Stripe;
- executa Supabase/Redis/PostgreSQL;
- faz shadow com tráfego real;
- faz canary/E2E live;
- migra registros reais;
- remove completamente o legado;
- inicia Fase 8.

## 19. Rollback

Como não há deploy, rollback da Fase 7 é:

1. não aplicar o patch ao runtime operacional;
2. remover a distribuição nova do ambiente de teste;
3. descartar a réplica;
4. reverter commits da branch da Fase 7 no `agente-v2`;
5. preservar manifests/evidência do candidato rejeitado;
6. confirmar runtime original sem drift.

Nenhum rollback chama provider, restaura banco ou reenvia mensagem.

## 20. Riscos específicos

### P7-R1 — Réplica não corresponde ao candidato operacional

**Mitigação:** base + patch + allowlist + hashes + autenticação antes/depois.

### P7-R2 — Importer inventa identidade ausente

**Mitigação:** proibir inferência por texto/label; manual review; properties e
mutantes de ID.

### P7-R3 — Dual-read vira dual-write

**Mitigação:** port legado sem método de write; AST/call-graph scan; contention de
gênese.

### P7-R4 — Coordinator vira novo cérebro comercial

**Mitigação:** KernelPort é único owner de reducer; coordinator só ordena e
persiste.

### P7-R5 — ToolDispatch preserva writes dentro do turno

**Mitigação:** command dispatch apenas persiste comando; provider ports vivem nos
workers.

### P7-R6 — Plugin fino continua decidindo por helper indireto

**Mitigação:** scan de calls/imports e mutation que reintroduz budget/confirm/retry.

### P7-R7 — Comparador se autocertifica

**Mitigação:** catálogo local independente, rows completas, totais reconstruídos
e mutantes de severidade.

### P7-R8 — Package testado por path diverge do artefato

**Mitigação:** wheel offline, install em ambiente temporário e import smoke pelo
manifest do wheel.

### P7-R9 — Validação pesada é repetida sem nova evidência

**Mitigação:** registrar tree congelada e matriz de blast radius; rerun integral
somente após mudança material vinculante.

## 21. Decisão de avanço

A aprovação desta spec autoriza escrever o plano TDD detalhado. Não autoriza
implementar antes da aprovação do plano, tocar a árvore operacional, executar
capabilities live ou iniciar a Fase 8.
