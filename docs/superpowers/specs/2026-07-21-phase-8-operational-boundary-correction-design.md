# Fase 8 — Correção upstream da fronteira operacional

## Estado

**Draft para aprovação. Implementação, nova wheel, build, canary e rollout permanecem
NO-GO.**

Esta spec corrige a lacuna descoberta entre o fechamento funcional da Fase 7 e o
graph realmente iniciado pelo container da Fase 8. Ela não autoriza alteração do
runtime operacional, provider write, delivery ManyChat, pagamento, build ou deploy.

## Âncoras autenticadas

### Source e runtime candidate

- branch Phase 8: `phase8-shadow-canary-rollout`;
- HEAD publicado: `ab02490ffdf5429d06aec957f988fb6ad56a4da1`;
- tree: `1613a7dcb6dd8730ac3d7f4f0c83caa97ae9a45f`;
- candidato de packaging auditado:
  `/home/ubuntu/workspace/agente-v2-phase8-runtime-candidate1`;
- commit do candidato: `853e523ab2f1bb8987b2f9c2302c759dcd464de7`;
- tree do candidato: `9c4577a7c96bfc0fc10fc2eedf7378af61f1ee04`;
- wheel Phase 7 `0.7.0`: 214954 bytes, SHA-256
  `be1bed664f9eb0a9f0af06b31bd55688e4041c81411ee1cc22416282270446dd`;
- worktrees relevantes estavam limpas na autenticação do desenho, exceto o RED
  isolado e não versionado do candidato experimental de wiring.

### Findings causais

1. `Dockerfile` inicia `uvicorn app:app`.
2. `app.py` publica `app = create_app()`.
3. `create_app(..., phase7_turn_adapter=None)` apenas copia o argumento para o
   contexto e não constrói nenhuma porta concreta.
4. O primeiro turno que alcança `_process_event` falha fechado com
   `RuntimeError("phase7 turn adapter is required")`.
5. `domain/turn_coordinator_adapter.py` é apenas um seam injetável; não existe
   composition factory, lock, store, reader, importer, intent, kernel ou result
   port de produção.
6. Os testes de regressão predominantes injetam um adapter legado por
   `qa.phase7_regression_support`; eles não autenticam o graph carregado pelo
   Docker.
7. O native agent roda como processo Hermes filho. O plugin Chapada lê contexto
   por env e usa um JSONL temporário (`state_commit_path`) para tool actions e
   commits. Esse arquivo é informal, pós-processado e removido no fim do turno.
8. O plugin atual pode chegar ao `ToolExecutor` diretamente. Isso permite reads e
   caminhos de write fora da autoridade transacional do coordinator.
9. O `TurnCoordinator` atual persiste estado/commands/outbox, mas retorna
   `public_messages=()`; o adapter monta e enfileira chunks **depois** do commit.
10. Em replay duplicado, o coordinator atual retorna estado corrente e outputs
    vazios. Ele não pode reproduzir os bytes públicos originais.
11. `boundary_commands` não é consumida pelos workers das Fases 5/6.
12. `boundary_outbox` usa o tipo de outbox da execução de reserva e não é uma
    outbox pública ManyChat.
13. `JsonPublicMessageOutbox` não é uma autoridade suficiente para a nova rota:
    aceita duplicata divergente silenciosamente e trata corrupção como store
    vazio.
14. O schema Phase 7 exige um universo literal de seis tabelas. Nenhum DB SQLite
    Phase 7 implantado foi localizado neste host na auditoria. Isso permite um
    schema v8 novo, mas qualquer DB v7 descoberto depois invalida essa premissa.
15. Stripe, Wise e actions de imagem ainda têm ingress mutantes fora do turn
    coordinator. Eles precisam de boundaries próprios ou capability desabilitada;
    não podem permanecer como bypass no graph promovido.

## Objetivo

Produzir uma fronteira operacional em que:

- todos os turnos concluídos entram por um único `TurnCoordinator` concreto;
- Maya continua sendo o único cérebro conversacional;
- o kernel continua sendo o único autorizador de transições e commands;
- ToolDispatch continua sendo o único catálogo/normalizador de tools;
- reads retornam resultados sanitizados à Maya e evidência tipada ao kernel;
- state facts e command proposals voltam ao processo pai sem arquivo informal;
- resposta pública, estado, commands e receipts são persistidos atomicamente;
- duplicate replay não roda Maya, provider read ou kernel novamente e preserva os
  mesmos bytes públicos;
- provider writes e delivery ficam exclusivamente em workers pós-commit;
- canary e produção usam a mesma factory, mesmas classes e mesmo manifest digest
  OCI, variando apenas roots e capabilities declaradas.

## Não objetivos

- não reativar o planner JSON ou `_process_legacy_event_for_regression_tests`;
- não mover regras conversacionais para o kernel;
- não permitir tool name/arguments livres no boundary;
- não chamar provider write durante o turno;
- não consolidar os schemas das Fases 5/6 no boundary DB;
- não migrar automaticamente DB v7 desconhecido;
- não alterar o runtime live nesta entrega;
- não considerar image ID ou tag mutável como identidade de promoção.

## Decisões arquiteturais

### 1. Release upstream `chapada-reservation-kernel==0.8.0`

A correção muda contratos, wire e schema. Ela não será disfarçada como patch da
wheel `0.7.0`. A nova wheel deve ser construída a partir de commit limpo,
reprodutível, revisada em 3 lanes e vendorizada em um **novo** runtime candidate.

A wheel `0.7.0` e o candidato `853e523...` permanecem evidência histórica; deixam
de ser source elegível para build da Fase 8.

### 2. Projection conversacional explícita no `BoundaryState`

`BoundaryState` precisa conter, além de workflows de reserva/handoff/pagamento,
uma projection tipada mínima para reconstruir o contexto Maya sem consultar o
legado como autoridade:

```text
ConversationProjection
  stage
  desired_services
  locale
  facts: tuple[TypedFact, ...]
  reservation_execution_projection
```

Regras:

- nenhum raw payload, texto de conversa, token ou metadata aberta;
- nomes de facts pertencem a catálogo fechado;
- o importer cria essa projection na gênese;
- após a gênese, o boundary store é a única autoridade de estado do turno;
- a projection de execução retém, quando existir workflow de reserva, a gênese,
  sequência exata de eventos Phase 5, summary outboxes e hashes necessários para
  full reducer replay no relay;
- uma sessão Hermes nunca é autoridade de estado e nunca é retomada entre
  tentativas. Cada tentativa usa sessão/home efêmeros construídos apenas da
  projection boundary; o ID da sessão é observabilidade privada da tentativa e
  não entra no próximo turno;
- uma tentativa órfã é apagada e jamais promovida como contexto canônico;
- legacy reader/importer só é alcançável quando `StateNotFound`.

O reader de gênese deixa de retornar `snapshot | None` ambíguo. Ele retorna um
union fechado `FOUND | PROVEN_ABSENT | UNAVAILABLE`:

- `FOUND`: importer valida/migra snapshot;
- `PROVEN_ABSENT`: cria empty genesis canônica para lead novo;
- `UNAVAILABLE`, timeout ou malformed: falha fechada, nunca cria lead vazio.

Essa distinção é obrigatória para não confundir indisponibilidade do legado com
novo contato.

### 3. Contrato `MayaTurnPort`, não `IntentPort` isolado

O contrato atual `IntentPort.interpret(...) -> ConversationIntent` é insuficiente:
não transporta reads, facts, command proposals, session ID nem resposta pública.
Ele será substituído no coordinator operacional por um port equivalente a:

```text
MayaTurnPort.run(MayaTurnRequest, ToolGatewayPort) -> MayaTurnProposal
```

