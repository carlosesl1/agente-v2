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

`finally` é apenas a limpeza rápida. Criação e scavenge usam um protocolo de
publicação, sem janela `mkdir→owner.lock`. O root privado contém
`coord.lock`, `.staging/` e `active/`; creators e scavenger precisam adquirir
`coord.lock` exclusivamente por dirfd/no-follow. O creator, ainda sob esse lock:

1. cria `.staging/<random-128-bit-id>` com `0700` e fail-if-exists;
2. cria `owner.lock` por `openat(O_CREAT|O_EXCL|O_NOFOLLOW, 0600)`, abre e adquire
   `flock` exclusivo;
3. grava todos os markers fechados por `openat`, faz `fsync` de files/dir;
4. publica somente a tentativa completa por
   `renameat2(RENAME_NOREPLACE, .staging/id, active/id)` e faz `fsync` dos dirs;
5. libera `coord.lock`, mas mantém o fd/lock de owner até o fim da tentativa.

O staging protocol usa **um único marker canônico** `attempt.meta`. Durante criação,
o universo permitido é uma destas gramáticas de prefixo, em ordem:

```text
S0 = {}                                  # mkdir concluído
S1 = {owner.lock}                        # lock file criado
S2 = {owner.lock, attempt.meta.tmp}      # write possivelmente parcial
S3 = {owner.lock, attempt.meta}          # metadata publicada e válida
```

`attempt.meta.tmp` nunca é interpretado; por estar em staging não publicado, pode
ser removido mesmo parcial. O creator publica `attempt.meta` por temp+fsync+
rename-no-replace+dir-fsync e remove qualquer temp antes do rename do diretório.
`active/id` exige `owner.lock` + `attempt.meta` válidos. O metadata enumera um set
fechado de runtime member names; enquanto vivo, o owner pode materializar/remover
esses members. Depois de crash, o conjunto observado pode ser qualquer **subconjunto
desse allowlist** — inclusive socket ainda ausente ou temp parcial explicitamente
nomeado — e continua sendo órfão removível. Member desconhecido, symlink ou nome
fora do allowlist falha readiness. Assim, crash logo após publish não vira falso
`malformed`, mas conteúdo inesperado nunca é apagado por adivinhação.

O scavenger capability-free também segura `coord.lock` durante scan/remoção. Como
nenhum creator solta esse lock antes do publish, qualquer staging entry observada
sob o lock foi deixada por crash. O scavenger:

- abre roots/entries por dirfd/no-follow e rejeita symlink, path escape ou
  owner/mode inválido;
- em `.staging`, aceita **somente** S0–S3; em S1–S3 adquire `owner.lock`, e então
  remove o prefixo abandonado sem exigir metadata completa;
- entry staging fora de S0–S3 falha readiness, nunca é apagada por adivinhação;
- em `active`, nunca remove diretório cujo `owner.lock` não consegue adquirir;
- com owner lock adquirido, exige metadata/hash e que todos os members observados
  pertençam ao allowlist fechado antes de apagar; desconhecido/divergente falha
  readiness;
- faz `fsync` após unlink/rmdir e antes de liberar `coord.lock`;
- prova barreiras em cada fronteira create→lock→temp→metadata→rename, além de
  limpeza após `SIGKILL`, `os._exit`, power loss simulado e restart real.

Cleanup normal não desmonta files enquanto a tentativa ainda está publicada. O
owner primeiro encerra filho/socket e para toda mutação, depois libera/fecha
`owner.lock`; em seguida adquire `coord.lock`, reabre `active/id`, adquire o owner
lock agora livre, valida o universo fechado e remove tudo. Se o scavenger venceu a
corrida e o diretório já não existe, cleanup termina idempotentemente. A ordem
global é sempre `coord.lock → owner.lock`; não existe caminho owner→coord, evitando
deadlock e falso `malformed` durante teardown.

Attempt roots jamais são pesquisados para retomar sessão ou estado canônico.
Startup só fica ready depois de adquirir `coord.lock`, resolver staging/active e
provar zero órfão; o scanner periódico e o cleanup normal usam a mesma gramática,
ordem de locks e semântica idempotente.

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
2. adquirir lock cross-process por lead/ownership DB;
3. consultar duplicate;
4. se não existe boundary state, solicitar freeze na authority compartilhada;
   sob o lock, `begin_freeze` entra em `FREEZING`, incrementa epoch e nega novos
   permits; então libera o lock;
5. fora do lead lock e de qualquer boundary transaction, aguardar/reconciliar os
   permits do epoch anterior até active count zero; cada mutator conclui seu permit
   em transação própria, sem precisar do lead lock;
6. readquirir o lead lock e executar `finish_freeze`, que revalida owner row/epoch,
   zero permits ativos e source snapshot, e só então publica `FROZEN` com token;
7. quando o turno é E2E, agora sob o lead lock que não será mais liberado até o
   ack, CAS da admission `admitted→commit_fenced` no QualificationJournal, capturando
   boundary preimage version/hash e fixando admission revision, commit token e owner
   instance;
8. carregar estado e gênese/import **somente em memória**, sem persistir
   `boundary_state`, import claim ou fencing token;
9. executar Maya + reads ainda sob o lead lock, sem DB write transaction;
10. reduzir no kernel puro e validar proposal/decision/receipt;
11. ainda em `FROZEN`, reler source version/snapshot A fora de qualquer boundary
   transaction; divergência em relação ao snapshot usado por Maya aborta;
12. para E2E, ainda sob o lead lock e antes de `BEGIN IMMEDIATE`, reler a admission
   e exigir o mesmo tuple `commit_fenced/revision/token/owner`; status `aborted`,
   token stale ou journal indisponível abortam sem boundary write;
13. abrir transação boundary curta com busy timeout menor que o tempo restante;
14. depois de obter o writer lock, reamostrar deadline e revalidar apenas
   event/source identities, state version/hash, o epoch/token `FROZEN` local e a
   admission-fence capturada;
   nenhuma leitura legacy/remota ocorre dentro da transação;
15. persistir gênese/import claim, CAS state/fence, event/sources, receipt,
   commands, relays e outboxes atomicamente;
16. reamostrar deadline imediatamente antes de `COMMIT`; deadline vencida causa
    rollback integral;
17. após commit, finalizar ownership como `BOUNDARY_OWNED` vinculado ao receipt;
18. para E2E, ainda sob o mesmo lead lock, CAS
    `commit_fenced→turn_receipt_committed` no journal com os bytes/hash do receipt;
    crash nessa janela é resolvido pelo reconciler a partir do receipt durável;
19. liberar lock.

`LeadMigrationOwnershipPort` é uma autoridade separada que **todos** os ingress e
efeitos mutantes legacy/candidate precisam consultar. Estados fechados:

```text
LEGACY_OWNED → FREEZING → FROZEN → BOUNDARY_OWNED
                    ↘ LEGACY_OWNED(new epoch)  [released_to_legacy receipt]
```

Todo mutator legacy precisa adquirir um `LegacyWritePermit` no mesmo
`SQLiteMigrationOwnershipStore` **antes** de ler estado ou preparar um efeito. O
permit contém lead, operation ID, epoch e fencing token; fica ativo durante
provider dispatch, local commit e receipt terminal. Permit lifetime **não** mantém
o lead flock; acquire/complete usam transactions próprias. O mutator revalida o
permit por full-tuple CAS imediatamente antes de provider dispatch e no commit
local final; durante `freezing`, somente o `draining_epoch` permanece autorizado.
`begin_freeze` faz CAS
`LEGACY_OWNED→FREEZING`, move o epoch corrente para `draining_epoch`, incrementa o
owner epoch e nega novos permits. O freezer então libera o lock e observa active
count até zero enquanto permite `complete_permit`; ele nunca espera sob flock ou
SQLite writer transaction. Ao readquirir o lead lock, a
authority captura source version/hash e faz CAS
`FREEZING→FROZEN`; esse snapshot é o único que o passo 7 entrega à Maya. Permit de
processo morto só pode ser fechado por reconciler quando um
operation receipt prova resultado terminal; resultado externo incerto bloqueia a
migração em `manual_review`. Freeze nunca ignora ou expira permit em voo.

- `FREEZING` bloqueia novos writers e drena/invalida de forma comprovável todos os
  writers autorizados no epoch anterior;
- `FROZEN` só existe com active permits zero e bloqueia writes/flush/callbacks
  legacy e nova entrega para o lead;
- contém owner token, source version/hash e nunca expira de volta ao legado
  automaticamente;
- crash antes do boundary commit deixa o lead congelado; reconciler só libera
  depois de provar ausência de boundary state/event/receipt;
- crash depois do boundary commit deixa o lead congelado; reconciler encontra o
  receipt e finaliza `BOUNDARY_OWNED`;
- source snapshot é relido sob o freeze antes de abrir `BEGIN IMMEDIATE`; como não
  há permit legacy ativo, ele não pode mudar no intervalo até o commit;
- stale token/owner ou source hash divergente nunca cria gênese/import claim.

`register`, `begin_freeze`, `finish_freeze`, `finalize`, `release` e coordinator
usam o lead lock. `acquire_permit` e `complete_permit` usam apenas transactions
curtas do ownership DB; isso é necessário para o drain convergir enquanto o freezer
não segura o lock. A transação boundary faz somente CAS local do epoch/token
esperado; nenhuma authority remota ou legacy I/O fica sob `BEGIN IMMEDIATE`. A
corrida obrigatória pausa writer W depois de obter permit, executa `begin_freeze`,
libera o lock, retoma W/complete, readquire e executa `finish_freeze`; prova que
`FROZEN` não é publicado até W terminar ou ir para manual review e que W jamais
grava depois de `FROZEN/BOUNDARY_OWNED`.

#### Migration ownership backing store

O único owner persistente é `SQLiteMigrationOwnershipStore`, num root compartilhado
montado read-write por **todos** os processos legacy e candidate. Ele não fica no
boundary DB nem em memória. O root é um único arquivo SQLite local num volume de
filesystem que suporta flock/POSIX locks; não pode ser NFS/object storage. Todos os
processos no host abrem o mesmo path e startup compara device/inode. Schema
`migration-ownership-v1`, com universo exato:

1. `migration_owners` — PK `lead_key_hash`; state
   `legacy_owned|freezing|frozen|boundary_owned|manual_review`,
   epoch, `draining_epoch` nullable apenas em `freezing`, owner token, source
   version/hash, active permit count, boundary receipt/hash opcional,
   manual-review reason e timestamps;
