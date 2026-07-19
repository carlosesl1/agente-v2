# Fase 4 — Resumo e confirmação únicos — Design

**Data:** 2026-07-19
**Status:** aprovado para implementação
**Fase:** `phase-04-single-summary-and-confirmation`

## Objetivo

Provar, sem I/O ou integração live, o contrato:

```text
uma versão comercial
→ um resumo determinístico persistível
→ uma decisão natural posterior vinculada ao resumo vigente
→ no máximo um ReservationCommand
```

A fase fecha a transformação entre domínio comercial pronto, apresentação ao
cliente e decisão tipada. O provider continua fora do fluxo; o comando é apenas
o artefato imutável já existente no domínio.

## Decisão arquitetural

A abordagem escolhida é um boundary puro e isolado:

```text
ReadyToSummarizeState
→ deterministic renderer
→ PreparedSummary + SummaryRecorded
→ reducer
→ AwaitingConfirmationState
→ ConfirmationClassifier Protocol
→ targetless DecisionCandidate
→ trusted binder injeta versão/assinatura vigentes
→ ConfirmationReceived
→ reducer
→ zero ou um ReservationCommand
```

O package novo será `reservation_confirmation`. Ele depende do domínio puro, mas
o domínio não importa o classificador nem uma LLM. A única extensão estrutural
da FSM é `AwaitingAdjustmentState`, necessária para desarmar o resumo anterior
assim que o cliente pede uma alteração.

Alternativas rejeitadas:

- incorporar linguagem/renderização em `reservation_domain`: mistura
  apresentação e autorização e dificulta substituir o classificador;
- certificar somente um corpus de frases: não fecha a ligação com
  draft/summary/command e preserva falsos verdes;
- integrar Hermes/LLM agora: antecipa runtime, modelo, rede e observabilidade da
  Fase 7 e torna o gate não determinístico.

## Ownership

| Componente | Decide | Não decide |
|---|---|---|
| `reservation_confirmation.renderer` | projeção pública determinística do draft | provider, disponibilidade futura, execução |
| `ConfirmationClassifier` | classe semântica da resposta | versão, assinatura, comando, target provider |
| `ReferenceConfirmationClassifier` | classificação fechada PT/EN para contrato e replay | cobertura aberta de linguagem natural |
| trusted binder | vincula a decisão ao resumo tipado vigente | interpreta fatos econômicos ou aceita target do classifier |
| `ReservationKernel`/reducer | versão, resumo vigente, transição e comando | texto livre, entrega, HTTP |
| store/outbox futura | atomicidade e entrega | conteúdo econômico ou autorização |

A confiança do classificador é diagnóstico. Ela nunca participa da autorização.

## Componentes

### `reservation_confirmation/types.py`

Tipos públicos imutáveis e fechados:

- `SummaryLocale`: `pt_BR | en`;
- `RenderedSummary`:
  - `renderer_id`;
  - `renderer_version`;
  - `locale`;
  - `draft_id`;
  - `draft_version`;
  - `subject_signature`;
  - `content`;
  - `content_hash`;
  - `claim_status`, fixo em `none`;
  - `private_fields`, sempre vazio;
- `PreparedSummary`:
  - `rendered`;
  - `summary_event_id`;
  - `outbox_message_id`;
  - `presented_at`;
  - `event: SummaryRecorded`;
- `ClassificationContext`:
  - `workflow_id`;
  - `summary_event_id`;
  - `draft_id`;
  - `draft_version`;
  - `subject_signature`;
  - `presented_at`;
  - `locale`;
  - `content_hash`;
- `ClassificationInput`:
  - `source_event_id`;
  - `received_at`;
  - `text`;
  - `context | None`;
- `DecisionCandidate`:
  - `decision`;
  - `classifier_id`;
  - `classifier_version`;
  - `confidence_basis_points` entre 0 e 10.000;
  - `evidence_codes` fechados;
- `BoundConfirmation`:
  - input sanitizado;
  - candidate;
  - `confirmation_event_id`;
  - `event: ConfirmationReceived | None`.

`DecisionCandidate` não possui draft version, assinatura, provider ref, offer ID
ou operation. Um classificador real não terá sequer um campo para propor o
target comercial.

### `reservation_confirmation/renderer.py`

Interface:

```python
def render_summary(
    draft: CommercialDraft,
    *,
    locale: SummaryLocale,
) -> RenderedSummary: ...
```

O renderer:

1. ordena componentes por `offer_id` e adicionais por código;
2. normaliza texto público com Unicode NFKC, whitespace único e limites
   explícitos;
3. usa datas ISO, horário `HH:MM`, inteiros e Decimal canônico;
4. calcula subtotal de componentes, adicionais e total final sem float;
5. apresenta todos os fatos revisáveis:
   - label público;
   - serviço, datas/horário e party;
   - valor/moeda;
   - adicionais;
   - nome, e-mail, telefone e país;
   - método de pagamento;
6. omite `offer_id`, `lookup_id`, `provider_ref`, hashes e IDs internos;
7. termina com aviso explícito de que nenhuma reserva foi criada e uma única
   pergunta de confirmação/ajuste;
8. produz PT-BR e EN com templates versionados;
9. calcula `content_hash` sobre renderer/version/locale/draft binding/content.

Não serão emitidas frases como “reserva confirmada”, “disponibilidade garantida”
ou “pagamento confirmado”. O antigo texto “Total confirmado” é classificado
como claim indevido e não será reproduzido.

### `reservation_confirmation/presentation.py`

Interface:

```python
def prepare_summary(
    state: ReadyToSummarizeState,
    *,
    locale: SummaryLocale,
    presented_at: datetime,
) -> PreparedSummary: ...
```

IDs são derivados deterministicamente do workflow, draft, version, signature,
locale, renderer version e content hash:

```text
summary:<sha256>
outbox:<sha256>
```

O caller não fornece IDs nem conteúdo. O bundle contém o texto exato para a
futura outbox e o `SummaryRecorded` correspondente. Nesta fase, “presented”
significa que estado e artefato de outbox estão prontos para persistência
atômica; não é uma alegação de entrega ManyChat.

O reducer continua sendo o owner de `SummaryPresented`. Um segundo
`SummaryRecorded` para a mesma versão não encontra handler em
`AwaitingConfirmationState`; duplicata exata é no-op pelo event ID.

### `reservation_confirmation/classifier.py`

Contrato:

```python
class ConfirmationClassifier(Protocol):
    def classify(self, item: ClassificationInput) -> DecisionCandidate: ...
```

Implementação de referência:

```python
class ReferenceConfirmationClassifier:
    classifier_id = "reference-confirmation"
    classifier_version = 1
```

Ela usa conjuntos fechados e precedência fail-closed:

1. ausência de contexto vigente → `AMBIGUOUS`;
2. sinais mistos → `AMBIGUOUS`;
3. pedido de mudança → `ADJUST`;
4. negativa/cancelamento → `REJECT`;
5. aceite explícito → `ACCEPT`;
6. aceite coloquial/contextual, somente com contexto → `ACCEPT`;
7. qualquer outro texto → `AMBIGUOUS`.

A normalização usa NFKC, `casefold`, whitespace canônico e pontuação periférica
não interrogativa. `?` é sinal semântico e falha como ambíguo; marcadores como
“sim/yes” só ajudam a detectar sinais mistos e não autorizam complementos fora
do conjunto fechado. Não há match de substring capaz de transformar “não
confirmo” em aceite.

Cobertura de referência:

- PT-BR e EN;
- explícito;
- coloquial;
- contextual;
- negativo;
- ambíguo;
- ajuste.

Esta implementação é uma prova de contrato, não promessa de cobertura linguística
live. Um futuro adapter Hermes/LLM deverá implementar o mesmo Protocol e passar o
mesmo corpus, sem ganhar autoridade adicional.

### `reservation_confirmation/binding.py`

Interface:

```python
def classification_context(
    state: AwaitingConfirmationState,
    *,
    locale: SummaryLocale,
    content_hash: str,
) -> ClassificationContext: ...


def classify_and_bind(
    state: AwaitingConfirmationState | None,
    *,
    source_event_id: str,
    received_at: datetime,
    text: str,
    locale: SummaryLocale,
    content_hash: str | None,
    classifier: ConfirmationClassifier,
) -> BoundConfirmation: ...
```

Regras:

- target version/signature vêm exclusivamente de `state.draft` e
  `state.summary`;