`MayaTurnRequest` contém somente:

- estado boundary versionado;
- mensagem normalizada;
- event/lead binding;
- channel/delivery binding privado;
- deadline UTC;
- fingerprint do profile/config comportamental.

O filho **não** produz um `MayaTurnProposal` autoritativo. Ele produz apenas um
`MayaTurnClosure` fechado contendo:

- `aggregate_turn_id`;
- `MayaIntentClosure`, que contém somente kind, seleção/confirmação/handoff e
  **não possui facts, tool name ou command fields**;
- texto público final e route/reply type fechados;
- `final_seq` e MAC do transcript UDS que ele observou;
- ID efêmero da sessão apenas para observabilidade;
- marcador terminal de ausência de tools em voo.

O processo pai constrói o único `MayaTurnProposal` autoritativo combinando:

- closure validado do filho;
- `ReadObservation[]` acumuladas exclusivamente pelo gateway;
- `TypedFact[]` acumulados exclusivamente pelo gateway;
- `NormalizedToolProposal[]` acumuladas exclusivamente pelo gateway;
- chunks produzidos pelo splitter/guard do pai;
- hash/MAC final do transcript e runtime graph digest;
- route/reply type fechados.

Somente o pai converte `MayaIntentClosure` em `ConversationIntent`, anexando facts
e tool proposals do transcript. O tipo legado `ConversationIntent` vindo do filho
é rejeitado mesmo quando seus bytes parecem canônicos.

Maya escolhe linguagem, fluxo comercial, skills, perguntas e texto. Ela não cria
`ReservationCommand`/`PaymentSettlementCommand` diretamente, não pode anexar ao
closure artefatos que não passaram pelo gateway e não persiste estado. O stdout do
CLI não é resposta pública: após `chapada_finalize_turn`, ele deve conter somente
um marker protocolar fixo; divergência invalida o turno.

### 4. Resultados de read fechados

`ReadObservation` não pode carregar `dict` provider ou JSON arbitrário. O union
fechado deve reutilizar `reservation_lookup.LookupResult` para disponibilidade e
adicionar tipos mínimos equivalentes para FAQ e descrições.

Cada observação vincula:

- request tipado e `request_hash`;
- status fechado (`positive`, `negative`, `uncertain`, `rejected`);
- resultado tipado/sanitizado;
- `result_hash`;
- facts derivados;
- `safe_for_public_claims` mecanicamente derivado.

O payload mostrado à Maya é uma projection sanitizada desse tipo. Raw provider
payload nunca atravessa o canal nem entra no boundary DB.

### 5. Tool gateway por Unix domain socket autenticado por turno

#### Opções rejeitadas

- **JSONL temporário:** sem request/response transacional, tolera truncamento,
  mistura action log com commit e permite execução fora do coordinator.
- **HTTP localhost:** amplia superfície, exige porta/lifecycle e autenticação TCP,
  e cria mais um servidor de rede dentro do processo.
- **global/thread-local:** não atravessa o processo Hermes filho.

#### Opção escolhida

O processo pai cria um Unix domain socket por turno em diretório `0700`, socket
`0600`, e injeta no cliente plugin — não no prompt — um capability context
efêmero. O protocolo usa canonical JSON length-prefixed com schema fechado e uma
única conexão autenticada para todo o turno.

Binding obrigatório por request:

- protocol version;
- capability token aleatório por turno, nunca logado/persistido, usado como chave
  de HMAC-SHA-256 para a hash chain dos frames;
- `turn_id`, `lead_key`, aggregate event ID e sequência global monotônica;
- state version/hash;
- request ID e hash;
- deadline;
- tool name + arguments tipados.

No Linux, o servidor exige `SO_PEERCRED`, mesmo UID e PID pertencente ao process
group Hermes que o pai acabou de lançar. Ausência ou divergência não faz fallback.
O servidor valida também token/MAC, binding, sequência, budget, deadline e catálogo
antes de executar qualquer operação.

Cada response inclui sequência e MAC acumulado. Request ID repetido com bytes
idênticos retorna o response cacheado sem repetir read; request ID ou sequência
repetida com bytes divergentes aborta. O pai mantém uma única hash chain
autoritativa; o filho nunca fornece observations/facts/proposals fora dela.

HMAC é autenticação **live**, não provenance histórica. Em paralelo à chain
secreta, o pai constrói um `TranscriptCommitment` determinístico e privacy-safe:

- para cada frame, persiste somente direction, kind, sequence, request ID,
  canonical request hash, canonical response hash e previous frame commitment;
- capability token, HMAC key, prompt, raw message e raw provider payload nunca
  entram nesse commitment;
- o commitment terminal é recomputável depois de restart sem qualquer segredo;
- cada `ReadObservation`, `TypedFact`, `NormalizedToolProposal`,
  `LearningProposal` e `MayaTurnClosure` possui canonical bytes/hash e aponta para
  exatamente um frame commitment;
- `MayaTurnProposal` e `KernelDecision` também são persistidos em canonical bytes,
  com hashes recomputáveis e listas ordenadas dos artifacts de origem.

O HMAC prova ao pai que o peer live conhecia a capability; o commitment histórico
prova que os artifacts persistidos recompõem o transcript aceito. MAC opaco sem
essas rows não é evidence elegível.

Semântica por categoria:

- **READ:** o pai executa somente o read adapter, retorna projection sanitizada à
  Maya e acumula `ReadObservation`.
- **STATE_COMMIT:** nenhum write ocorre; o pai valida e acumula `TypedFact`.
- **LEARNING:** nenhum Hermes memory write ocorre; o pai valida uma
  `LearningProposal` sem PII/raw text e a acumula para internal outbox pós-commit.
- **COMMAND:** nenhum provider é chamado. Uma nova operação
  `ToolDispatch.normalize_proposal(...)` — owner único do catálogo, aliases e
  typed arguments — produz `NormalizedToolProposal`; ela não autoriza command.
- **tool bloqueada/unmigrada:** resposta fail-closed e manual-review quando
  aplicável.

O fechamento é uma tool protocolar `chapada_finalize_turn`. O frame `FINAL`
contém closure, `final_seq`, `expected_prefix_mac` que o filho recebeu no último
response e declara zero requests em voo. Esse campo é o MAC do prefixo anterior,
não do próprio FINAL. O pai aceita apenas se:

- sequência/MAC forem exatamente os do transcript pai;
- todos os requests tiverem response terminal;
- não houver segunda conexão ou frame tardio;
- o processo sair com stdout igual ao marker fixo de conclusão;
- closure/event/deadline estiverem vinculados ao turno.

Depois de validar o prefixo, o pai acrescenta os bytes canônicos do FINAL (com o
campo de prefixo em domínio separado) à chain e calcula `final_transcript_mac`, que
é o valor persistido no proposal/receipt. Assim não existe auto-hash circular.

Após `FINAL`, o socket fecha para novos frames. Socket EOF prematuro, request
duplicado divergente, sequência quebrada, peer inválido, deadline ou schema
inválido abortam o turno sem commit boundary. O diretório/socket/capability e a
sessão Hermes efêmera são apagados no `finally`.

`finally` é apenas a limpeza rápida. Cada tentativa vive sob
`${STATE_ROOT}/attempts/<random-128-bit-id>/`, criado com `0700`, `O_EXCL` e sem
symlinks. O pai mantém um `owner.lock` aberto com `flock` exclusivo durante toda a
tentativa; isso é a autoridade de liveness e não PID reutilizável. No startup e
periodicamente, um scavenger capability-free:

- abre o root por dirfd/no-follow e rejeita symlink, path escape, owner/mode ou
  marker inválido;