2. `migration_permits` — PK `permit_id`; UNIQUE operation ID; FK somente para
   `migration_owners.lead_key_hash`; `permit_epoch` imutável, mutator kind, status
   `active|terminal|manual_review`, fencing token, source-before hash, operation
   receipt JSON/hash opcional e timestamps; epoch/token são validados por full-tuple
   CAS/trigger contra o owner state, não por FK composto mutável;
3. `migration_transitions` — append-only PK transition ID; lead/epoch,
   `transition_revision`, `previous_transition_hash`, from/to, expected row hash,
   canonical transition receipt/hash e occurred-at. UNIQUE `(lead, revision)` e
   `(lead, receipt_hash)`; revision começa em zero e cada receipt inclui/hash-chaina
   revision anterior + previous hash.

Constraints e store invariants fecham `active_permit_count == COUNT(active permits)`
na mesma transação de toda operação,
`fencing_token == permit claim/epoch sequence`, um owner row por lead, um operation
ID global, transition revision contígua por lead e receipt tuple
all-null/all-present. Startup semantic scan recompõe owner state/count
e transition chain de permits; row extra, ausente ou divergente falha readiness.

Matriz de permit imutável: acquire exige owner `legacy_owned` e
`permit_epoch == owner.epoch`; completion aceita essa mesma igualdade enquanto
`legacy_owned`, ou owner `freezing` com
`permit_epoch == owner.draining_epoch == owner.epoch - 1`. Nenhum UPDATE de owner
reescreve `permit_epoch`; trigger proíbe UPDATE desse campo. `finish_freeze` exige
`draining_epoch` presente, active count zero, zero permit ativo daquele epoch e
limpa `draining_epoch` ao entrar em `frozen`.

Table universe, DDL hash, WAL/FK/integrity e filesystem identity são verificados em
startup/readiness; não há migration automática. Root ausente, compartilhado com
state live errado, schema extra ou processo mutator sem o mesmo DB device/inode é
stop condition.

Operações fechadas, todas em `BEGIN IMMEDIATE` curto e full-tuple CAS:

- `register_legacy_owner(...)` cria epoch 0/`legacy_owned` somente com row ausente,
  sob o mesmo lead lock, e exige prova de ausência de boundary state/event/receipt;
  o compatibility rollout registra/valida toda lead elegível antes de abrir ingress;
  lead nova é registrada pelo guard antes do primeiro legacy read/effect;
- `acquire_permit(lead, external_operation_id, mutator_kind, expected_epoch)`
  deriva `operation_id = H("phase8-migration-op-v1", lead_key_hash,
  mutator_kind, external_operation_id)`; a UNIQUE global é sobre esse ID
  domain-separated. Aceita somente
  `legacy_owned`, incrementa active count, insere permit e retorna token/receipt;
  duplicate exata retorna os mesmos bytes;
- `complete_permit(permit, operation_receipt)` exige token/epoch/status ativos,
  grava receipt terminal e decrementa active count atomicamente;
- `begin_freeze(...)` faz CAS `legacy_owned→freezing`, incrementa epoch e fecha
  novas aquisições, preservando o epoch antigo em `draining_epoch`; retorna
  imediatamente com drain receipt, sem esperar sob lock;
- `finish_freeze(...)` exige active count zero, zero permit ativo, source snapshot
  hash/version exatos e faz CAS `freezing→frozen`;
- `finalize_boundary_ownership(...)` exige boundary turn receipt byte-idêntico e
  faz CAS `frozen→boundary_owned`;
- `release_to_legacy(...)` só é permitido a partir de `freezing|frozen` com active
  count zero/zero permit ativo, quando scan prova ausência completa de boundary
  state/event/receipt/effects; ele incrementa
  epoch e faz CAS diretamente para um novo `legacy_owned`, retornando receipt
  byte-idêntico em retry. `released_to_legacy` é somente transition-receipt kind no
  log append-only, não estado terminal da owner row; nunca automático;
- reconciler lê permits/transitions/operation receipts e só conclui estado
  comprovável; efeito incerto faz CAS para `manual_review`, nunca decremento cego.

Candidate/freeze nunca cria owner row implicitamente. Row ausente, registro sem
prova ou lead conhecida não pré-registrada durante compatibility preflight fecha o
ingress. Corridas register/freeze são serializadas pelo lead lock + CAS; acquire
concorrente é serializado pelo SQLite writer lock/full-tuple state CAS: ou
incrementa count no epoch antigo antes de begin, ou observa `freezing` e falha.
Unique PK garante uma única gênese da authority.

Todo mutator usa um guard wrapper obrigatório que recebe `LegacyWritePermit`; o
mesmo permit/token participa do commit local e do provider effect receipt. Import,
freeze e boundary commit persistem ownership epoch/token nos respectivos receipts.
Compatibility preflight compara graph/import scans + runtime observations para
provar que webhook, debounce/flush, Stripe, Wise, image/actions e callbacks não têm
entrypoint sem esse wrapper. Nenhum store alternativo pode implementar a port.

Não existe write **boundary/commercial** pré-Maya. O único write permitido é a
claim de ownership control-plane recuperável acima. Crash/timeout/EOF antes do
passo 11 deixa zero row change no boundary e zero efeito comercial. Retry usa uma
nova sessão efêmera porque a tentativa anterior não pode ser retomada nem se tornar
pública.

O runtime legacy atual não conhece essa autoridade. Portanto rollout gradual é
NO-GO até um compatibility guard separado, explicitamente autorizado, provar que
webhook, debounce/flush, Stripe, Wise, image/actions e todos os mutating callbacks
respeitam `FREEZING/FROZEN/BOUNDARY_OWNED`. Sem esse guard, a única alternativa elegível é
cutover global quiescente; mixed-mode por lead é proibido.

### 8. Lock cross-process deadline-aware

Implementação mínima Linux:

- arquivo derivado da identity do migration-ownership DB + lead hash, em lock root
  compartilhado por legacy/candidate, privado ao serviço;
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
IMMEDIATE`, antes do primeiro write e antes de `COMMIT`. Todos os writers de turno,
guards legacy, ownership transitions e candidate usam o mesmo lock file/inode.
Startup compara ownership DB e lock-root device/inode/mount identity entre
processos. Worker writes pós-handoff não dependem dele, mas obedecem
seus próprios leases/fences e transações curtas.

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
  structural graph + capability policy + effective stage-binding digest
  behavior-state snapshot digest lido no turno
  qualification/admission sequence + revision + commit-fence token quando E2E
  allocation-manifest hash + generations/allocations imutáveis vinculadas pelo turno E2E
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

Universo mínimo proposto de onze tabelas:

1. `boundary_state`;
2. `boundary_events` — passa a armazenar `turn_receipt_json/hash`;
3. `boundary_event_sources` — IDs/hashes ordenados, com identidade única por
   lead/source event e FK ao aggregate turn;
4. `boundary_turn_artifacts` — commitments e canonical artifacts do transcript;
5. `boundary_commands`;
6. `boundary_command_relays`;
7. `boundary_outbox` — jobs internos fechados;
8. `boundary_public_outbox` — uma row por chamada externa/chunk;
9. `boundary_dispatch_authority` — geração durável de policy/binding;
10. `legacy_import_claims`;
11. `decision_comparisons`.

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

`boundary_dispatch_authority` possui uma row histórica por allocation pública
exata, PK `(authorization_id, scope_subject_id, channel_scope, generation,
allocation_id)`. `authorization_kind` é `conversation_test|e2e`; E2E exige
qualification/scenario IDs, enquanto conversation test usa seu approval/budget ID.
Cada generation possui no mesmo table universe uma row reservada
`row_kind=generation_header, allocation_id=__header__`; as demais têm
`row_kind=allocation`. Cada row fixa contract/authorization-binding/policy/stage
digests, recipient/target/channel binding, chunk ordinal permitido e immutable
generation. Header state é `open|closed|manual_review`; allocation state é
`available|bound|dispatch_fenced|terminal|closed|manual_review`, além de public row
binding nullable, CAS revision e timestamps. A autorização correspondente instala
as allocations antes de abrir seu ingress; E2E instala **todas** as allocations do
contrato, e conversation test instala um budget público finito. Trigger/scan proíbem
duas generations não encerradas no mesmo authorization/scope/channel. A generation seguinte
só pode nascer quando cada row anterior está `terminal|closed` e closure receipt
bilateral existe; `dispatch_fenced|manual_review` bloqueia avanço.

`install_public_allocations` insere header `open` + manifest completo numa única
transaction. `close_public_generation` fecha o header e rows disponíveis/ligadas;
se a instalação ainda não ocorreu, insere atomicamente um header tombstone `closed`
com o manifest hash esperado. Assim close-vs-late-install é serializado no boundary
DB: tombstone primeiro faz install falhar; install primeiro é fechado integralmente.

O commit do turno precisa fazer CAS `available→bound` e ligar cada public row por
FK composta à allocation distinta na mesma boundary transaction. Row extra, ordinal
extra ou
allocation inexistente aborta o turno. `fence_dispatch` faz CAS
`bound→dispatch_fenced` no mesmo commit da outbox row; revogação fecha somente
`available|bound`, nunca reescreve generation nem apaga histórico. Semantic scan
prova backlinks exatos, `fenced_at` dentro da generation e digests idênticos. O
commit do delivery receipt também faz `dispatch_fenced→terminal`; resultado incerto
vai para `manual_review`.
Configuração em memória/env nunca é autoridade para fence.

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
inalteradas mais duas tabelas, total oito:

1. `reservation_boundary_ingress_receipts` com:

- `ingress_receipt_id` PK;
- `source_turn_receipt_hash` e UNIQUE `(source_turn_receipt_hash, command_id)`;
- `bundle_json`, `bundle_hash`;
- `command_id` UNIQUE e FK para `reservation_commands`;
- E2E authority key tuple nullable all-null/all-present
  `(qualification, scenario, effect_scope, generation, allocation_id)` com FK
  composta para a authority key; trigger exige `row_kind=allocation` e é obrigatória
  em role E2E;
- `target_receipt_json`, `target_receipt_hash`;
- `applied_at`.

O ID lógico de um turn receipt pode alimentar múltiplos commands; por isso não há
UNIQUE isolada em `source_turn_receipt_hash`. A unicidade é composta por
source receipt + command, enquanto `command_id` continua globalmente único.

2. `reservation_e2e_effect_authority`, uma row por **alocação exata pré-instalada**,
com PK `(qualification_id, scenario_id, effect_scope, generation, allocation_id)`;
inclui row reservada `generation_header/__header__` e rows `allocation`.
Contract/authorization-binding digests, immutable generation, allocation ordinal,
effect kind/role `primary|compensation`, parent allocation opcional; header state
`open|closed|manual_review`, allocation state
`available|bound|dispatch_fenced|terminal|closed|manual_review`, command/workflow binding
nullable e CAS revision. O command é ligado uma única vez; generation/allocation
nunca são reescritos. Ela participa da transaction de `execution_ledger` fence no
Gate 13; fica vazia/closed fora de E2E.

Antes de abrir admission, `install_e2e_reservation_allocations` insere o manifest
completo e não vazio + header numa transaction por duplicate byte-idêntica;
`close_e2e_reservation_generation` fecha o existente ou insere header tombstone
`closed` quando install ainda não ocorreu. Trigger/scan
proíbem nova generation até todas as rows da anterior estarem `terminal|closed` com
closure receipt; `dispatch_fenced|manual_review` bloqueia avanço. O target
receipt recompõe genesis, eventos contíguos, summary outboxes, workflow final,
command e ledger seed. `accept_boundary_reservation` insere tudo, inclusive o
ingress receipt, e faz CAS de uma allocation exata `available→bound` na mesma
transaction. Não cria authority. Duplicate exige igualdade byte a byte de
bundle/command/allocation/target receipt; divergência é `IdentityConflict`.

O Phase5-v6 **não** adiciona tabela de provider outcome receipt. A segunda tabela é
apenas authority preventiva de budget/fence. O owner do outcome continua
`execution_ledger.outcome_json/hash`. Uma função pura e versionada
`derive_reservation_effect_receipt(ingress_receipt, command, workflow,
ledger_terminal)` produz bytes/hash canônicos, incluindo ingress backlink,
certainty/evidence/economic before-after e operation identity. O qualification
journal persiste essa projeção e source row IDs/hashes; startup/qualification
rederivam e exigem igualdade. Não há write novo no UoW owner.

**Phase 6 follow-up: schema `1 → 2`.** O universo v2 contém as onze tabelas v1
inalteradas mais três, total quatorze:

1. `handoff_boundary_ingress_receipts`, vinculada por FK/UNIQUE a
   `handoff_workflows.handoff_id`;
2. `payment_boundary_ingress_receipts`, vinculada por FK/UNIQUE a
   `payment_commands.settlement_command_id`.
3. `payment_e2e_effect_authority`, uma row por allocation pré-instalada, com a mesma
   PK/header-tombstone/generation/history/state machine da authority Phase5; settlement command é
   nullable até `available→bound`, e compensation allocation referencia a primary
   allocation parent. Participa da mesma transaction do payment fence.

Ambas possuem `ingress_receipt_id` PK, `source_turn_receipt_hash`, bundle
JSON/hash, target subject ID, target receipt JSON/hash e `applied_at`; cada par
source receipt+target subject é único. `install_e2e_payment_allocations` instala o
manifest exato antes da admission. `accept_boundary_handoff` e
`accept_boundary_settlement` persistem full replay + ingress receipt na mesma
transaction; settlement também liga uma allocation `available→bound`, sem criar
authority. Duplicate retorna bytes idênticos.

`payment_boundary_ingress_receipts` carrega a mesma E2E authority key composta,
nullable all-null/all-present e obrigatória para settlement E2E, com FK para a
authority key e trigger `row_kind=allocation`; handoff não possui provider
allocation. Target receipts
incluem a chave e o post-CAS authority row hash.

O Phase6-v2 também não recebe tabela extra de **outcome**; a terceira tabela nova é
somente authority preventiva. Para settlement,
`derive_settlement_effect_receipt(payment_boundary_ingress_receipt,
payment_command, workflow, payment_ledger_terminal)` projeta deterministicamente o
receipt; handoff/payment delivery receipts já são rows owner das tabelas existentes
v1. O journal guarda somente projeção auditável + backlinks/hashes e rederiva em
todo scan. Nenhuma função substitui/muta `outcome_json`, ledger ou ingress receipt.

Não haverá migration automática desses UoWs na `0.8.0`. Phase 8 exige roots novos
e vazios para schemas Phase5-v6 e Phase6-v2. Encontrar schema Phase5-v5,
Phase6-v1, migration history extra ou table universe inesperado no root escolhido
falha startup e é stop condition; migração offline futura exige design/validator e
autorização separados. Startup, readiness, duplicate e claim executam full replay
e semantic scan dos ingress receipts contra todas as rows alvo.

Identidade de schema é declarativa e fail-closed:

- Phase5 persiste `SCHEMA_VERSION=6` + DDL hash em `schema_migrations`, como já faz
  v5;
- boundary v8 e Phase6-v2, que não têm migration table, usam constantes de versão
  package-owned apenas como label e autenticam o DB por igualdade exata de table
  universe, columns/indexes/triggers/FKs/checks e aggregate DDL hash normalizado;
- a versão sem o DDL/universe esperado nunca é aceita, e nenhum `PRAGMA
  user_version` ou metadata row implícita altera o universo declarado.

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
- qualification/scenario/immutable generation/allocation ID quando E2E;
- `artifact_hash` independente do backlink.

A relay row, fora do bundle hash, carrega o `source_turn_receipt_hash`.

Essa história nasce e evolui na `reservation_execution_projection` do boundary;
hashes isolados não são aceitos como reconstrução. Para settlement, o bundle
Phase 6 carrega anchor/policy/history/evidence/command e estado final, mesmo quando
o `PaymentWorkflow.history` já contém parte dessas informações.

`boundary_command_relays` usa a máquina fechada
`pending|leased|acked|cancelled|manual_review` e persiste owner, fencing token,
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
5. em E2E, exige que o target ingress ligue a allocation pré-instalada exata; nunca
   cria authority target-local;
6. força full replay no UoW alvo e valida receipt por command/bundle/source receipt
   e allocation hashes;
7. ack no boundary DB por full-tuple CAS.

Morte/falha antes do target call libera/requeue a claim; após três preparation
failures vira `manual_review`. Morte/exception durante/depois do target call pode
ter commit alvo: a lease expira, retry chama o mesmo ingresso com os mesmos bytes,
recebe o mesmo receipt e faz ack. Divergência target ou budget esgotado vai para
manual review, nunca para provider. Morte após target receipt e antes do source ack
é coberta pelo mesmo replay idempotente.

Closure de qualification faz CAS de relays `pending|leased` ainda pre-target para
`cancelled` e fecha primeiro as allocations target. Worker stale pode chamar apenas
o ingress local idempotente: a allocation fechada rejeita a ligação, e seu ack stale
falha. Relay com target receipt já commitado precisa ser reconciliado/acked e entrar
no scan; cancellation nunca o apaga.

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
- idempotency key com domínio `phase8-public-v1`, release child digest,
  `lead_key_hash`, target-binding hash, channel ID, aggregate turn ID, chunk index e
  artifact hash, nunca do receipt hash nem de texto/PII bruto;
- status `pending|leased|dispatch_fenced|delivered|cancelled|manual_review`;
- owner/token/lease/claim-count, preparation-failures e dispatch-slots-consumed;
- authorization kind/ID, scope subject/allocation ID + immutable generation;
  qualification/scenario IDs são obrigatórios apenas em E2E;
- policy/stage-binding digests esperados;
- source turn receipt backlink, delivery receipt hash e timestamps.

O target-binding hash inclui deterministicamente recipient/contact binding,
channel/account binding e route, com domínio/release. Unicidade externa não depende
de aggregate/source IDs serem globais; duas leads ou canais distintos nunca
compartilham idempotency key.

Máquina fechada:

- `pending`: sem lease, slot 0;
- `leased`: lease completa, slot 0;
- `dispatch_fenced`: lease completa, slot exatamente 1;
- `delivered`: sem lease, slot 1, delivery receipt presente;
- `manual_review`: sem lease; slot 0 para preparation terminal ou 1 para resultado
  pós-fence desconhecido.
- `cancelled`: sem lease, slot 0, allocation fechada antes de qualquer fence.

`fencing_token == claim_count`; expiry é `expires_at <= now`; todas as mutações
fazem full-tuple CAS e stale completion é rejeitada. Claim escolhe somente o menor
chunk não terminal cujo predecessor está `delivered`. Lease `leased` expirada é
reclaimable porque slot=0 prova zero send. Preparation failure libera para
`pending` e incrementa failure count; ao máximo 3 termina `manual_review` com
slot=0. Somente `fence_dispatch` consome permanentemente o único slot.

`PublicDeliveryWorker.run_once()` processa no máximo uma row, um chunk e uma
chamada ManyChat:

1. claim;
2. prepara request sem side effect e valida role, capability, binding e allowlist;
3. produz `DispatchPermit` canônico contendo row/chunk/request hash, lease owner e
   token, target binding hash, authorization kind/ID + scope subject/allocation ID +
   immutable generation (e qualification/scenario quando E2E), capability-policy
   digest,
   stage-binding digest e validade limitada pela lease/deadline;
4. `fence_dispatch` executa uma única transaction no boundary DB: revalida a row e
   exige a allocation exata `bound`, mesma immutable generation/digests e backlink
   para essa row; somente então faz CAS conjunto da allocation
   `bound→dispatch_fenced` e da outbox `leased→dispatch_fenced`, consumindo o slot.
   Permit negado/stale nunca consome slot;
5. adquire `dispatch-exec/<row-id>.lock` por dirfd/no-follow e `flock` exclusivo;
6. sob esse lock, relê a row e exige o mesmo `dispatch_fenced`, owner/token, slot e
   authority generation capturada no fence; se um reconciler já terminalizou, não
   envia;
7. chama ManyChat exatamente uma vez mantendo o execution lock até persistir o
   delivery receipt ou encerrar por crash;
8. grava receipt e faz CAS conjunto da allocation
   `dispatch_fenced→terminal` + outbox `dispatch_fenced→delivered` por full-tuple CAS;
   somente então libera o execution lock.

Policy/allowlist denial é preparation failure terminal ou requeue conforme o
motivo fechado, sempre com slot 0; jamais vira resultado externo incerto. Mudança
de policy/binding entre prepare e fence invalida o permit e rejeita o CAS. Dark
mode nega claim e, por defesa em profundidade, também prepare/fence/send.

O budget público é preventivo: a allocation exata existe antes do turno e o commit
do turno só cria/binda a quantidade de chunks autorizada. Closure usa uma única
boundary transaction para fechar allocations `available|bound` e mover public rows
`pending|leased` ainda slot 0 para `cancelled`; CAS stale do worker falha. Row já
`dispatch_fenced` não é cancelável como zero-effect e precisa receipt terminal ou
`manual_review` antes de fechar a qualification.

No Gate 11, antes de E2E, um `ConversationTestDispatchAuthorization` separado fixa
o único recipient/target/channel autorizado, janela, generation e um número finito
de allocations públicas. Cada chamada consome uma allocation; budget esgotado fecha
o send sem fallback. Allocations não usadas são fechadas ao terminar o teste. Esse
artefato não abre provider/relay/payment/handoff e não conta como qualification E2E.

Um fence confirmado é uma autorização irrevogável para **essa única chamada**;
revogação posterior fecha allocations `available|bound`, preserva a immutable
generation e bloqueia novos fences, mas não tenta desfazer slot já consumido.
Expiry da lease, sozinha, não
revoga o executor que mantém o execution lock; enquanto ele está vivo, reconciler
não pode declarar resultado terminal desconhecido.

Falha/crash depois do fence e antes de receipt deixa a row representavelmente
`dispatch_fenced`. Um **reconciler sem capability de send** varre leases vencidas
e promove atomicamente para `manual_review`; successors permanecem bloqueados.
Não ocorre reenvio automático. Prefixo parcialmente entregue fica explícito por
chunks anteriores `delivered`; o chunk incerto e os posteriores nunca são enviados
automaticamente. Corridas worker/reconciler, close/reopen e restart precisam de
prova.

O reconciler recebe estruturalmente somente store+clock+execution-lock factory;
não recebe ManyChat, credentials ou send port. Ele distingue `leased` expirada
(reclaim pre-fence) de `dispatch_fenced` expirada. Para esta última, precisa
primeiro adquirir o mesmo execution lock; sob o lock, relê full tuple e somente
então faz CAS conjunto da outbox e allocation para `manual_review`. Se não consegue
adquirir, não altera a row e
readiness sinaliza dispatch em voo. Depois que terminaliza e libera o lock, worker
antigo pode até adquiri-lo, mas a releitura obrigatória vê `manual_review` e não
envia. Testes com barreiras cobrem worker pausado antes/depois de fence/lock,
reconciler concorrente, lease vencida com lock vivo, morte depois do send e antes
do receipt, stale worker e budgets terminais.

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
- migration ownership v1 store/port/reconciler + legacy reader/importer;
- Maya turn port + UDS tool gateway;
- attempt-root scavenger;
- kernel adapter;
- command relay e boundary-internal-job workers;
- durable dispatch authority + public delivery worker/execution-lock factory +
  capability-free reconciler;
- memory authority/learning target;
- qualification journal/controller;
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

O qualification controller/journal também integra o graph, embora capabilities
permaneçam fechadas antes do Gate 13. A dispatch authority e execution-lock root
são obrigatórias sempre que o public worker é construído.

Memória aprendida não contamina esse digest estrutural. Um
`BehaviorStateSnapshot` canônico contém schema/version/hash da memória dinâmica;
ele é validado no startup, vinculado ao stage binding de admissão e persistido
por turno. Canary recebe clone autenticado e isolado do snapshot escolhido, nunca
mount RW da memória de produção.

Cada estágio possui ainda uma `CapabilityPolicy` canônica e hash, contendo a
matriz de capabilities, worker modes e guard semantics, mas **não** roots,
allowlist concreta ou percentual. Esses valores ficam nas identidades de stage
fechadas abaixo, todas hashadas e persistidas para auditoria.

Há quatro identidades distintas; não se exige igualdade impossível entre o
binding efetivo dos turnos e um binding criado somente depois da selagem.

`EffectiveE2EDeploymentBinding`, persistido em cada turn receipt, tem schema
fechado:

```text
release_child_manifest_digest
runtime_graph_digest
capability_policy_digest
behavior_state_snapshot_digest_at_admission
runtime_role = canary_e2e
provider_scope + workflow_scope + effect_scope
qualification_id + qualification-contract hash
allowlist_digest + allowlist_cardinality
traffic_stage
state_root_class = ephemeral_canary
instance_id + admission_epoch
```

O behavior digest pode avançar entre turnos apenas por `LearningReceipt` válido;
isso gera um novo effective-binding digest e fica explícito no receipt daquele
turno. Provider/workflow/effect scopes são enums/IDs canônicos, nunca texto livre.
Roots são classes fechadas, validadas contra mounts reais; paths concretos não
entram em hashes de comportamento.

Allocations pré-instaladas não podem depender do behavior digest futuro. Um
`E2EEffectAuthorizationBinding` estável é derivado do contract + release child +
graph + capability policy + qualification/admission epoch + scopes + allowlist +
traffic stage + root class/instance constraints, **excluindo** o behavior snapshot.
Cada `EffectiveE2EDeploymentBinding` de turno precisa projetar exatamente esses
campos estáveis; somente seu behavior digest pode avançar por LearningReceipt. O
allocation manifest referencia o authorization-binding digest estável, evitando
ciclo/valor futuro.

#### E2E provider effect authority

Post-validation não controla budget. Antes de abrir o primeiro ingress E2E, o
controller deriva do contrato um `ExactEffectAllocationManifest` fechado com uma
allocation distinta para **cada** efeito permitido:

- reservation provider primary/compensation;
- payment provider primary/compensation;
- cada chamada pública/chunk, com scenario, target/channel e ordinal exatos.

Cada allocation fixa qualification, scenario, contract/effect-authorization binding,
effect/workflow/channel scope, immutable generation, allocation ID/ordinal e parent
allocation quando compensatória. A soma das rows `row_kind=allocation` — headers
não contam — é o budget; não existe “budget por command” criado depois. Os manifests são instalados idempotentemente em
`boundary_dispatch_authority`, `reservation_e2e_effect_authority` e
`payment_e2e_effect_authority` **antes** de admission passar a `OPEN`; cada target
retorna installation receipt canônico e o journal faz ack. Instalação parcial deixa
admission `INSTALLING`, nunca aberta.

Cada install target é uma única transaction `header open + todas as allocations`.
Cada close target é também transacional e, se install ainda não ocorreu, insere um
header tombstone `closed` para a mesma qualification/scenario/scope/generation e
manifest hash. Logo install-vs-close tem ordem total local: tombstone-first rejeita
install tardio; install-first permite close de todo o conjunto. Closure receipt
autentica header, contagens e aggregate allocation hash.

Isso preserva os universos **boundary-v8 = 11**, **Phase5-v6 = 8** e
**Phase6-v2 = 14 tabelas**. Cada authority table é append-history por immutable
generation; revogar fecha rows `available|bound` e uma generation futura é inserida
como novas rows, nunca UPDATE da geração antiga. Trigger + semantic scan garantem no
máximo uma generation com rows disponíveis/ligadas por
qualification/scenario/scope. Ledger/outbox fence referencia a chave completa da
allocation histórica.

Ingress target não cria authority. Na mesma transaction de
`accept_boundary_reservation|settlement`, ele valida o relay bundle/turn receipt e
faz CAS de uma allocation pré-existente `available→bound`, ligando o command. Row
ausente, generation fechada, allocation já ligada, kind/parent/binding divergente ou
command extra falham antes de provider claim. Crash target-commit/journal-ack é
reconciliado pela chave determinística; retry recebe os mesmos bytes.

Command compensatório criado posteriormente pelo reducer target só é elegível se o
manifest já contém uma allocation `effect_role=compensation` com parent allocation
exata. A UoW cria command/workflow e faz `available→bound` dessa allocation na mesma
transaction local. Sem allocation, a criação falha antes de outbox/ledger claim;
reutilizar a allocation primária é proibido.

`fence_dispatch` Phase5/6 revalida e faz CAS, na **mesma transaction do ledger
fence**, da allocation `bound→dispatch_fenced`, exigindo command/economic binding,
generation, contract/effect-authorization binding e effect role exatos. Se close vence, fence
falha sem provider call. Se fence vence, a allocation histórica permanece
`dispatch_fenced` e precisa de ledger outcome terminal ou `manual_review`; cancel e
qualification não podem ignorá-la. Qualquer worker E2E que fence sem allocation é
poison test/stop condition.

O mesmo UoW commit que grava o ledger outcome faz CAS da allocation
`dispatch_fenced→terminal` e persiste o ledger backlink; isso não cria novo owner de
outcome. Crash/resultado externo incerto deixa `dispatch_fenced|manual_review` e
proíbe geração seguinte.

O scan bilateral exige bijeção entre manifest, authority rows, commands/public rows,
fences e outcomes: `available=0`, `bound=0`, allocations `terminal` iguais ao
budget executado, nenhuma allocation/command/chunk extra e generations/bindings
idênticos. Assim o excesso é impedido **antes** do efeito, não apenas detectado.

Cancelamento/revogação executa saga fechada: bloqueia admission; fecha por CAS todas
as allocations `available|bound` nos três roots; fecha a generation pública; marca
relays e public rows pre-target/pre-fence como `cancelled`; e aguarda installation,
target-commit/source-ack e relay leases chegarem a terminal conhecido. Worker stale
que tenta target ingress encontra allocation fechada; stale public/provider fence
falha no CAS. Allocation já fenced precisa outcome terminal/manual-review. Somente
depois de receipts/acks de fechamento bilaterais o journal pode publicar
`CANCELLED`; root inalcançável ou efeito incerto permanece `MANUAL_REVIEW`.

Dark/ingress fechado exercitam o graph completo com capabilities negadas, não
omitem classes. Antes de abrir a canary E2E, a autorização humana cria um
`E2EQualificationContract` canônico e imutável. Ele contém uma lista **não vazia**
de `E2EScenarioContract`, cada uma com:

```text
scenario_id + deterministic turn/source identities
lead/target/channel hashes e allowlist binding
provider/workflow/effect scopes + janela
expected command/relay kinds e cardinalidades exatas
expected target-ingress receipt kinds/cardinalidades
expected provider-effect outcome kinds/cardinalidades
expected public chunk/delivery cardinalidades
expected compensation/cancellation receipts, quando aplicável
expected final state/economic hashes
external-effect budget exato
```

O contrato global exige `scenario_count >= 1`, pelo menos um provider-write outcome
terminal e pelo menos uma public delivery terminal. Zero cenários, somente reads,
somente turn receipts ou budgets externos zero **não** podem qualificar rollout.
Cada turno E2E carrega `scenario_id/contract_hash`; ingress fora do contrato é
negado e qualquer efeito extra é finding terminal.

`ProviderEffectOutcomeReceipt` é um tipo fechado derivado do estado terminal
persistido pelo worker/UoW owner. Ele liga command, target-ingress receipt, provider
operation, idempotency key, before/after ou economic hash, resultado terminal e
effect role `primary|compensation` e parent-effect ID quando for compensation. Uma
compensation é outro command/ledger outcome owner-owned, com seu próprio
`ProviderEffectOutcomeReceipt`; ela nunca é campo aninhado do receipt primário. Um
`TurnReceipt` sozinho prova somente commit do turno;
nunca prova relay, provider outcome ou delivery.

Ele não cria ledger concorrente. Para reserva, referencia/recompõe exatamente
`execution_ledger.outcome_json/hash` e command/workflow rows do Phase5-v6; para
settlement, referencia/recompõe `payment_ledger.outcome_*`; handoff/payment
deliveries usam as receipt rows Phase6-v2. O qualification journal guarda uma cópia
canônica + source row IDs/hashes para scan, mas a autoridade continua no UoW/worker
owner. Outcome sem source row terminal byte-idêntica é inválido.

As únicas constructors são as funções puras `derive_reservation_effect_receipt`
e `derive_settlement_effect_receipt` definidas no contrato dos UoWs acima. O journal
não aceita bytes enviados pelo worker; ele lê as rows owners e deriva novamente.

Antes de selar, o qualification controller exige igualdade bilateral e
cardinalidade exata entre o contrato e:

- turn receipts admitidos;
- command relays `acked` e target-ingress receipts terminais;
- provider-effect outcome receipts `succeeded` e compensation receipts requeridos;
- public rows `delivered` com delivery receipts exatos;
- final states/hashes esperados.

Qualquer item `pending|leased|dispatch_fenced|manual_review`, receipt ausente,
extra, duplicado, divergente, cenário não executado ou efeito fora do budget falha
a qualificação. O scan inclui source e target stores; não aceita contagem derivada
somente do boundary receipt.

No contrato/scans, “compensation receipt” significa um
`ProviderEffectOutcomeReceipt(effect_role=compensation, parent_effect_id=...)`
derivado de command/workflow/ledger owner rows. Se o workflow não possui command de
compensation migrado, o contrato não pode prometer compensação e o gate humano deve
autorizar somente cenário cujo rollback externo não a exige.

A recuperação usa `QualificationJournal`, um SQLite root isolado com schema/table
universe exato:

1. `qualification_admission_state` — singleton com state
   `INSTALLING|OPEN|QUALIFYING|FROZEN`, epoch, next admission sequence, cached
   active count e qualification ID;
2. `qualification_admissions` — membership-append-only (row/key nunca removida),
   PK qualification/epoch/sequence e
   UNIQUE turn ID; scenario/contract hashes, status
   `admitted|commit_fenced|turn_receipt_committed|aborted|manual_review`,
   immutable membership
   digest, boundary preimage version/hash nullable até `commit_fenced`, admission
   revision, commit token/owner nullable, turn/abort receipt backlinks e timestamps.
   ACK/abort nunca removem
   membership;
3. `qualification_runs` — qualification ID, contract bytes/hash, admission epoch,
   allocation-manifest bytes/hash, status, cutoff sequence, canonical ordered
   admitted-set JSON/hash, expected CAS fields e hashes dos artifacts terminais;
4. `qualification_scenarios` — uma row por scenario ID/contract hash, com os
   aggregates e terminal verification receipt;
5. `qualification_artifacts` — IDs/kinds/hashes/bytes canônicos de turn, target
   ingress, allocation installation/closure, provider outcome, delivery,
   compensation, learning, seal, transition e binding receipts.

Constraints/semantic scan exigem cached active count igual ao número de admissions
`admitted|commit_fenced`, revisions monotônicas por row, tuple commit
token/owner/preimage all-null ou all-present conforme status e backlinks terminais
all-null/all-present. Row, scenario, installation receipt ou membership extra/ausente
falha readiness.

`qualification_runs` e todas as scenario rows são criadas atomicamente na
autorização **antes** do primeiro turno E2E, em status `INSTALLING`; cenário ausente
não pode receber allocation ou admission. O controller instala o manifest exato nos
três roots por operation IDs estáveis, persiste cada installation receipt por
target-commit/journal-ack e só então faz CAS conjunto da run/admission state para
`OPEN`. Crash repete a instalação byte-idêntica; instalação parcial nunca admite.

Esse journal é também a autoridade de admission da canary E2E: todo ingress
incrementa a sequence e insere uma membership row na mesma DB antes de entrar no
coordinator. O domínio imutável do membership digest contém **somente**
`qualification_id, epoch, sequence, turn_id, scenario_id, contract_hash,
effective_binding_hash`; exclui status, revision, owner/token, ACK/abort backlinks e
timestamps. Portanto o admitted-set continua rederivável depois dos ACKs.

Admission e boundary commit formam um handshake linearizável pelo mesmo lead lock.
Sob o lock, o coordinator faz `admitted→commit_fenced`, mantém o lock durante Maya e
captura/persiste a boundary preimage version/hash no mesmo CAS, mantém o lock durante
Maya e boundary commit, inclui revision/token no `TurnReceipt` e faz journal ack
antes de liberar. O admission reconciler também precisa adquirir esse mesmo lock:

- se encontra boundary receipt com revision/token exatos, faz ack idempotente para
  `turn_receipt_committed`;
- se a boundary state ainda possui exatamente a preimage version/hash da admission
  e não há event/receipt/child row para o aggregate turn, target-ingress receipt ou
  allocation consumida/ligada por esse turno, faz full-tuple CAS
  `admitted|commit_fenced→aborted` e persiste `AdmissionAbortReceipt` com zero-scan
  hash;
- divergência ou qualquer efeito incerto termina em `MANUAL_REVIEW`, nunca abort.

Um coordinator que ainda não obteve o lock encontra `aborted` e não consegue fence;
um coordinator que já possui `commit_fenced` mantém o lock, portanto o reconciler
não pode publicar aborto concorrente. Crash após boundary commit/antes do ack é
resolvido pelo receipt; crash antes do commit libera o flock e permite zero-scan.
Registro nunca é apagado, e cenário abortado falha a qualificação.
Falha normal do coordinator antes do boundary commit usa o mesmo caminho de aborto
sob o lock; não abandona indefinidamente uma row `commit_fenced`.

Status fechados:

```text
INSTALLING → OPEN → QUALIFYING → EFFECTS_VERIFIED → LEARNING_DRAINED → MEMORY_SEALED
     → TRANSITION_RECORDED → QUALIFIED
     ↘ CANCELLED | MANUAL_REVIEW