- `received_at` deve ser estritamente posterior a `presented_at`;
- contexto ausente, hash ausente/divergente, classifier exception, candidate
  inválido ou timestamp não posterior produzem `AMBIGUOUS` e nenhum evento
  autorizável;
- o event ID é derivado de workflow + summary + source event;
- o confirmation ID é derivado também da decisão;
- o texto livre e confidence não entram em `ConfirmationReceived` nem no
  comando;
- classifier não constrói `ReservationCommand`.

### `reservation_confirmation/properties.py`

O property runner constrói cada baseline desde workflow vazio:

```text
new_workflow
→ StartSearch
→ adapter in-memory Cloudbeds ou Bókun
→ LookupRecorded
→ OfferChosen
→ DraftRequested
→ prepare_summary
→ SummaryRecorded
→ classify_and_bind
→ ConfirmationReceived
→ reduce
```

Ele não injeta `AwaitingConfirmationState`, offer selecionada, assinatura ou
versão canônica diretamente como condição de partida.

## Extensão da FSM

### Novo estado

```text
AwaitingAdjustmentState
- meta
- draft
- summary
- decision: ConfirmationRecord(decision=adjust)
```

Novo phase tag: `awaiting_adjustment`.

### Transições

```text
AwaitingConfirmation + ACCEPT
→ ExecutionQueued + exatamente um command

AwaitingConfirmation + REJECT
→ Cancelled + zero commands

AwaitingConfirmation + AMBIGUOUS
→ AwaitingConfirmation + zero commands

AwaitingConfirmation + ADJUST
→ AwaitingAdjustment + zero commands

AwaitingAdjustment + DraftAdjusted(economic change)
→ ReadyToSummarize(version + 1) + zero commands

AwaitingAdjustment + ACCEPT/old confirmation
→ ignored + zero commands
```

`DraftAdjusted` também continua permitido diretamente em
`ReadyToSummarizeState` e `AwaitingConfirmationState`, desde que altere o assunto
semântico. Ajuste idêntico é rejeitado e não incrementa versão.

A matriz passa a ter 16 estados, 12 eventos e 192 pares explícitos. Nenhum evento
novo é necessário.

## Invariantes de autorização

1. antes de `SummaryRecorded`: zero command;
2. no mesmo timestamp do resumo: zero command;
3. sem contexto tipado vigente: zero command;
4. summary version/signature divergente: zero command;
5. somente `ACCEPT`, posterior e vinculado ao summary vigente emite command;
6. `REJECT`, `ADJUST` e `AMBIGUOUS`: zero command;
7. `ADJUST` desarma imediatamente a versão antiga;
8. alteração semântica incrementa versão exatamente uma vez;
9. no-op adjustment não cria versão;
10. nova versão exige novo resumo e nova confirmação posterior;
11. confirmação da versão anterior nunca autoriza a nova;
12. duplicata exata não reemite command;
13. payload divergente com mesmo event ID falha fechado;
14. um workflow continua limitado a um command;
15. nenhum componente desta fase executa provider, rede ou entrega.

## Corpus sintético

Arquivo:

```text
tests/fixtures/phase4/confirmation-corpus.json
```

Cada caso contém apenas:

- `case_id`;
- locale;
- category;
- mensagem sintética;
- contexto presente/ausente;
- decisão esperada;
- se pode produzir evento tipado.

O corpus inclui PT/EN balanceado para as seis categorias. Não conterá mensagem,
telefone, subscriber ID, e-mail ou payload real.

## Replays obrigatórios

1. explícito: “Sim, confirmo exatamente esse resumo.” / “I confirm this exact
   summary.”;
2. coloquial: “fechado, pode seguir” / “sounds good, go ahead”;
3. contextual: “pode fazer” / “go ahead”, apenas após contexto tipado;
4. negativo: “não confirme” / “do not book it”;
5. ambíguo: pergunta, texto vazio, sinais mistos ou contexto ausente;
6. ajuste: “troque para cartão” / “change it to card”.

Cada replay atravessa o renderer, o binder e o reducer. Aceites válidos produzem
um command; todos os demais produzem zero.

## Property gate

Modo gate:

- mínimo 50.000 casos;
- seed `20260719`;
- abaixo do mínimo somente `--smoke`;
- ambos adapters in-memory com contadores positivos;
- PT e EN com contadores positivos;
- todas as seis categorias com contadores positivos.