- nunca remove diretório cujo `owner.lock` não consegue adquirir;
- adquire o lock exclusivo de órfão antes de apagar somente o conteúdo fechado
  conhecido;
- diante de entry desconhecida/malformada, falha readiness em vez de apagar;
- prova limpeza após `SIGKILL`, `os._exit` e restart real.

Attempt roots jamais são pesquisados para retomar sessão ou estado canônico.
Power loss pode deixar bytes órfãos, mas o próximo startup não fica ready antes de
scavenge autenticado.

O processo filho recebe **um plugin novo e mínimo de boundary client**, além das
tools protocolares. Esse plugin contém somente schemas e cliente UDS. Terminal,
file, web, generic memory, cron e quaisquer plugins externos ficam ausentes.
Import/AST/module-graph gates proíbem transitivamente no processo filho:

- `ToolExecutor` e `chapada_native_tools` legacy;
- provider adapters/SDKs e constructors de Cloudbeds/Bókun/Wise/Stripe;
- ManyChat/senders/outboxes/delivery;
- file/memory writers ou alternative plugin entrypoints.

Nenhuma credencial comercial ou de delivery entra no env/home do filho; apenas a
credencial de transporte do modelo Maya, separada das capabilities comerciais.
Poison tests tornam qualquer import/call legado terminal. Profile/skills e um
snapshot das memories de
entrada são copiados com hashes autenticados para a tentativa; somente o session
store efêmero é gravável e todo o home é destruído no fim. Autoaprendizado continua
disponível somente pelo `LearningProposal`: ele entra na transação como internal
outbox e um worker idempotente o aplica à memória canônica depois do commit.
Tentativa abortada não altera memória; duplicate não cria segundo learning job.
O proposal/receipt registra versão/hash do snapshot de memória lido. A autoridade
de memória oferece `apply_learning(job_id, proposal_hash, expected_version,
expected_hash) -> LearningReceipt` e persiste **na mesma transação** da atualização:

- job/proposal identity;
- before version/hash;
- after version/hash;
- canonical receipt/hash.

Duplicate byte-idêntica retorna o mesmo receipt; job/proposal divergente é
identity conflict. Crash depois do target commit e antes do boundary ack é seguro:
retry consulta a autoridade pelo job ID, valida o receipt e faz ack com CAS completo
da lease. Conflito real não sobrescreve memória mais nova, fica `manual_review` e
não altera o receipt do turno. Memória dinâmica é estado versionado, não parte do
digest imutável do release.

### 6. Kernel puro continua owner único

`KernelPort` passa a reduzir o estado com o proposal completo, por contrato
conceitual equivalente a:

```text
KernelPort.reduce(BoundaryState, MayaTurnProposal) -> KernelDecision
```

O kernel:

- aplica facts à projection conversacional;
- reduz read evidence nos workflows canônicos;
- valida seleção/confirmação contra offer/version/signature;
- transforma somente proposals **normalizadas** em commands canônicos;
- produz `BoundaryInternalJob` fechado quando aplicável;
- nunca chama LLM, plugin, provider ou delivery.

`ToolDispatch` ganha duas operações explicitamente distintas:

1. `normalize_proposal`: catálogo/alias/arguments, antes do kernel, sem command;
2. `verify_authorized`: depois do reducer, prova vínculo exato entre proposal,
   estado/offer/version/evidence e command canônico.

O kernel permanece único owner de autorização/transição; ToolDispatch permanece
único owner de catálogo e normalização. Nenhum DTO de tool é montado manualmente
no plugin ou gateway.

A validação do coordinator rejeita:

- command sem proposal correspondente;
- read request não resolvida;
- facts não reduzidos;
- reply que não esteja vinculada ao proposal final;
- output com identity/version/event divergente.

### 7. Não manter transação SQLite durante Maya/provider reads

O coordinator atual abre `turn_transaction` antes do intent. O desenho novo evita
reter write transaction durante uma chamada de até dezenas de segundos:

1. validar envelope/deadline;
2. adquirir lock cross-process por lead/DB;
3. consultar duplicate;
4. se não existe boundary state, adquirir `LeadMigrationOwnership` no estado
   durável `FROZEN`, antes de ler o legado;
5. carregar estado e gênese/import **somente em memória**, sem persistir
   `boundary_state`, import claim ou fencing token;
6. executar Maya + reads ainda sob o lead lock, sem DB write transaction;
7. reduzir no kernel puro e validar proposal/decision/receipt;
8. abrir transação curta com busy timeout menor que o tempo restante;
9. depois de obter o writer lock, reamostrar deadline e revalidar
   event/source identities + state version/hash ou ausência da gênese; na gênese,
   revalidar também ownership token, source version e snapshot hash;
10. persistir gênese/import claim, CAS state/fence, event/sources, receipt,
   commands, relays e outboxes atomicamente;
11. reamostrar deadline imediatamente antes de `COMMIT`; deadline vencida causa
    rollback integral;
12. após commit, finalizar ownership como `BOUNDARY_OWNED` vinculado ao receipt;
13. liberar lock.

`LeadMigrationOwnershipPort` é uma autoridade separada que **todos** os ingress e
efeitos mutantes legacy/candidate precisam consultar. Estados fechados:

```text
LEGACY_OWNED → FROZEN → BOUNDARY_OWNED
                   ↘ RELEASED_TO_LEGACY
```

- `FROZEN` bloqueia writes/flush/callbacks legacy e nova entrega para o lead;
- contém owner token, source version/hash e nunca expira de volta ao legado
  automaticamente;
- crash antes do boundary commit deixa o lead congelado; reconciler só libera
  depois de provar ausência de boundary state/event/receipt;
- crash depois do boundary commit deixa o lead congelado; reconciler encontra o
  receipt e finaliza `BOUNDARY_OWNED`;
- source snapshot é relido sob o freeze antes do commit e qualquer mudança aborta;
- stale token/owner ou source hash divergente nunca cria gênese/import claim.

Não existe write **boundary/commercial** pré-Maya. O único write permitido é a
claim de ownership control-plane recuperável acima. Crash/timeout/EOF antes do
passo 10 deixa zero row change no boundary e zero efeito comercial. Retry usa uma
nova sessão efêmera porque a tentativa anterior não pode ser retomada nem se tornar
pública.

O runtime legacy atual não conhece essa autoridade. Portanto rollout gradual é
NO-GO até um compatibility guard separado, explicitamente autorizado, provar que
webhook, debounce/flush, Stripe, Wise, image/actions e todos os mutating callbacks
respeitam `FROZEN/BOUNDARY_OWNED`. Sem esse guard, a única alternativa elegível é
cutover global quiescente; mixed-mode por lead é proibido.

### 8. Lock cross-process deadline-aware

Implementação mínima Linux:

- arquivo derivado do boundary DB e lead hash, em root privado;
- `flock(LOCK_EX | LOCK_NB)`;
- polling limitado e clock injetável;
- revalidação imediatamente após adquirir;
- `finally` obrigatório;
- crash libera file descriptor;
- timeout ou deadline antes do commit deixa DB byte/row semanticamente
  inalterado, exceto artefatos SQLite puramente físicos aceitos pelo teste
  (`-wal`/`-shm`) sem mudança lógica.

O lock bloqueante atual ligado ao JSONL temporário não é reutilizado.

SQLite deve usar transações curtas, WAL/FK/integrity verificados e busy timeout
estritamente menor que o tempo restante. O clock é reamostrado após `BEGIN
IMMEDIATE`, antes do primeiro write e antes de `COMMIT`. Todos os writers de turno
usam o mesmo lock; worker writes não dependem dele, mas obedecem seus próprios
leases/fences e transações curtas.

