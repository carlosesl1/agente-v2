# Contrato do domínio puro — Fase 2

## Limite arquitetural

```text
ConversationIntent tipado (futuro boundary)
→ eventos do domínio
→ reducer puro
→ estado imutável + ReservationCommand
```

Nenhum objeto desta fase executa I/O. `ReservationCommand` é somente a decisão
persistível produzida após autorização completa.

## Estados discriminados

```text
collecting
searching
offered
selected
ready_to_summarize
awaiting_confirmation
execution_queued
executing
succeeded
failed_before_provider
failed_no_effect
uncertain
manual_review
cancelled
expired
```

A separação entre `failed_before_provider` e `failed_no_effect` evita colapsar:

```text
provider comprovadamente não chamado
!=
provider chamado e comprovadamente sem efeito
```

## Eventos discriminados

```text
start_search
lookup_recorded
offer_chosen
draft_requested
draft_adjusted
summary_recorded
confirmation_received
execution_started
execution_finished
manual_review_requested
workflow_cancelled
workflow_expired
```

## Autorização de comando

O único caminho que emite comando é:

```text
AwaitingConfirmationState
+ ConfirmationReceived(decision=accept)
+ mesma draft_version
+ mesma subject_signature
+ occurred_at > SummaryPresented.presented_at
→ ExecutionQueuedState + exatamente um ReservationCommand
```

Qualquer outro par estado/evento emite zero comandos. O ID e a idempotency key
do comando são derivados deterministicamente de:

```text
workflow_id
draft_id
draft_version
subject_signature
operation
```

## Assinatura canônica

Incluídos:

- `offer_id`;
- service e provider ref;
- datas e horário;
- adultos e crianças;
- total e moeda;
- disponibilidade autorizadora;
- customer ref, nome, e-mail, telefone E.164 e país;
- método de pagamento;
- código, quantidade, preço unitário e moeda de cada adicional.

Excluídos intencionalmente:

- label pública;
- `lookup_id` e texto de apresentação;
- ordem de componentes/adicionais;
- IDs de resumo/outbox;
- timestamps e versão do draft.

Mudança econômica altera a assinatura; apresentação/provenance equivalente não.
A assinatura é um digest semântico determinístico, não MAC nem assinatura
digital; autenticidade e autorização da store pertencem à Fase 5.

## Evidência temporal

`LookupEvidence` contém:

```text
lookup_id
service
query_signature
observed_at
expires_at
snapshot_hash
status
```

A oferta só entra em `OfferedState` quando a evidência é positiva, fresca,
vinculada à mesma query e todas as ofertas correspondem ao service, período,
horário e party. `OfferChosen` revalida frescor e busca exatamente um
`offer_id`.

Frescor usa o intervalo semiaberto `[observed_at, expires_at)`: exatamente em
`expires_at`, a evidência já está vencida e falha fechada.

## Idempotência e ordem

`StateMeta` mantém:

```text
workflow_id
revision
last_event_at
seen_event_ids[]
seen_event_hashes[]
command_ids[]
```

- evento duplicado: no-op exato;
- mesmo event ID com payload divergente: rejeitado sem mutação;
- evento novo atrasado: somente metadata auditável avança; estado comercial é
  preservado, status é `rejected` e zero comandos são emitidos;
- evento não aplicável: registrado e ignorado;
- após command: nenhuma confirmação ou reset cria outro command;
- não existe retry de execução nesta fase.

## Outcome

Ordem monotônica de agregação:

```text
called_unknown
> effect_confirmed
> called_no_effect
> not_called
```

`called_unknown` conduz a `uncertain` e somente
`manual_review_requested` conduz a `manual_review`.

## Serialização

Envelope:

```json
{
  "schema_version": 1,
  "type": "tag_discriminada",
  "data": {}
}
```

O decoder exige conjunto exato de campos em todos os níveis e rejeita:

- versão desconhecida ou numericamente equivalente com tipo JSON incorreto;
- tag desconhecida;
- campo ausente;
- campo adicional;
- chave JSON duplicada em qualquer profundidade;
- tipo JSON incompatível;
- subclasses fora do universo fechado;
- datas, timestamps ou decimais em forma não canônica;
- value object inválido.

Além da forma, ele recompõe a assinatura, a identidade/idempotency key do
comando e as ligações draft → resumo → confirmação → comando. Combinações
cruzadas ou adulteradas falham fechadas.

## Gate property-based

O modo padrão é gate e exige pelo menos `100000 × 20`; workloads menores só
são permitidos com `--smoke`. O oráculo verifica bilateralmente:

- todo aceite novo, posterior e compatível produz exatamente um comando com o
  payload do draft autorizado;
- qualquer outro caso produz zero comandos;
- probes fora de ordem são rejeitados sem mutação comercial;
- colisões de event ID, duplicatas e segundo comando permanecem em zero;
- lookup positivo, negativo, expirado, indisponível e multi-oferta possuem
  cobertura positiva registrada.

## Limites da prova

Esta fase prova a FSM pura. Não prova adapters, renderer, classificação do modelo,
persistência transacional, worker, provider, outbox, ManyChat ou rollout.