Contadores obrigatórios:

- deterministic summaries;
- summaries sem private field/claim;
- posterior accepts;
- pre-summary/same-time/stale-version rejections;
- contextual accepts com contexto;
- context-free ambiguous;
- negative/ambiguous/adjust zero-command;
- adjustment disarms old summary;
- semantic adjustment creates version;
- no-op adjustment rejections;
- duplicate zero additional commands;
- classifier exceptions fail closed;
- unexpected exceptions, false commands e missing required commands iguais a
  zero.

Como a FSM muda, o gate regressivo da Fase 2 será executado novamente com
100.000 sequências × 20 eventos e suas evidências serão regeneradas.

## Mutation gate

Catálogo fechado e reproduzível em cópias temporárias, incluindo no mínimo:

- inserir `provider_ref` no resumo;
- omitir método de pagamento do resumo;
- afirmar “reserva confirmada”;
- aceitar contextual sem contexto;
- avaliar token de aceite antes da negação;
- aceitar classifier exception;
- usar target fornecido pelo classifier;
- permitir confirmação no mesmo timestamp;
- aceitar versão/signature antiga;
- manter resumo antigo armado após `ADJUST`;
- permitir no-op adjustment incrementar versão;
- suprimir command obrigatório;
- emitir command para negativo/ambíguo;
- reemitir command em duplicata;
- reduzir workload abaixo do gate.

Todos devem morrer. O CI regenera `mutation-result.json` e exige diff vazio.

## Validação e CI

O validador da Fase 4 exigirá:

- arquivos obrigatórios tracked/staged;
- package sem rede, env, filesystem, subprocess, provider SDK, legado ou LLM;
- `ReservationCommand` com owner único no reducer;
- `ConfirmationReceived` de texto natural construído somente pelo trusted
  binder no código de produção;
- renderer sem private fields/claims no corpus;
- corpus sintético e sanitizado;
- properties e mutations reproduzíveis;
- manifests e SHA-256;
- matriz/domínio da Fase 2 regenerados;
- validadores 0–3 e regressões integrais;
- workflow `.github/workflows/phase4.yml`;
- `git diff --check`, compileall, scans e CI.

## Fonte somente leitura

O design foi informado sem execução/modificação por:

- incidentes F01, F02 e F08 da caracterização;
- `domain/hermes_native_runner.py::_prepare_single_service_confirmation_reply`;
- `domain/hermes_native_runner.py::_single_service_confirmation_phase_from_state`;
- `domain/tool_executor.py::reservation_write_confirmation_guard_action`.

Problemas deliberadamente não herdados:

- phase em metadata paralela;
- texto “Total confirmado” antes de qualquer efeito;
- model-owned summary sem artefato persistível;
- boolean de confirmação no payload de write;
- segunda rodada de confirmação após aceite.

O legado permanece no HEAD
`57408d8b2040399bc25ee7957505208079458884`, status canônico com 80 entradas e
SHA-256
`77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b`.

## Fora do escopo

- LLM/Hermes real, fallback de modelo ou benchmark live;
- ManyChat, WhatsApp, delivery ack ou outbox persistente;
- banco, transação, ledger, worker ou lease;
- provider read/write, autenticação, rede ou credenciais;
- extração de novos fatos econômicos do texto de ajuste;
- retry, execução ou `ExecutionOutcome`;
- runner/plugin/executor;
- deploy, shadow, canary ou rollout;
- Fase 5.

## Rollback

Reverter os commits da Fase 4 no repositório novo. Não existe ação live,
migração de banco, mensagem, provider call ou efeito comercial a desfazer.

## Gate de saída

A fase só fecha quando:

1. design/plano/REDs/implementação/evidências existem;
2. todos os replays obrigatórios passam desde workflow vazio;
3. renderer é determinístico e não expõe private fields/claims;
4. aceite válido gera exatamente um command;
5. todas as outras decisões geram zero;
6. alteração gera nova versão e desarma a anterior;
7. duplicates/stale/out-of-order permanecem fail-closed;
8. 50 mil properties e catálogo de mutantes passam;
9. gate regressivo da Fase 2 passa com 100 mil × 20;
10. validadores 0–4, manifests, hashes, scans e CI passam;
11. `HEAD == origin/main == remote` no closeout;
12. rollout comercial permanece `NO-GO`.