### 9. Reply e receipt duráveis no mesmo commit

O splitter/guard roda antes do commit. O commit persiste os chunks exatos; o adapter
não cria mensagem nova depois que `coordinate` retorna.

Novo contrato mínimo:

```text
TurnReceipt
  aggregate_turn_id/event_hash
  ordered source event IDs/hashes
  Maya proposal + kernel decision hashes
  canonical sanitized read observations/hashes
  committed state/version/hash
  public chunk row IDs + exact bytes/hashes/order
  command IDs/hashes
  relay IDs/bundle hashes
  internal outbox IDs/hashes
  UDS transcript MAC/final_seq
  structural graph + capability policy + deployment binding digests
  behavior-state snapshot digest lido no turno
  committed_at
```

O receipt canônico é persistido com hash. Um duplicate com event hash idêntico:

- carrega o receipt;
- recompõe e valida o receipt contra as linhas relacionais vinculadas;
- não chama legacy reader, Maya, tool gateway, provider read ou kernel;
- retorna os mesmos IDs/chunks/hashes;
- não cria nova delivery job.

Mesmo aggregate/source event ID com hash diferente continua
`TurnEventConflict`.

### 10. Boundary schema v8

Universo mínimo proposto de dez tabelas:

1. `boundary_state`;
2. `boundary_events` — passa a armazenar `turn_receipt_json/hash`;
3. `boundary_event_sources` — IDs/hashes ordenados, com identidade única por
   lead/source event e FK ao aggregate turn;
4. `boundary_turn_artifacts` — commitments e canonical artifacts do transcript;
5. `boundary_commands`;
6. `boundary_command_relays`;
7. `boundary_outbox` — jobs internos fechados;
8. `boundary_public_outbox` — uma row por chamada externa/chunk;
9. `legacy_import_claims`;
10. `decision_comparisons`.

`boundary_turn_artifacts` usa PK `(lead_key, aggregate_turn_id, artifact_index)` e
kind fechado:

```text
frame_commitment | read_observation | typed_fact | normalized_tool_proposal |
learning_proposal | maya_closure | maya_proposal | kernel_decision
```

Cada row contém artifact ID, kind, index, optional frame sequence/ref, canonical
artifact JSON, `artifact_hash` e FK ao `boundary_events`. Frame commitment JSON
contém apenas metadata/hashes privacy-safe; proposal/decision/observation/fact
contêm os canonical sanitized bytes dos tipos fechados. Unique constraints impedem
artifact ID, kind/index e frame reference duplicados/divergentes.

`boundary_commands` e `boundary_public_outbox` são inseridas na mesma transação de
estado/event/receipt.

O receipt contém contagens e aggregate hashes, mas SQL mantém FKs explícitas de
event/source/artifact/command/relay/internal-outbox/public-chunk. `ON DELETE` não pode
ocultar dependentes. Startup/readiness, duplicate e cada claim executam scans
semânticos bidirecionais:

- cada ID/hash/count do receipt deve recompor exatamente as rows filhas;
- cada row filha pertence a exatamente um receipt;
- cada relay e row pública persiste `source_turn_receipt_hash`;
- transcript terminal, proposal e decision devem ser recompostos das artifact
  rows; `final_transcript_mac` isolado nunca satisfaz o scan;
- bytes canônicos no target UoW precisam reproduzir bundle/receipt de origem;
- row ausente, órfã, extra ou divergente é corrupção e bloqueia claim/readiness.

Para evitar hash circular, cada child row tem dois domínios distintos:

1. `artifact_hash`, calculado somente do payload imutável e **excluindo** backlink,
   lease/status/receipt de delivery;
2. `source_turn_receipt_hash`, metadata relacional preenchida com o receipt hash
   na mesma transação.

O receipt hash é calculado dos IDs + `artifact_hash` dos filhos; depois o mesmo
valor é gravado nos backlinks. O semantic scan exige igualdade, mas o backlink não
entra novamente no `artifact_hash`. Essa regra vale também no target UoW.

Não existe migração automática v7→v8. Startup:

- cria v8 somente em path novo/vazio;
- aceita apenas schema/hash exatos;
- falha diante de v7 ou universo inesperado;
- permite migração/descarte apenas por decisão offline autenticada.

A premissa atual é que não há DB v7 implantado. Encontrar um DB v7 é stop condition.

#### Evolução fechada dos UoWs alvo

O relay exige novas versões declarativas; não pode anexar colunas/tabelas ad hoc:

**Phase 5 execution: schema `5 → 6`.** O universo v6 contém as seis tabelas v5
inalteradas mais `reservation_boundary_ingress_receipts` com:

- `ingress_receipt_id` PK;
- `source_turn_receipt_hash` e UNIQUE `(source_turn_receipt_hash, command_id)`;
- `bundle_json`, `bundle_hash`;
- `command_id` UNIQUE e FK para `reservation_commands`;
- `target_receipt_json`, `target_receipt_hash`;
- `applied_at`.

O target receipt recompõe genesis, eventos contíguos, summary outboxes, workflow
final, command e ledger seed. `accept_boundary_reservation` insere tudo, inclusive
o ingress receipt, numa única transaction. Duplicate exige igualdade byte a byte
de bundle/command/target receipt; divergência é `IdentityConflict`.

**Phase 6 follow-up: schema `1 → 2`.** O universo v2 contém as onze tabelas v1
inalteradas mais:

1. `handoff_boundary_ingress_receipts`, vinculada por FK/UNIQUE a
   `handoff_workflows.handoff_id`;
2. `payment_boundary_ingress_receipts`, vinculada por FK/UNIQUE a
   `payment_commands.settlement_command_id`.

Ambas possuem `ingress_receipt_id` PK, `source_turn_receipt_hash`, bundle
JSON/hash, target subject ID, target receipt JSON/hash e `applied_at`; cada par
source receipt+target subject é único. `accept_boundary_handoff` e
`accept_boundary_settlement` persistem full replay + ingress receipt na mesma
transaction e retornam duplicate exata de forma byte-idêntica.

Não haverá migration automática desses UoWs na `0.8.0`. Phase 8 exige roots novos
e vazios para schemas Phase5-v6 e Phase6-v2. Encontrar schema Phase5-v5,
Phase6-v1, migration history extra ou table universe inesperado no root escolhido
falha startup e é stop condition; migração offline futura exige design/validator e
autorização separados. Startup, readiness, duplicate e claim executam full replay
e semantic scan dos ingress receipts contra todas as rows alvo.

### 11. Relay durável para as Fases 5/6

#### Opção rejeitada: UoW consolidado

Fases 5 e 6 possuem schemas, migrations, reducers, ledgers, outboxes e recovery
próprios. Fundir tudo no boundary DB exigiria reabrir invariantes já aprovadas e
criaria ownership duplicado.

#### Opção escolhida: claim/relay/ack idempotente

Cada `boundary_command` recebe uma relay job 1:1 com um bundle canônico fechado.
Para reserva, `ReservationRelayBundle` contém:

- estado Phase 5 de gênese em revisão zero;
- sequência exata e contígua de eventos Phase 5;
- todos os summary outboxes necessários ao full reducer replay;
- estado final esperado e hash;
- command/ledger seed canônicos;
- `artifact_hash` independente do backlink.

A relay row, fora do bundle hash, carrega o `source_turn_receipt_hash`.

Essa história nasce e evolui na `reservation_execution_projection` do boundary;
hashes isolados não são aceitos como reconstrução. Para settlement, o bundle
Phase 6 carrega anchor/policy/history/evidence/command e estado final, mesmo quando
o `PaymentWorkflow.history` já contém parte dessas informações.