```

`qualification_id` deriva de contract hash + release/graph/policy digests +
admission epoch. O allocation manifest inclui esse qualification ID e seu hash é
persistido na run antes de qualquer instalação. Toda transição é full-tuple CAS e
persiste canonical receipt/hash;
duplicate byte-idêntica retorna os mesmos bytes, identidade divergente falha.
Restart abre o journal, executa scan bilateral e retoma da última transição
confirmada, sem repetir provider ou public effects.

Fechar claims normais de learning também possui operation ID estável por
qualification. A memory authority retorna `LearningClaimsClosedReceipt`
byte-idêntico em retry; o journal o persiste em `qualification_artifacts`. Estado
`QUALIFYING` sem esse receipt sempre repete/consulta a mesma operação antes de
avançar para effects scan.

Quando o gate solicita qualificação, o controller executa esta ordem obrigatória:

1. numa única transaction do journal, faz CAS da run `OPEN→QUALIFYING` e de
   `qualification_admission_state OPEN→QUALIFYING`, fixa `cutoff_sequence` no
   último admission e copia **todas** as membership rows ordenadas até o cutoff para
   `admitted_set_json/hash` da run. ACK concorrente só muda status/backlink e nunca
   membership; o commit bloqueia novos admissions sem janela. Depois, a authority
   de learning é fechada para claims normais com target-commit/journal-ack
   idempotente antes de continuar;
2. usa exclusivamente o admitted set já congelado e aguarda todos os turn
   receipts, target ingress, provider outcome, compensation e delivery receipts
   exigidos pelo contrato;
3. executa o scan E2E bilateral acima; só então persiste `EFFECTS_VERIFIED`;
4. recompõe dos receipts o conjunto finito e completo de learning jobs; um drainer
   de qualificação pode claimar somente esse conjunto;
5. aguarda completion/ack de jobs leased e drena os pending; qualquer extra,
   ausente, divergente ou `manual_review` falha; persiste `LEARNING_DRAINED`;
6. chama a memory authority por operação idempotente
   `seal(qualification_id, expected_version, expected_hash, epoch, admitted_set_hash)`;
   ela persiste seal+snapshot receipt atomicamente e duplicate retorna os mesmos
   bytes. O journal grava esse receipt por CAS e chega a `MEMORY_SEALED`;
7. constrói/persiste deterministicamente `BehaviorTransitionReceipt`, então faz
   CAS para `TRANSITION_RECORDED`;
8. constrói/persiste deterministicamente `SealedCanaryQualificationBinding`, faz
   CAS para `QUALIFIED` e mantém admission/memory congelados até rollout/cancel.

Crash depois do seal na memory authority e antes do journal ack é recuperado
chamando o mesmo `seal` e recebendo o receipt byte-idêntico. Crashes entre
transition/binding write e status CAS recompõem os mesmos bytes e completam o CAS;
nenhum passo é best-effort. Zero learning é conjunto vazio + before==after, nunca
campo omitido.

Cancelamento começa por CAS da admission para `FROZEN`; executa a saga de closure
das allocations/relays/public rows descrita acima e persiste todos os closure
receipts antes de `CANCELLED`. Não existe “scan de rows atuais e depois cancelar”:
os tombstones são as próprias allocations pré-instaladas da immutable generation,
portanto relay/turn stale não consegue criar authority nova. Antes de
`MEMORY_SEALED`, somente após closure bilateral o journal pode encerrar e reabrir em
novo epoch/generation. Depois de `MEMORY_SEALED`, snapshot não é “deselado”:
reabrir canary exige clone byte-idêntico para nova memory authority/root e novo
epoch, invalidando toda qualification/authorization anterior. Injeções em cada
fronteira provam retry, ausência de cenário vacuamente verde e nenhum item
omitido/extra.

`SealedCanaryQualificationBinding` é criado **depois** do seal e contém:

```text
release + graph + capability policy digests
provider/workflow/effect scopes
qualification epoch + cutoff/admitted-set hash
E2E qualification-contract hash + nonempty scenario count
exact allocation-manifest hash + immutable generation aggregate hash
ordered allocation installation receipts + terminal allocation/ledger aggregate
ordered scenario terminal-verification receipt aggregate hash
ordered effective-E2E-binding aggregate hash
sealed behavior snapshot digest
behavior transition receipt hash
canary root class + image/container binding attestations
```

Ele não finge ser o binding usado nos turnos. `RolloutAuthorization` é outro
artefato canônico e independente: referencia o qualification-binding digest e
fixa target role, allowlist digest/cardinality, traffic stage, production root
class, instance constraints, janela e approver identity. Seu digest esperado é
registrado antes de criar produção. Só pode ser criado quando o journal está
`QUALIFIED` e um scan fresco recompõe exatamente contract/scenarios/allocation
manifest + installation receipts + terminal authority/ledger rows, artifacts,
transition receipt, sealed snapshot e qualification binding; status anterior,
`CANCELLED` ou `MANUAL_REVIEW` rejeitam autorização.

Uma função fechada
`derive_production_initial_binding(qualification, authorization)` produz
`ProductionInitialDeploymentBinding`. Ela exige igualdade de release, graph,
capability policy, sealed behavior snapshot, transition receipt e
provider/workflow/effect scopes; permite somente:

- role `sealed_canary_qualification → production_initial`;
- root class `ephemeral_canary → persistent_production`;
- instance ID dentro das constraints autorizadas;
- allowlist e traffic stage exatamente iguais ao `RolloutAuthorization`.

Produção é inicializada por clone byte-idêntico do snapshot selado e o digest do
clone é revalidado antes de readiness. Qualquer diferença em memória, model/profile,
worker mode, scopes, capability/guard ou campo não listado falha. Cancelamento
transiciona a qualificação para `CANCELLED`; reabrir canary incrementa epoch e
invalida qualification/authorization anteriores. Paths privados podem diferir
somente conforme a root class e mount preflight, sem mudar bytes/comportamento.

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
- boundary-v8/11, Phase5-v6/8 ou Phase6-v2/14
  schema/hash/table-universe/WAL/FK/integrity inválido;
- migration-ownership-v1 root/schema/hash/device/inode inválido ou diferente entre
  processos mutators;
- lock dir/socket dir indisponível;
- attempt root malformado, symlink/path escape ou orphan não scavenged;
- qualquer port obrigatória ausente;
- outbox não durável para uma role que permite delivery;
- worker/capability incoerente;
- dispatch authority generation/digests ou execution-lock root incoerentes;
- provider write habilitado sem worker boundary correspondente;
- helper/route legado alcançável no graph promovível;
- plugin filho alcança import/capability proibida;
- structural graph/profile/config/catalog digest diferente do release manifest;
- semantic scan receipt↔sources↔transcript/artifacts↔commands↔relays↔target
  receipts↔outboxes divergente;
- behavior snapshot/binding/transition receipt não satisfaz o stage atual;
- qualification journal/schema/scenario/artifact scan divergente para stage
  `QUALIFYING|QUALIFIED`;
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

Antes do build, o controller cria uma identidade de input acíclica em três níveis:

1. um **payload root** limpo contém somente inputs source reais; os paths reservados
   `.phase8-generated/payload-context-manifest.json` e
   `.phase8-generated/source-attestation.json` precisam estar ausentes;
2. fora do payload root, `payload-context-manifest.json` canônico enumera cada input
   Docker-reachable por path relativo, kind, mode, symlink target quando permitido,
   bytes e hash — incluindo Dockerfile, `.dockerignore`, wheel, profile/config,
   skills/plugins e todos os sources de `COPY/ADD`;
3. `source-attestation.json` referencia o payload-manifest hash e fixa
   `source_F_commit/tree`, `source_E_commit/tree`, wheel/package identity,
   `runtime_F_commit/tree`, `runtime_E_commit/tree`, graph/profile/catalog hashes
   e `approval_manifest_hash`. Ela exclui explicitamente os dois artifacts gerados
   do domínio do payload.

`approval-manifest.json` é artifact externo, evidence-only e content-addressed,
criado **depois** de F/E imutáveis e dos reviews; não é member de E, portanto não
contém o próprio hash por ciclo. Ele autentica os pares source/runtime F/E,
parent(E)==F, diffs evidence-only, pareceres AND e package/wheel identity. O payload
funcional vem exclusivamente dos Fs; Es nunca entram nos bytes executáveis, mas o
hash do approval manifest entra na autorização da release. Alterar E, parecer ou
approval manifest exige novo manifest/review gate e muda source attestation/build-
input identity.

O controller monta o contexto final como tar canônico a partir **exclusivamente**
dos members listados no payload manifest mais os dois generated metadata files.
Não passa um diretório mutável ao builder. Uma identidade externa
`build_input_identity = H(domain, payload_manifest_bytes/hash,
source_attestation_bytes/hash)` cobre payload + attestation sem auto-referência.
Payload manifest e source attestation são baked em paths/labels fixos; a identidade
externa entra no `release-manifest.json`.

O preflight interpreta todos os stages/instruções do Dockerfile e a semântica de
`.dockerignore`, resolve wildcards e exige igualdade entre o universo alcançável e
o manifest. `ADD` remoto, named/external context, bind mount de build, path não
listado, member extra, `COPY` que resolve fora do universo ou mudança de
Dockerfile/`.dockerignore` falham. Poison tests adicionam arquivo não listado e
alargam `COPY`; ambos precisam falhar antes do builder.

Depois da publicação, um `release-manifest.json` externo, imutável e montado
read-only no container, vincula:

```text
source F commit/tree + source E commit/tree
→ wheel 0.8.0 hash/bytes
→ runtime F commit/tree + runtime E commit/tree
→ single combined approval-manifest hash
→ payload-context manifest/hash
→ source-attestation hash
→ external build-input identity
→ OCI index digest
→ linux/arm64 child manifest digest
→ config/layers
```

Existe exatamente **um** approval manifest combinado por release candidate. Não há
“source approval manifest” e “runtime approval manifest” separados; esses rótulos
são proibidos no schema/validator. O documento combinado enumera os dois pares F/E,
wheel/package identity e todos os pareceres AND.

Não se tenta incorporar o digest OCI da própria imagem dentro dela. O controller
verifica manifest→config/layers; o startup verifica payload manifest/source
attestation baked, release manifest montado, expected child digest injetado,
runtime graph e stage binding hashes. Qualquer lado ausente ou divergente falha
readiness. O release manifest comum termina em child/config/layers e seu hash é
igual em todas as instâncias da mesma release.

Container ID, runtime mounts, resolved immutable image reference, instance ID,
root paths/classes, effective config digest e stage binding pertencem a uma
`ContainerExecutionAttestation` por instância. O controller cria e revalida essa
attestation após `docker create` e antes de readiness; ela referencia o release
manifest, mas não altera o documento comum. Canary, produção e rollback precisam
de attestations próprias que provem child digest→config/layers→container efetivo.

SOUL, HERMES, profile, skills, plugin, config não secreto, ToolDispatch catalog,
modelo/provider/reasoning e Hermes version ficam dentro da imagem ou têm hashes
exatos no release manifest e são verificados fail-closed no startup. O
`RuntimeGraphManifest` e a `CapabilityPolicy` também são vinculados à cadeia e aos
receipts. Segredos são referenciados somente por nomes de slots/capabilities, nunca
por valor ou hash reversível.

Esta decisão substitui os trechos anteriores que tratavam image ID + archive como
identidade primária. A substituição precisa ocorrer **antes do Slice 0**, não no
fim da implementação.

### Quarentena obrigatória antes do Slice 0

Os blobs abaixo são inputs históricos, não autoridade executável:

| Path | Blob/SHA-256 histórico |
|---|---|
| `docs/superpowers/specs/2026-07-21-phase-8-shadow-canary-rollout-design.md` | `0613b334f99d0601f2b9e1aec16dc2d5c044e6ee` / `93136a8832d8895a312c40a52b942f22bc0a094d5d38b5d1545bd1cd883e14a3` |
| `docs/superpowers/plans/2026-07-21-phase-8-shadow-canary-rollout.md` | `20a320227bcd74d108c526fc448a9a09392ceff7` / `2765510f1ca4ec371e7843bc189ef47a4872d5369341a1c763041563a5c0256d` |
| `docs/refactor/decisions/0006-promote-identical-oci-digest.md` | `27fbe14cd56c37508091e8c737fc6d1de5c38122` / `33023167589e8714b9c19f1dfdc372732ccf83c3ce1d1c6630057c7498a9f767` |
| `docs/refactor/phases/phase-08-shadow-canary-rollout.md` | `0f8631a1766a0ac582ed0535a12435d9d8377c47` / `2ca3a2f9dd626bc9f25de4c0cf42a6a6c75063c9724b06e1fd18f49ec88910ec` |
| `docs/refactor/evidence/phase-08/entry-baseline.json` | `7885d05d2446f255379128b2b9a2bc67bad625e4` / `7acb04dbe81831d70d42b9cc16b30b686b96e14891a3d86e64717b59146028cd` |
| `docs/refactor/evidence/phase-08/red-results.json` | `52b3c2466f716636c013ca648fdc740ce6b39a87` / `5d385dca3986c1e746576232d519397e60d3003db258340848b694550bd26b32` |
| `tests/test_phase8_entry.py` | `a96aeafaf76b6a497041771418ae6f399ac45609` / `1068f2d416d416f755ea839d141fc46ad998b87b1a016a3840c407fa7f3c2e88` |

Antes de qualquer RED do Slice 0, um commit de contract replacement deve:

1. publicar o plano substituto
   `docs/superpowers/plans/2026-07-21-phase-8-operational-boundary-correction.md`;
2. atualizar ADR 0006, phase page, risk/evidence interfaces e entry tests para
   child manifest arm64, F/E evidence pair e gates desta spec;
3. marcar spec/plano anteriores como `HISTORICAL-NON-EXECUTABLE` e removê-los de
   qualquer index/validator/command owner ativo;
4. publicar um quarantine manifest com paths, blobs e SHA-256 completos;
5. provar por teste/scan que nenhum comando ou import ativo referencia as
   interfaces antigas.

Até esse commit/plano receberem aprovação, ficam proibidos os comandos/interfaces
do plano antigo: `docker buildx build --load`, `docker image save`,
`python3 -B scripts/build_phase8_image.py`,
`python3 -B scripts/generate_phase8_manifest.py --write|--check`,
`ImageIdentity(image_id, archive_sha256, ...)` como autoridade e qualquer
create/promote por image ID. Slice 18 apenas **verifica e fecha** o contrato já
corrigido; ele não corrige retrospectivamente o plano que governou Slices 0–17.

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

Cada RED possui identidade reproduzível própria:

```text
U = unfixed base commit/tree
P = test-only patch blob/SHA-256 + exact paths
S = expected staged Git tree after applying P to U
R = execution-root manifest + command/env/exit/duration/counts
O = exact raw output object SHA-256/bytes
```

O runner cria worktree detached/temporary index a partir de U, aplica P, verifica
que apenas test/fixture paths permitidos mudaram e que `git write-tree == S`, então
executa em `S`. O envelope fixa U/P/S, root absoluto resolvido, Python/tool versions,
env-name allowlist sem valores secretos, comando exato e O. Um patch que toca
production code não é RED elegível. Reexecução a partir de U/P precisa reproduzir
a mesma causa/asserção, embora duração e formatação não determinística explicitada
possam variar em campos excluídos do oracle.

Raw output não entra no Git. Em vez de `/tmp`, ele vai para um
`EvidenceArtifactStore` privado, content-addressed e retido até o closeout:

- runner usa env scrubbed e scanner fail-closed para impedir segredo ou PII de
  lead no output retido;
- o store root possui `coord.lock`, `.staging/` e `objects/`. Sob `coord.lock`, o
  publisher cria primeiro `.staging/<random>/`, depois `owner.lock` e `object.tmp` por
  `openat(O_CREAT|O_EXCL|O_NOFOLLOW, 0600)`, adquire/retém o owner flock e libera o
  coord lock. Scavenger usa sempre `coord.lock→owner.lock` e nunca remove staging
  cujo owner lock não consegue adquirir;
- publisher escreve `object.tmp` enquanto calcula hash/bytes, faz `fsync`,
  reabre/no-follow e confirma o digest esperado; aplica mode final read-only e faz
  **novo `fsync` do inode depois do chmod**;
- somente depois publica no namespace SHA-256 em `objects/` por
  `renameat2(RENAME_NOREPLACE)` ou `linkat` no-replace, seguido de directory
  `fsync`; reviewer nunca observa o nome final antes dos bytes completos;
- se o nome final já existe, o publisher reabre/no-follow, valida mode/owner,
  rehasheia bytes e aceita apenas igualdade exata; conteúdo parcial/divergente
  bloqueia o gate, nunca é sobrescrito. Depois da publicação, publisher libera o
  owner lock; para cleanup, adquire `coord.lock` e só então readquire owner lock,
  valida members, remove staging e faz dir-fsync. Se o scavenger venceu essa janela,
  ausência do staging termina cleanup idempotentemente. Como mkdir e criação de
  owner lock ocorrem sob coord lock, crash pode deixar o prefixo legítimo `S0={}`;
  os demais são `S1={owner.lock}` e `S2={owner.lock, object.tmp}`. Sob coord lock,
  scavenger remove/dir-fsync S0; para S1/S2 exige owner lock livre. Member
  desconhecido/symlink falha readiness, e
  publisher vivo nunca é removido; não existe caminho owner→coord;
- manifest externo fixa path, bytes, hash e retention; reviewers reabrem e
  rehasheiam o object;
- ausência, mutação, scanner failure ou retenção não comprovada vale zero.

Depois dos RED/GREEN, congela-se o par:

```text
F = functional candidate commit/tree (code + tests, sem evidence-only envelopes)
E = filho direto de F contendo somente evidence/quarantine/manifest paths
```

Antes de criar F, o validator compara bilateralmente staged tree S com os paths de
P em F:

- todo test/fixture blob tocado por P precisa permanecer byte-idêntico em F;
- remover, enfraquecer ou alterar qualquer desses blobs exige novo U/P/S/R/O RED;
- production paths ausentes de P podem mudar de S→F somente dentro da allowlist de
  implementação do slice;
- test/fixture novo não coberto por P exige sua própria proveniência RED ou marcação
  explícita de GREEN-only helper sem substituir a asserção causal;
- o mapping P-path→S-blob→F-blob é versionado em E e validado nos reviews.

`E` versiona P, S e envelopes sanitizados que apontam para O; não versiona raw.
Um validator prova parent(E)==F, diff F→E restrito à allowlist evidence-only e
ausência de mudança em source/tests/package inputs. Builds, wheels e testes
funcionais usam F; auditoria usa o par F/E e os objects retidos. Package hash,
wheel bytes e runtime candidate são fixados ao mesmo par quando aplicável.

Reviews são AND gates no mesmo **F/E pair + package identity**. `Needs fixes`,
timeout ou summary ausente valem zero. Qualquer mudança material em F, E, wheel,
package ou evidence object invalida todas as aprovações e exige nova rodada.

### Slice 0 — Contract lock

- contract-replacement commit/plano/quarantine manifest do Gate 2 já aprovados;
- scanner prova interfaces antigas históricas e comandos antigos inalcançáveis;
- testes de estrutura para novos types/ports;
- RED prova que v0.7.0 não contém projection, proposal, receipt e relay;
- nenhum runtime change.

### Slice 1 — Types e wire v2

- `ConversationProjection`;
- read-result union;
- source event identities, `MayaTurnRequest/Closure/Proposal`;
- normalized tool/learning proposals, transcript binding e graph/policy/binding
  digests;
- `EffectiveE2EDeploymentBinding`, `E2EEffectAuthorizationBinding`,
  `SealedCanaryQualificationBinding`,
  `BehaviorTransitionReceipt`, `RolloutAuthorization` e
  `ProductionInitialDeploymentBinding`;
- `E2EQualificationContract/E2EScenarioContract`,
  `ProviderEffectOutcomeReceipt` derivado, terminal scenario verification e effect
  budgets;
- `ExactEffectAllocationManifest`, immutable generation/allocation IDs e
  installation/closure receipts;
- qualification/admission states e transformação fechada, com canonical wire,
  completeness, zero-learning e forbidden-field mutations;
- public message/receipt/relay types;
- genesis lookup tri-state e `BoundaryInternalJob` handoff/learning;
- exact-type, canonical serialization, unknown-field e mutation tests.

### Slice 2 — ToolDispatch proposal contract

- `normalize_proposal` sem autorização/command/provider;
- `verify_authorized` após kernel;
- catálogo/alias/typed arguments com owner único;
- matriz read/state/command/bloqueado e mutations.

### Slice 3 — Schema/store v8

- onze boundary tables exatas, incluindo turn artifacts e dispatch authority, com
  FKs bidirecionais;
- migration-ownership-v1: três tabelas exatas, DDL hash, permits/transitions e
  reconciler full-tuple CAS;
- Phase5-v6 tem oito tabelas exatas, incluindo boundary ingress receipt e
  reservation E2E effect authority;
- Phase6-v2 tem quatorze tabelas exatas, incluindo handoff/payment boundary ingress
  receipts e payment E2E effect authority;
- authority header/allocation row-kind checks, immutable generation, composite FKs,
  header tombstone e transition/ledger backlinks exatos;
- roots novos obrigatórios; schemas antigos/universos extras fail-closed;
- receipt/public/relay atômicos com fault injection entre todos os writes;
- v7/universo divergente fail-closed;
- zero row change em deadline/CAS/fence/genesis failure;
- semantic scans receipt↔artifacts↔rows e source/target receipt hashes.

### Slice 4 — Lock e transações curtas

- multiprocess flock;
- freeze split-phase: begin sob lock, drain sem lock, finish após readquirir;
- permit ativo no epoch antigo → begin freeze → complete permit → finish freeze;
- FK de permit somente ao lead; permit_epoch permanece imutável após epoch advance;
- release-to-legacy cria novo epoch `legacy_owned` byte-idempotente;
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
- ownership `LEGACY_OWNED→FREEZING→FROZEN→BOUNDARY_OWNED`, permit drain e
  reconciler de crash;
- acquire/complete permit, begin/finish freeze, finalize/release e uncertain-effect
  manual-review no único ownership store compartilhado;
- legacy snapshot A alterado para B durante Maya aborta sem gênese/import claim;
- nenhum write/flush/callback legacy passa enquanto `FROZEN`;
- gênese/import claim persistidos apenas no commit final;
- legacy reader inalcançável após gênese;
- ordem lock→snapshot→Maya/read sem transaction→kernel→CAS/commit;
- validação bilateral transcript/proposal/decision/reply/receipt;
- source-event aggregate e conflito hash;
- admission `admitted→commit_fenced→turn_receipt_committed` sob o mesmo lead lock;
- abort reconciler só sob lead lock, com zero-scan/receipt handshake e stale
  coordinator impedido de commit;
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
- allocation manifests target-local são pré-instalados/acked antes de admission;
- install-vs-close nos dois targets: close-first tombstone rejeita install tardio;
  install-first fecha o conjunto completo; crash/retry retorna receipts idênticos;
- bundle E2E apenas liga allocation pré-existente na mesma transaction de
  command/ingress; nunca cria authority;
- compensation command liga allocation parent-bound no mesmo UoW commit;
- execution/payment fence consome allocation scenario/binding/generation no mesmo
  CAS do ledger; revogação/cancel fecha target authorities antes do journal;
- crash target-authority-commit/journal-ack, stale generation, over-budget e worker
  que tenta fence sem authority falham sem provider call;
- command/internal relay machines: exact expiry, full-tuple CAS, pre-target reclaim,
  max 3 failures, stale ack rejection e target-receipt divergence;
- crash target-commit/boundary-ack;
- duplicate exata e divergente;
- relay não chama provider;
- policy fechada deixa provider workers sem claim/dispatch.

### Slice 11 — Public delivery ledger e reconciler

- uma row/fence/receipt por chunk/chamada externa e ordering por predecessor;
- public allocation exata pré-instalada e ligada ao chunk no commit do turno;
- public install-vs-close header tombstone e generation history append-only;
- leased pre-fence exact expiry/reclaim, preparation release/budget e stale CAS;
- idempotency key isola release+lead+target+channel;
- dispatch authority generation/policy/binding participa da mesma transaction de
  fence;
- execution lock exclui worker stale versus reconciler pós-fence;
- policy revocation concorrente, worker pausado com/sem execution lock e
  reconciler não produzem send após terminalização nem segundo send;
- cancellation fecha available/bound allocations e public rows slot 0 antes de
  publicar `CANCELLED`; stale worker não cria/fence nova allocation;
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
- qualification controller bloqueia admission/normal learning claims antes de
  drenar, sela por CAS e mantém freeze até rollout/cancel;
- cinco qualification tables exatas; authorization cria run/scenarios INSTALLING,
  instala/acka manifests nos três roots e só então abre admission/run;
  cutoff copia membership append-only para admitted-set na mesma transaction que
  OPEN→QUALIFYING; ACK/crash concorrente não altera membership;
- admitted-set hash usa apenas campos imutáveis, excluindo status/backlinks/tempo;
- `E2EQualificationContract` não vazio, cardinalidade/effect budgets exatos e scan
  bilateral de turn/target/provider/delivery/compensation receipts;
- QualificationJournal crash-idempotente até `QUALIFIED`, incluindo seal orphan e
  cancel/reopen por novo epoch/root;
- effective→qualification→authorization→production oracles bilaterais e mutations;
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
- primeiro congelar/publicar candidatos imutáveis F e evidence child E;
- validator terminal reautentica F/E, S→F test blobs e artifacts retidos;
- EvidenceArtifactStore prova write/fsync/chmod/fsync/publish/dir-fsync e
  coord→owner scavenger S0/S1/S2 contra publisher vivo, SIGKILL e power-loss;
- revisão funcional ocorre somente depois, no mesmo F/E pair já congelado;
- qualquer mudança subsequente cria novo par e invalida os pareceres.

### Slice 15 — Wheel 0.8.0

- construir nova wheel 0.8.0;
- RECORD/metadata/wire/schema/hash/bytes autenticados;
- package review 3/3 no mesmo wheel e upstream F/E pair.

### Slice 16 — Runtime candidate e wiring

- criar novo runtime candidate limpo;
- incorporar wheel e composition root sem delta estranho;
- startup/lifespan real, health ready e ingress local;
- testes focados e blast radius runtime.

### Slice 17 — Runtime terminal verification

- suíte integral runtime única para o candidato final;
- startup/restart/crash/worker readiness;
- revisão funcional/security/packaging 3/3 no mesmo source/runtime F/E pairs e
  wheel;
- source/runtime live fingerprints reautenticados.

### Slice 18 — Release contract executável

- verificar que spec/plano/ADR/evidence já foram substituídos antes do Slice 0 e
  continuam coerentes com child manifest `linux/arm64`;
- payload-context manifest, source attestation e external build-input identity
  sem ciclo; canonical tar + Dockerfile/`.dockerignore` poison tests;
- source/runtime F e E + único approval-manifest hash combinado explícitos na source attestation
  e release manifest, enquanto bytes executáveis vêm somente de F;
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
- plano substituto/quarantine não aprovados antes do Slice 0;
- RED sem U/P/S/R/O reproduzíveis ou raw object publicado/retido atomicamente;
- test/fixture blobs do RED S divergem em F sem novo RED autenticado;
- review não está vinculada ao mesmo F/E pair + package identity;
- DB v7 real descoberto;
- migration ownership não possui o único v1 store compartilhado/DDL exato ou
  mutator alcança efeito sem permit do mesmo DB;
- reply ainda produzida/enfileirada pós-commit;
- filho consegue injetar observation/fact/proposal fora do transcript pai;
- sessão Hermes de tentativa pode ser retomada após falha;
- duplicate chama Maya/read/kernel;
- cache/debounce oculta source-event conflict;
- provider write alcançável no turno;
- command relay sem bundle full-replay/source receipt ou best-effort;
- receipt/row integrity não é bidirecional;
- public send sem fence/receipt por chamada ou reconciler;
- policy/binding/allowlist avaliada somente depois do dispatch fence;
- dispatch fence não usa authority generation no mesmo CAS;
- idempotency key pública não isola release/lead/target/channel;
- reconciler pode terminalizar sem adquirir execution lock ou worker stale pode
  enviar depois da terminalização;
- UDS sem HMAC/peer/final transcript binding;
- transcript/proposal/decision não recomputável após restart;
- plugin filho ainda alcança ToolExecutor/provider/delivery/memory writer;
- attempt creation/scavenger não usa staging+root coordination lock+atomic publish;
- transaction aberta durante LLM/read remoto;
- write boundary persistido antes de Maya;
- AdmissionAbortReceipt pode ser publicado sem o mesmo lead lock/commit fence ou
  boundary commit não consome/revalida admission revision/token;
- permit legacy anterior sobrevive a `FROZEN` ou não é revalidado no commit final;
- freeze espera permits mantendo lead lock/transaction, permit_epoch é reescrito ao
  avançar owner epoch ou release não retorna a novo `legacy_owned`;
- snapshot legacy não é relido sob freeze antes da transaction boundary;
- UoW Phase5/6 target não está no schema novo exato/root novo;
- relay/internal/public lease machine não fecha pre-target/pre-fence CAS e expiry;
- exact effect allocation manifest não é pré-instalado/acked antes de admission,
  ingress cria authority tardia ou generation histórica pode ser reescrita;
- provider E2E fence não consome allocation target-local exata no mesmo CAS do
  execution/payment ledger;
- public chunk não liga allocation exata no commit do turno/fence ou efeito extra
  consegue executar com budget próprio inventado;
- cancellation publica `CANCELLED` antes de fechar/ackar allocations, relays e
  public rows nos três roots;
- memory apply e `LearningReceipt` não são atômicos;
- `create_app` aceita adapter obrigatório `None`;
- factory/graph ou capability policy do E2E diferente da promoção;
- qualification aceita ingress/learning depois do cutoff ou sela antes do drain;
- qualification aceita zero cenário, ausência/extra, item não terminal ou apenas
  turn receipts sem target/provider/delivery outcomes;
- provider-effect receipt não é derivado deterministicamente das owner UoW rows ou
  cria tabela/ledger concorrente fora dos universos v6/v2;
- qualification seal/transition/binding não têm journal/CAS/retry byte-idêntico;
- admitted-set hash inclui status/backlink/timestamp mutável;
- effective binding, qualification, transition receipt, rollout authorization ou
  production binding não formam a transformação fechada aprovada;
- qualquer ingress mutante bypassa seu boundary;
- mixed-mode iniciado antes do compatibility guard de migration ownership;
- payload context/attestation é circular, aceita member não listado ou depende de
  directory build context mutável;
- evidence object usa nome SHA final antes de write+fsync+chmod+fsync+rehash
  completos ou scavenger não reconhece S0/S1/S2;
- release manifest comum inclui container/instance state;
- source/runtime F/E pairs ou approval-manifest hash ausentes da source attestation
  e release manifest;
- promoção/rollback não fixados ao child manifest digest `linux/arm64`;
- Slice 14–18 ou review AND gate incompleto;
- runtime operacional alterado antes da autorização correspondente.

## Gates de aprovação

1. **Design:** Carlos aprova esta arquitetura; ainda sem código.
2. **Plano/quarentena:** plano TDD substituto, quarantine manifest, ADR/page/evidence
   interfaces corrigidos e review aprovados; só então Slice 0 pode começar.
3. **Upstream terminal closeout:** Slice 14 verde no source F/E pair exato.
4. **Wheel:** 0.8.0 autenticada e package review 3/3 no mesmo F/E pair.
5. **Runtime wiring terminal:** candidata nova, Slice 17 e review 3/3 nos source e
   runtime F/E pairs exatos.
6. **Release contract / GO de build:** Slice 18, source/runtime live reautenticados
   e decisão explícita; nenhuma etapa anterior implica build.
7. **Build:** uma única publicação OCI; index e child manifest arm64 autenticados.
8. **Dark canary:** reads reais; graph completo; zero provider write/delivery.
9. **Ingress fechado:** rota/allowlist restritas, outbound fechado, estado limpo.
10. **Migration ownership readiness:** antes de abrir public delivery para uma
    identidade possivelmente legacy, compatibility guard, permits/drain e
    reconciler autenticam todos os ingress/efeitos. Alternativa somente por decisão
    explícita: identidade `PROVEN_ABSENT` e mecanicamente inalcançável por todo
    mutator legacy, ou cutover global quiescente.
11. **Conversation readiness:** mesma imagem/digest; allowlist efetiva com
    cardinalidade exatamente um; a única capability de **efeito externo** aberta é
    public delivery sob `ConversationTestDispatchAuthorization` com budget finito
    pré-instalado (reads permanecem read-only); learning pode operar apenas na
    memória canary isolada;
    provider/command-relay/payment/handoff effects mecanicamente fechados;
    state/session/outboxes canary limpos; memory baseline autenticada e isolada;
    zero pendência antiga; readiness verde e revisão aprovada.
12. **Teste humano:** somente agora Carlos é avisado e executa as conversas.
13. **Canary E2E/qualification:** autorização separada para
    contrato não vazio de cenários, provider/workflow/período/policy/effect budgets
    exatos. Exact allocations são instaladas/ackadas nos três roots antes de abrir
    ingress. No cutoff, admission fecha primeiro; qualification exige igualdade bilateral de
    turn, target-ingress, provider-outcome, delivery e compensation receipts
    terminais; então drena learning, sela behavior por CAS/journal e produz
    transition receipt + qualification binding. Policy/scopes são os do rollout
    inicial.
14. **Rollout:** decisão e `RolloutAuthorization` separadas; gradual, mesmo child
    manifest/policy/scopes/snapshot selado; production binding nasce somente pela
    função fechada qualification+authorization.
15. **Closeout Phase 8:** decisão posterior e separada, com snapshot terminal,
    review 3/3 no mesmo SHA/tree, CI remoto exato, manifests/riscos atualizados,
    rollback por digest preservado e `phase9_started=false`.

Até o Gate 11 completo, não é momento de avisar Carlos para conversar com o
agente. Rollout não implica closeout, e closeout não autoriza a Fase 9.