`boundary_command_relays` usa a máquina fechada
`pending|leased|acked|manual_review` e persiste owner, fencing token,
lease-acquired/expires, claim count, preparation failures (máximo 3), optional
target receipt JSON/hash, acked-at e updated-at. Invariantes:

- `fencing_token == claim_count` e incrementa em todo claim/reclaim;
- expiry é exatamente `expires_at <= now`;
- claim/release/reclaim/ack fazem CAS do tuple completo
  `(status, owner, token, acquired_at, expires_at, counts, updated_at)`;
- completion/ack stale é sempre rejeitado;
- target receipt só existe em `acked`, vinculado ao source receipt e bundle;
- nenhuma transição de preparation consome provider dispatch slot.

O `BoundaryCommandRelayWorker` é one-shot e:

1. claim com lease/fencing token;
2. carrega command, bundle e source receipt canônicos;
3. prepara/valida sem chamar provider;
4. chama um novo ingress idempotente no UoW alvo;
5. força full replay no UoW alvo e valida receipt por command/bundle/source receipt
   hashes;
6. ack no boundary DB por full-tuple CAS.

Morte/falha antes do target call libera/requeue a claim; após três preparation
failures vira `manual_review`. Morte/exception durante/depois do target call pode
ter commit alvo: a lease expira, retry chama o mesmo ingresso com os mesmos bytes,
recebe o mesmo receipt e faz ack. Divergência target ou budget esgotado vai para
manual review, nunca para provider. Morte após target receipt e antes do source ack
é coberta pelo mesmo replay idempotente.

Os UoWs precisam de ingress explícitos equivalentes a:

```text
SQLiteUnitOfWork.accept_boundary_reservation(...)
SQLiteFollowupUnitOfWork.accept_boundary_settlement(...)
```

Eles criam genesis, eventos, summary outboxes, workflow final, ledger e command de
forma atômica; aceitam duplicate byte-idêntica e rejeitam identidade divergente.
Inserção direta de estado avançado é proibida. Não chamam provider.

Crash após commit alvo e antes do ack é seguro: retry recebe o mesmo receipt do UoW
e então faz ack. Provider workers só podem claimar o target UoW após esse ingress.

No dark canary, relay pode ser autorizado contra DBs isolados para provar
reachability, enquanto o graph constrói os provider workers mas a policy nega
claim/fence/dispatch.

`boundary_outbox` v8 não reutiliza mais
`reservation_execution.OutboxMessage`. Ela armazena um union fechado
`BoundaryInternalJob`, inicialmente:

- `HandoffRelayBundle`, com request/policy/history/expected hash e artifact hash
  independente do backlink; a row carrega `source_turn_receipt_hash`;
- `LearningProposal`, sem PII/raw text e vinculada ao receipt.

O follow-up UoW ganha ainda
`SQLiteFollowupUnitOfWork.accept_boundary_handoff(...)`, com a mesma semântica de
full replay/duplicate exata/divergência. O handoff delivery worker só enxerga o
job após o ingresso idempotente no UoW da Fase 6. O learning worker aplica a
memória canônica pelo target receipt atômico descrito acima. Nenhum deles
chama efeito no turno.

`BoundaryInternalJobWorker` é o owner production-reachable de
`boundary_outbox`. Ele é construído/supervisionado pela factory, exposto em
readiness e usa a **mesma** máquina lease/CAS/budget do command relay. Ports alvo
fechados:

- `HandoffIngressPort.accept(bundle) -> HandoffIngressReceipt`;
- `LearningAuthorityPort.apply_learning(...) -> LearningReceipt`.

Handoff segue target-commit/source-ack idempotente. Learning faz memory+receipt
atômicos no target e source ack depois. Expired lease é reclaim pre-target;
stale ack é rejeitado; target receipt divergente ou três failures termina em
manual review. Crash tests cobrem antes/depois de target commit e source ack para
ambos os variants.

### 12. Delivery pública com fence próprio

Não usar `JsonPublicMessageOutbox` como autoridade da rota Phase 8.
`boundary_public_outbox` mantém **uma row por chunk e por chamada ManyChat**:

- target/channel binding privado;
- bytes/hash exatos, índice total e predecessor do chunk;
- idempotency key derivada de aggregate turn ID + chunk index + artifact hash,
  nunca do receipt hash;
- status `pending|leased|dispatch_fenced|delivered|manual_review`;
- owner/token/lease/claim-count, preparation-failures e dispatch-slots-consumed;
- source turn receipt backlink, delivery receipt hash e timestamps.

Máquina fechada:

- `pending`: sem lease, slot 0;
- `leased`: lease completa, slot 0;
- `dispatch_fenced`: lease completa, slot exatamente 1;
- `delivered`: sem lease, slot 1, delivery receipt presente;
- `manual_review`: sem lease; slot 0 para preparation terminal ou 1 para resultado
  pós-fence desconhecido.

`fencing_token == claim_count`; expiry é `expires_at <= now`; todas as mutações
fazem full-tuple CAS e stale completion é rejeitada. Claim escolhe somente o menor
chunk não terminal cujo predecessor está `delivered`. Lease `leased` expirada é
reclaimable porque slot=0 prova zero send. Preparation failure libera para
`pending` e incrementa failure count; ao máximo 3 termina `manual_review` com
slot=0. Somente `fence_dispatch` consome permanentemente o único slot.

`PublicDeliveryWorker.run_once()` processa no máximo uma row, um chunk e uma
chamada ManyChat:

1. claim;
2. prepara request sem side effect;
3. consome permanentemente um único dispatch slot (`dispatch_fenced`);
4. chama ManyChat somente se role/capability/allowlist permitem;
5. grava receipt de exatamente uma chamada externa.

Falha/crash depois do fence e antes de receipt deixa a row representavelmente
`dispatch_fenced`. Um **reconciler sem capability de send** varre leases vencidas
e promove atomicamente para `manual_review`; successors permanecem bloqueados.
Não ocorre reenvio automático. Prefixo parcialmente entregue fica explícito por
chunks anteriores `delivered`; o chunk incerto e os posteriores nunca são enviados
automaticamente. Corridas worker/reconciler, close/reopen e restart precisam de
prova.

O reconciler recebe estruturalmente somente store+clock; não recebe ManyChat,
credentials ou send port. Ele distingue `leased` expirada (reclaim pre-fence) de
`dispatch_fenced` expirada (manual-review pós-fence). Testes cobrem morte após
claim, durante/depois de prepare, antes/depois do fence, depois do send e antes do
receipt, stale worker versus reconciler e budgets terminais.

Dark canary e primeiro ingress gate constroem e supervisionam esse worker, mas a
policy nega claim/fence/send; os gates provam zero dispatch slot e zero chamada.

### 13. Composition root canônica

Substituir import-time global por uma única factory pública:

```text
uvicorn chapada_leads.runtime:create_app --factory
```

A factory constrói e autentica:

- settings e role;
- paths/state roots exclusivos;
- boundary store probe;
- lock factory;
- migration ownership port/reconciler + legacy reader/importer;
- Maya turn port + UDS tool gateway;
- attempt-root scavenger;
- kernel adapter;
- command relay e boundary-internal-job workers;
- public delivery worker + capability-free reconciler;
- memory authority/learning target;
- coordinator + runtime adapter;
- routes e lifespan.

Canary e produção usam a mesma factory/classes e constroem todos os workers. Um
`RuntimeGraphManifest` canônico autentica classes/versões, wheel, profile/config,
skills, plugin, ToolDispatch catalog, provider adapters e workers. Seu digest é
persistido no receipt e exposto em readiness.

O graph inclui explicitamente ownership reconciler, attempt scavenger,
`BoundaryCommandRelayWorker`, `BoundaryInternalJobWorker`,
`PublicDeliveryWorker`, public reconciler e learning authority. O import graph do
plugin filho mínimo faz parte do manifest. O semantic scan de readiness recompõe
transcript commitments, canonical proposal/decision, target ingress receipts e
todos os source acknowledgements.

Memória aprendida não contamina esse digest estrutural. Um
`BehaviorStateSnapshot` canônico contém schema/version/hash da memória dinâmica;
ele é validado no startup, vinculado ao `DeploymentBinding` inicial e persistido
por turno. Canary recebe clone autenticado e isolado do snapshot escolhido, nunca
mount RW da memória de produção.

Cada estágio possui ainda uma `CapabilityPolicy` canônica e hash, contendo a
matriz de capabilities, worker modes e guard semantics, mas **não** roots,
allowlist concreta ou percentual. Esses valores ficam em um `DeploymentBinding`
separado, também hashado e persistido para auditoria.

`DeploymentBinding` tem schema fechado:

```text
release_child_manifest_digest
runtime_graph_digest
capability_policy_digest
behavior_state_snapshot_digest
behavior_transition_receipt_hash | null
runtime_role
provider_scope
workflow_scope
effect_scope
allowlist_digest + allowlist_cardinality
traffic_stage
state_root_class + instance_id
```

Provider/workflow/effect scopes são enums/IDs canônicos, nunca texto livre. Roots
são classificados `ephemeral_canary|persistent_production` e validados contra
mounts reais; paths não entram em hashes de comportamento.

Dark/ingress fechado exercitam o graph completo com capabilities negadas, não
omitem classes.
O último canary E2E que autoriza rollout usa exatamente o mesmo graph e
capability-policy da promoção. Depois do último turno E2E:

1. drenar todos os learning jobs canary;
2. construir `BehaviorTransitionReceipt` que liga, em ordem, receipts de turno,
   snapshots lidos e `LearningReceipt`s ao snapshot final;
3. selar o `BehaviorStateSnapshot` final;
4. bloquear novos ingress/learning até a decisão de rollout;
5. inicializar produção byte-idêntica nesse snapshot.

O binding promovido aponta ao snapshot final e ao transition receipt. Qualquer
alteração de memória depois do seal invalida E2E. Se não houve learning, o
transition receipt ainda prova identidade entre snapshot inicial/final.

A transformação canary-E2E → produção inicial é um oráculo fechado. Exige
igualdade de release, graph, capability policy, behavior snapshot,
behavior-transition receipt, provider scope, workflow scope e effect scope. Só
permite:

- role `canary_e2e → production_initial` sem alterar branches do graph;
- root class `ephemeral_canary → persistent_production`;
- instance ID;
- allowlist digest/cardinality e traffic stage exatamente fixados na autorização
  de rollout gradual.

Qualquer outra diferença — memória, provider/workflow/effect, capability, guard,
modelo/profile ou worker mode — exige nova canary E2E. A allowlist/traffic target é
input fechado do gate, não “transformação declarada” livre.

Fora da transformação E2E→produção acima, nenhum campo pode variar entre o
binding certificado e o promovido. Paths privados concretos podem diferir apenas
quando o campo fechado `state_root_class` autoriza e o mount preflight prova a
classe esperada; eles não alteram bytes ou comportamento.

Nenhuma factory alternativa, global `app`, `LegacyRegressionTurnAdapter` ou helper
legado pode estar alcançável pelo Docker target.

### 14. Ingress universe

Turnos concluídos obrigatórios pelo coordinator:

1. webhook ManyChat imediato;
2. flush-ready HTTP;
3. flush-contact;
4. auto-flush.

`TurnEnvelope` carrega `aggregate_turn_id` e uma lista ordenada de
`SourceEventIdentity(source_event_id, source_event_hash)`. O aggregate ID/hash é
derivado deterministicamente desses itens e da mensagem normalizada.

Early idempotency/debounce pode apenas bufferizar. Para responder duplicate antes
de executar Maya, precisa consultar o receipt boundary e comparar todos os hashes;
divergência sempre entra no caminho autoritativo de `TurnEventConflict`. Cache sem
consulta ao receipt nunca marca evento processado nem produz reply. Flush-ready,
flush-contact e auto-flush persistem todas as source identities, não apenas um
event ID sintético.

Ingress não conversacionais:

- Stripe/Wise entram por boundary dedicado de payment evidence/follow-up;
- public/image/form/flow sends entram por workers/outbox;
- qualquer route ainda ligada diretamente a `ToolExecutor` ou sender fica
  desabilitada por capability e bloqueia promoção.

### 15. Boot, readiness e shutdown

Startup falha antes do `lifespan yield` quando houver:

- role/instance/state root ausente ou compartilhado;
- canary apontando para root de produção;
- boundary v8, Phase5-v6 ou Phase6-v2 schema/hash/table universe/WAL/FK/integrity
  inválido;
- lock dir/socket dir indisponível;
- attempt root malformado, symlink/path escape ou orphan não scavenged;
- qualquer port obrigatória ausente;
- outbox não durável para uma role que permite delivery;
- worker/capability incoerente;
- provider write habilitado sem worker boundary correspondente;
- helper/route legado alcançável no graph promovível;
- plugin filho alcança import/capability proibida;
- structural graph/profile/config/catalog digest diferente do release manifest;
- semantic scan receipt↔sources↔transcript/artifacts↔commands↔relays↔target
  receipts↔outboxes divergente;
- behavior snapshot/binding/transition receipt não satisfaz o stage atual;
- reconciler obrigatório ausente ou morto.

Semântica:

- `/health/live`: processo/event loop vivo;
- `/health/ready`: 503 até graph, DB, lock e workers da role estarem prontos;
- worker obrigatório que morre derruba readiness ou encerra o processo;
- shutdown torna readiness false, drena/cancela workers com prazo, fecha sockets e
  recursos;
- Docker healthcheck de canary/promoção usa readiness, não apenas liveness.

## Identidade de release corrigida

### Autoridade OCI executável

Plataforma única e obrigatória: **`linux/arm64`**. O build publica em um registry
OCI local, restrito a loopback e operado exclusivamente pelo release controller.
Delete, tag overwrite e garbage collection ficam proibidos enquanto a release ou
rollback forem elegíveis; um lock de release serializa writers e o controller
revalida os digests após cada operação.

O build gera e registra:

- digest do OCI index retornado pelo registry;
- descriptors e media types do index;
- exatamente um child image manifest para `linux/arm64` e zero descriptor de
  attestation/plataforma extra; qualquer outro universo falha;
- **child manifest digest** `sha256:...`, que é a autoridade de execução;
- config digest/image ID, ordered layer digests e archive/layout hash como
  evidência secundária.

Canary, promoção e rollback materializam somente uma referência imutável:

```text
127.0.0.1:<registry>/chapada-leads@sha256:<arm64-child-manifest>
```

O controller faz pull/create por essa referência, consulta o manifest no registry,
verifica media type, plataforma, config/layers e prova em `docker inspect` que o
container efetivo usa o config digest ligado ao child manifest. Tag mutável, image
ID isolado, index sem child pinado ou archive hash isolado não autorizam execução.

Antes de qualquer rollout, a imagem live anterior também é publicada no registry
local sem rebuild. O child manifest de rollback só é aceito se seu config digest e
RootFS reproduzirem exatamente o image ID/layers do container live autenticado.
Rollback usa `repo@child-manifest-digest`, nunca tag ou rebuild.

### Cadeia source→container

Antes do build, o contexto contém `source-attestation.json` canônico com
upstream/wheel/runtime commit/tree, build-context paths/hashes e graph/profile
hashes. Seu hash é baked em label e arquivo da imagem.

Depois da publicação, um `release-manifest.json` externo, imutável e montado
read-only no container, vincula:

```text
upstream commit/tree
→ wheel 0.8.0 hash/bytes
→ runtime candidate commit/tree limpo
→ build-context manifest/hash
→ OCI index digest
→ linux/arm64 child manifest digest
→ config/layers
→ container efetivo
```

Não se tenta incorporar o digest OCI da própria imagem dentro dela. O controller
verifica manifest→config/layers→container; o startup verifica que a source
attestation baked, release manifest montado, expected child digest injetado,
runtime graph e capability/deployment hashes concordam. Qualquer lado ausente ou
divergente falha readiness. O hash do release manifest externo é igual em todas as
instâncias da mesma release; somente o `DeploymentBinding` hash-autenticado e
aprovado pelo gate é específico da instância.

SOUL, HERMES, profile, skills, plugin, config não secreto, ToolDispatch catalog,
modelo/provider/reasoning e Hermes version ficam dentro da imagem ou têm hashes
exatos no release manifest e são verificados fail-closed no startup. O
`RuntimeGraphManifest` e a `CapabilityPolicy` também são vinculados à cadeia e aos
receipts. Segredos são referenciados somente por nomes de slots/capabilities, nunca
por valor ou hash reversível.

Esta decisão substitui os trechos anteriores da spec/plano Phase 8 que tratavam
image ID + archive como identidade primária. Spec, plano, ADR/evidência e scripts
principais precisam ser corrigidos e aprovados antes do build.

## Alternatives consideradas

### Adapter somente no runtime

Rejeitado. Não resolve reply pós-commit, duplicate sem bytes, command relay, schema,
sessão efêmera de tentativa ou plugin filho fora da autoridade.

### Entrypoint canary separado

Rejeitado como composition root. Poderia esconder o problema até o rollout e criar
graph distinto. Um launcher fino só é aceitável se delegar à mesma factory.

### HTTP local em vez de UDS

Rejeitado para a primeira implementação pela superfície e lifecycle adicionais.
Pode ser reconsiderado somente se houver requisito de plataforma não POSIX.

### Executar tools no próprio processo Hermes

Rejeitado para Phase 8. Mistura provider/commit com o cérebro e impede a autoridade
transacional do processo pai.

### Copiar commands/public reply best-effort após commit

Rejeitado. Crash cria perda ou divergência. Toda saída deve nascer em durable job
na transação do turno.

## TDD slices

Cada slice exige RED causal, GREEN focado, blast radius pelo módulo e revisão
antes do próximo. Não executar suíte integral repetidamente.

Envelope de evidência obrigatório por RED/GREEN:

- base commit/tree e status;
- comando exato, exit code, duração e contagens;
- SHA-256 e bytes do raw output; somente o raw fica em `/tmp`;
- conclusão causal sanitizada versionada.

Reviews são AND gates no mesmo commit/tree/package. `Needs fixes`, timeout ou
summary ausente valem zero. Qualquer correção material invalida todas as
aprovações do pacote e exige nova rodada completa das lanes.

### Slice 0 — Contract lock

- testes de estrutura para novos types/ports;
- RED prova que v0.7.0 não contém projection, proposal, receipt e relay;
- nenhum runtime change.

### Slice 1 — Types e wire v2

- `ConversationProjection`;
- read-result union;
- source event identities, `MayaTurnRequest/Closure/Proposal`;
- normalized tool/learning proposals, transcript binding e graph/policy/binding
  digests;
- public message/receipt/relay types;
- genesis lookup tri-state e `BoundaryInternalJob` handoff/learning;
- exact-type, canonical serialization, unknown-field e mutation tests.

### Slice 2 — ToolDispatch proposal contract

- `normalize_proposal` sem autorização/command/provider;
- `verify_authorized` após kernel;
- catálogo/alias/typed arguments com owner único;
- matriz read/state/command/bloqueado e mutations.

### Slice 3 — Schema/store v8

- dez boundary tables exatas, incluindo turn artifacts, e FKs bidirecionais;
- Phase5 schema v6 com `reservation_boundary_ingress_receipts`;
- Phase6 schema v2 com handoff/payment boundary ingress receipts;
- roots novos obrigatórios; schemas antigos/universos extras fail-closed;
- receipt/public/relay atômicos com fault injection entre todos os writes;
- v7/universo divergente fail-closed;
- zero row change em deadline/CAS/fence/genesis failure;
- semantic scans receipt↔artifacts↔rows e source/target receipt hashes.

### Slice 4 — Lock e transações curtas

- multiprocess flock;
- B expira sem mudança enquanto A segura lock;
- C sucede após release;
- nenhum write transaction aberto durante fake Maya lento;
- clock após writer lock/antes do primeiro write/antes do commit;
- SQLite busy timeout respeita deadline e faz rollback lógico integral.

### Slice 5 — UDS protocol

- token/HMAC/hash chain/binding/sequence/schema/deadline;
- socket permissions, conexão única e `SO_PEERCRED`/process group;
- duplicate request exata sem read versus divergente;
- FINAL/final_seq/MAC/no-inflight/stdout marker/late frame;
- commitments históricos recomputáveis depois de restart sem HMAC key;
- canonical closure/proposal/decision bytes e vínculo frame↔artifact;
- EOF/crash/truncamento/segunda conexão;
- READ/STATE/LEARNING/COMMAND nunca executam provider ou memory write.

### Slice 6 — Maya adapter

- subprocesso fake exercita plugin pelo UDS;
- tool result retorna à Maya;
- somente closure retorna do filho; proposal é construído do transcript pai;
- sessão/home efêmeros por tentativa e nenhuma retomada de órfã;
- plugin filho mínimo; scan transitivo proíbe ToolExecutor, legacy plugin,
  providers, sender/delivery e memory/file writers;
- env filho contém model transport, nunca credencial comercial;
- attempt owner-lock/scavenger com SIGKILL/os._exit/restart, no-follow e malformed
  root fail-closed;
- JSONL/global/thread-local proibidos por AST/import gate;
- guards conversacionais preservados.

### Slice 7 — Kernel adapter

- proposals viram estado/commands canônicos;
- owner único de reducer/ToolDispatch;
- package/confirmation/payment bindings;
- command não autorizado e claim sem evidence falham.

### Slice 8 — Coordinator, gênese e commit atômico

- primeiro evento `StateNotFound → FOUND/importer | PROVEN_ABSENT/empty genesis`
  somente em memória;
- `UNAVAILABLE`/timeout do legado nunca vira empty genesis;
- ownership `LEGACY_OWNED→FROZEN→BOUNDARY_OWNED` e reconciler de crash;
- legacy snapshot A alterado para B durante Maya aborta sem gênese/import claim;
- nenhum write/flush/callback legacy passa enquanto `FROZEN`;
- gênese/import claim persistidos apenas no commit final;
- legacy reader inalcançável após gênese;
- ordem lock→snapshot→Maya/read sem transaction→kernel→CAS/commit;
- validação bilateral transcript/proposal/decision/reply/receipt;
- source-event aggregate e conflito hash;
- fault após cada artefato produz rollback integral;
- crash após commit/antes de delivery preserva receipt.

### Slice 9 — Duplicate replay e integridade

- duplicate retorna mesmos chunks/IDs/hashes;
- contadores legacy/Maya/read/kernel permanecem zero;
- aggregate ou source event ID divergente falha;
- estado posterior não altera receipt histórico;
- rows ausentes/extras/órfãs bloqueiam duplicate, claim e readiness.

### Slice 10 — Command relay

- bundle Phase 5 contém genesis/eventos/summary outboxes/full replay;
- bundle Phase 6 e source receipt hash explícitos;
- handoff bundle/internal job entra idempotentemente no UoW Phase 6;
- learning target atualiza memória+LearningReceipt na mesma transaction e source
  ack é crash-safe;
- accept idempotente e atômico nos UoWs 5/6;
- command/internal relay machines: exact expiry, full-tuple CAS, pre-target reclaim,
  max 3 failures, stale ack rejection e target-receipt divergence;
- crash target-commit/boundary-ack;
- duplicate exata e divergente;
- relay não chama provider;
- policy fechada deixa provider workers sem claim/dispatch.

### Slice 11 — Public delivery ledger e reconciler

- uma row/fence/receipt por chunk/chamada externa e ordering por predecessor;
- leased pre-fence exact expiry/reclaim, preparation release/budget e stale CAS;
- crash pós-fence fica representado e reconciler capability-free promove manual
  review sem segundo send;
- prefixo parcial/successors bloqueados;
- allowlist/role/capability;
- dark mode produz zero dispatch slot.

### Slice 12 — Composition root/readiness

- target exato do Docker com `--factory`;
- graph completo sem `None`;
- graph/capability digests verificados e persistidos;
- ownership/internal/relay/public/learning workers e reconcilers supervisionados;
- historical transcript/target-ingress semantic scan em readiness;
- memory-learning target receipt atômico e somente pós-commit;
- roots canary/prod distintos;
- boot failure matrix;
- worker/reconciler death e shutdown.

### Slice 13 — Ingress universe/legacy poison

- quatro ingress de turno parametrizados;
- source identities e exatamente um aggregate receipt por caso;
- cache/debounce nunca oculta conflito;
- compatibility guard prova que todos os ingress/effects legacy respeitam
  migration ownership; caso contrário mixed-mode é poisonado;
- legacy helper/QA adapter não importável pelo pacote produtivo;
- Stripe/Wise/actions diretas desabilitadas ou migradas.

### Slice 14 — Upstream terminal verification

- properties/faults/restarts/contention/mutations afetadas;
- suíte integral upstream única;
- validator terminal e revisão funcional no mesmo commit/tree;
- somente depois congelar o commit upstream elegível.

### Slice 15 — Wheel 0.8.0

- construir nova wheel 0.8.0;
- RECORD/metadata/wire/schema/hash/bytes autenticados;
- package review 3/3 no mesmo wheel e upstream commit/tree.

### Slice 16 — Runtime candidate e wiring

- criar novo runtime candidate limpo;
- incorporar wheel e composition root sem delta estranho;
- startup/lifespan real, health ready e ingress local;
- testes focados e blast radius runtime.

### Slice 17 — Runtime terminal verification

- suíte integral runtime única para o candidato final;
- startup/restart/crash/worker readiness;
- revisão funcional/security/packaging 3/3 no mesmo commit/tree/wheel;
- source/runtime live fingerprints reautenticados.

### Slice 18 — Release contract executável

- corrigir spec/plano/ADR/evidence Phase 8 para child manifest `linux/arm64`;
- registry local immutable-policy, index/child/config/layers e rollback import;
- chain source→wheel→runtime→OCI→container;
- graph/profile/config/policy hashes;
- preflight e reviewers 3/3.

Somente após Slices 14–18 verdes há uma decisão explícita **GO/NO-GO de build**.
O build OCI não faz parte da aprovação implícita de nenhum slice anterior.

## Stop conditions

Qualquer item abaixo mantém build/rollout em NO-GO:

- design não aprovado;
- timeout de auditor/reviewer sem summary;
- DB v7 real descoberto;
- reply ainda produzida/enfileirada pós-commit;
- filho consegue injetar observation/fact/proposal fora do transcript pai;
- sessão Hermes de tentativa pode ser retomada após falha;
- duplicate chama Maya/read/kernel;
- cache/debounce oculta source-event conflict;
- provider write alcançável no turno;
- command relay sem bundle full-replay/source receipt ou best-effort;
- receipt/row integrity não é bidirecional;
- public send sem fence/receipt por chamada ou reconciler;
- UDS sem HMAC/peer/final transcript binding;
- transcript/proposal/decision não recomputável após restart;
- plugin filho ainda alcança ToolExecutor/provider/delivery/memory writer;
- attempt orphan não tem owner-lock/scavenger fail-closed;
- transaction aberta durante LLM/read remoto;
- write boundary persistido antes de Maya;
- snapshot legacy não está congelado/revalidado até commit;
- UoW Phase5/6 target não está no schema novo exato/root novo;
- relay/internal/public lease machine não fecha pre-target/pre-fence CAS e expiry;
- memory apply e `LearningReceipt` não são atômicos;
- `create_app` aceita adapter obrigatório `None`;
- factory/graph ou capability policy do E2E diferente da promoção;
- behavior snapshot/transition receipt ou provider/workflow/effect scope diverge
  entre último E2E e produção inicial;
- `DeploymentBinding` muda fora do oráculo fechado aprovado;
- qualquer ingress mutante bypassa seu boundary;
- mixed-mode iniciado antes do compatibility guard de migration ownership;
- promoção/rollback não fixados ao child manifest digest `linux/arm64`;
- Slice 14–18 ou review AND gate incompleto;
- runtime operacional alterado antes da autorização correspondente.

## Gates de aprovação

1. **Design:** Carlos aprova esta arquitetura; ainda sem código.
2. **Plano:** plano TDD detalhado e revisado; ainda sem build.
3. **Upstream terminal closeout:** Slice 14 verde no commit exato.
4. **Wheel:** 0.8.0 autenticada e package review 3/3.
5. **Runtime wiring terminal:** candidata nova, Slice 17 e review 3/3.
6. **Release contract / GO de build:** Slice 18, source/runtime live reautenticados
   e decisão explícita; nenhuma etapa anterior implica build.
7. **Build:** uma única publicação OCI; index e child manifest arm64 autenticados.
8. **Dark canary:** reads reais; graph completo; zero provider write/delivery.
9. **Ingress fechado:** rota/allowlist restritas, outbound fechado, estado limpo.
10. **Conversation readiness:** mesma imagem/digest; allowlist efetiva com
    cardinalidade exatamente um; a única capability de **efeito externo** aberta é
    public delivery (reads permanecem read-only); learning pode operar apenas na
    memória canary isolada;
    provider/command-relay/payment/handoff effects mecanicamente fechados;
    state/session/outboxes canary limpos; memory baseline autenticada e isolada;
    zero pendência antiga; readiness verde e revisão aprovada.
11. **Teste humano:** somente agora Carlos é avisado e executa as conversas.
12. **Migration ownership readiness:** antes de qualquer E2E/mixed-mode sobre lead
    legacy, compatibility guard e reconciler autenticam todos os ingress/efeitos;
    alternativa é cutover global quiescente explicitamente escolhido.
13. **Canary E2E:** autorização separada para provider/workflow/período e policy
    exatos; drena learning, sela behavior snapshot/transition receipt e bloqueia
    novos ingress; policy/scopes aprovados são idênticos ao rollout inicial.
14. **Rollout:** decisão separada, gradual, mesmo child manifest, policy, behavior
    snapshot/transition receipt e scopes; binding muda só pelo oráculo aprovado.
15. **Closeout Phase 8:** decisão posterior e separada, com snapshot terminal,
    review 3/3 no mesmo SHA/tree, CI remoto exato, manifests/riscos atualizados,
    rollback por digest preservado e `phase9_started=false`.

Até o Gate 10 completo, não é momento de avisar Carlos para conversar com o
agente. Rollout não implica closeout, e closeout não autoriza a Fase 9.
