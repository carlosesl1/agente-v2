# Fase 6 — Handoff e pagamentos separados — Design

- Data: 2026-07-19
- Status: aprovado por Carlos; plano TDD aprovado; implementação iniciada em TDD
- Base imutável da fase: `6c65c2612aefce4b217dcd0308e33dd68e1dc7db`
- Branch: `phase6-handoff-payments`
- Rollout: `NO-GO`

## 1. Objetivo

Separar atendimento humano e financeiro do ciclo de reserva já concluído, sem
permitir que falha, retry, alteração de método, mensagem ou notificação crie,
reabra ou repita um `ReservationCommand`.

A Fase 6 entrega dois workflows irmãos:

1. `HandoffWorkflow`, que pode nascer de uma decisão estruturada de handoff ou
   de revisão manual;
2. `PaymentWorkflow`, que somente pode nascer de uma reserva cujo outcome
   canônico seja `ExecutionCertainty.EFFECT_CONFIRMED`.

Os workflows compartilham apenas primitivas operacionais comprovadas —
transação, lease, fencing, hash canônico e execução one-shot — mas possuem
estado, comandos, ledgers, claims e outboxes próprios.

## 2. Decisões confirmadas

1. `PaymentWorkflow` só nasce depois de `effect_confirmed`.
2. Handoff considera obrigatórios:
   - estado/fila durável de atendimento humano;
   - resposta pública via outbox durável.
3. E-mail interno de handoff é opcional, desativável e não bloqueia o cliente.
4. Pix, Wise e Stripe são evidências/eventos financeiros distintos; um método
   nunca entra pelo contrato do outro.
5. Troca de método sem alteração econômica não reabre a reserva.
6. Alteração de valor, moeda, recebedor, unidade de negócio ou target cria nova
   versão financeira e exige nova confirmação financeira; nunca cria novo
   comando de reserva.
7. Nenhuma integração live ou migração de runtime pertence à Fase 6.

## 3. Alternativas rejeitadas

### 3.1 Ampliar a FSM de reserva

Rejeitada porque permitiria que handoff, e-mail, comprovante ou settlement
modificassem o lifecycle que já terminou em `effect_confirmed`. Isso recriaria o
acoplamento que a fase deve remover.

### 3.2 Motor genérico de side effects

Rejeitado por YAGNI. Um DAG configurável ou DSL de efeitos aumentaria a
superfície de schema, mutations e false-greens sem um terceiro workflow que
justifique a abstração.

## 4. Ownership

| Componente | Decide | Não decide |
|---|---|---|
| Maya/Hermes | intenção semântica de pedir handoff, escolher método ou fornecer fatos | autorização financeira, target, recebedor, valor, retry, idempotência |
| Reservation kernel | reserva, versão comercial e `ReservationCommand` | handoff, settlement ou mensagem financeira |
| `HandoffReducer` | lifecycle do incidente e efeitos exigidos pela política | texto livre, SMTP, ManyChat ou reserva |
| `PaymentReducer` | versão financeira, método, evidência, command e outcome | reserva, lookup, interpretação livre ou credenciais |
| Store | atomicidade, optimistic revision, uniqueness, lease e claims globais | regra comercial ou retry após incerteza |
| Handoff worker | entrega de um efeito de handoff já decidido | decidir rota ou reabrir confirmação |
| Settlement worker | claim, fence, um slot e gravação do outcome financeiro | criar reserva ou aceitar comprovante livre |
| Payment outbox worker | efeitos posteriores ao settlement já decidido | repetir settlement |
| Ports externos futuros | adaptar schema técnico e devolver resultado tipado | autorização, método alternativo ou retry cego |

## 5. Fronteiras de package

A implementação usará um package puro `reservation_followup` com módulos
focados:

```text
reservation_followup/
  types.py            # DTOs/enums fechados compartilhados
  handoff.py          # HandoffState/Event/Reducer/Policy
  payment.py          # PaymentState/Event/Reducer/evidence validation
  projection.py       # comandos e payloads canônicos privados/públicos
  schema.py           # DDL SQLite/PostgreSQL de contrato comum
  sqlite_store.py     # UoW, ledgers, claims e outboxes separados
  workers.py          # workers one-shot e ports caller-supplied
  properties.py       # properties cross-workflow
```

Não haverá import de HTTP, SDK de provider, env/auth, Hermes, ManyChat, SMTP,
Stripe, Wise, Cloudbeds, Bókun, Redis, Supabase ou subprocesso no package.

## 6. Âncora de reserva confirmada

### 6.1 `ConfirmedReservationAnchor`

O bootstrap financeiro recebe uma âncora imutável criada somente a partir do
estado canônico e outcome persistido:

```text
reservation_workflow_id
reservation_command_id
reservation_subject_signature
reservation_outcome_hash
provider_reference
service: lodging | activity | package
business_unit: hostel | agency
payment_target_id
amount_minor
currency
receiver_profile_id
confirmed_at
payment_deadline?
```

Regras:

- outcome precisa ser do tipo exato `ExecutionOutcome`;
- certainty precisa ser `effect_confirmed`;
- command, workflow, subject signature e provider reference precisam coincidir
  com o estado/outcome persistidos;
- `payment_target_id` é identidade técnica privada, nunca label ou nome público;
- valor, moeda e recebedor vêm de estado/política confiável, nunca de texto do
  lead ou escolha livre da LLM;
- uma âncora divergente para a mesma reserva falha fechado.

`called_unknown`, `called_no_effect` e `not_called` não podem produzir
`PaymentWorkflow`.

## 7. HandoffWorkflow

### 7.1 Abertura

Handoff pode nascer antes ou depois de uma reserva por evento estruturado
`HandoffRequested`. A autorização não depende de palavras/substrings no texto.
O evento carrega:

```text
handoff_id
lead_key_hash
incident_key
reason_code
source_event_id
reservation_anchor?       # somente se houver vínculo comprovado
requested_at
```

`reason_code` é diagnóstico fechado; não é obtido por matching lexical no
reducer.

### 7.2 Estados

```text
requested
active
acknowledgement_pending
acknowledged
manual_review
completed
cancelled
```

A transação de abertura persiste atomicamente:

1. workflow/evento;
2. estado `active` de fila humana;
3. outbox obrigatória de acknowledgement público;
4. outbox de e-mail somente quando habilitada;
5. idempotências por `handoff_id`, `incident_key` e efeito.

O handoff fica ativo quando a fila/estado é persistida; não espera e-mail. Fica
`acknowledged` quando a mensagem pública obrigatória tem receipt. O e-mail pode
estar pendente, desativado ou falhar sem regredir o estado público.

### 7.3 Precedência pública

No turno em que handoff é terminal:

```text
private/safety leak rewrite
→ provider outcome confirmado
→ handoff terminal
→ perguntas de confirmação/missing slots antigas
```

Um handoff terminal suprime perguntas antigas de confirmação. A resposta declara
sem ambiguidade se a reserva foi ou não criada, usando somente a âncora/outcome
canônico.

### 7.4 Política de efeitos

`HandoffEffectPolicy` é fechada e validada:

- `queue_state`: obrigatório e sempre habilitado;
- `customer_acknowledgement`: obrigatório e sempre habilitado;
- `internal_email`: opcional e desativado por padrão;
- um efeito não pode ser simultaneamente required/optional/disabled;
- configuração que desabilita efeito obrigatório falha no startup/constructor.

Replay idêntico é no-op. Mesmo `incident_key` com payload divergente vai para
conflito/manual review; nunca sobrescreve o incidente.

## 8. PaymentWorkflow

### 8.1 Estados

```text
awaiting_method
awaiting_financial_confirmation
awaiting_evidence
evidence_verified
settlement_queued
settling
paid
retryable
manual_review
expired
cancelled
```

### 8.2 Sujeito financeiro

`PaymentSubject` inclui:

```text
payment_id
payment_version
confirmed_reservation_anchor
amount_minor
currency
receiver_profile_id
business_unit
payment_target_id
method?
economic_signature
```

A `economic_signature` inclui target, business unit, amount, currency,
receiver profile e fatos econômicos aprovados. Não inclui descrição, instrução
de pagamento, display label, comprovante bruto ou texto do modelo.

Trocar somente `method` preserva a assinatura econômica e não reabre a reserva.
Se o método introduzir taxa/desconto e mudar o total, isso é alteração econômica:
nova `payment_version`, novo resumo financeiro e confirmação financeira.

### 8.3 Confirmação financeira

A confirmação financeira é independente da confirmação da reserva. Ela só é
necessária para uma nova versão econômica. Deve seguir o mesmo princípio:

```text
resumo financeiro determinístico persistido
→ confirmação natural posterior da mesma payment_version/signature
→ zero ou um PaymentSettlementCommand
```

Ela nunca emite `ReservationCommand`.

## 9. Evidências por método

### 9.1 Pix

Pix usa `PixVisualEvidence`, não evidência bancária:

```text
proof_amount_minor
proof_currency
proof_receiver_profile_id
proof_status
normalized_e2e
observed_at
extractor_id
extractor_version
evidence_hash
```

Validação obrigatória:

1. valor e moeda iguais ao sujeito financeiro;
2. receiver profile exatamente igual ao perfil oficial da unidade;
3. status em conjunto fechado de concluído/pago, nunca pending/scheduled;
4. E2E normalizado, não placeholder, com entropia/formato mínimos;
5. hash canônico íntegro;
6. campos ausentes, ambíguos ou divergentes falham fechado.

O sistema pode aceitar a prova visual como risco comercial autorizado, mas
mensagens/evidências nunca a chamam de confirmação bancária.

### 9.2 Wise

Wise usa somente `VerifiedWiseCredit` produzido por um verificador confiável:

```text
signer_profile_id
account_profile_id
amount_minor
currency
credited_at
transaction_fingerprint
payer_fingerprint?
reference_fingerprint?
signature_verified
verification_hash
```

`signature_verified` precisa ser booleano verdadeiro e vinculado ao
`verification_hash`. Créditos sem assinatura válida, antigos, de outra conta,
ambíguos ou fora da janela configurada não autorizam settlement.

### 9.3 Stripe

Stripe usa somente `VerifiedStripeEvent`:

```text
stripe_account_profile_id
event_id
payment_intent_fingerprint
amount_minor
currency
event_type
signature_verified
observed_at
verification_hash
```

O evento deve pertencer à conta/unidade e ao target esperados. Um caller não
pode declarar Stripe pelo schema Pix nem fornecer booleanos genéricos para
contornar assinatura.

## 10. Claims globais de evidência

A tabela `payment_evidence_claims` tem uniqueness global por identidade
econômica:

```text
pix:<normalized_e2e>
wise:<transaction_fingerprint>
stripe:<account_profile_id>:<event_id>
```

O claim é independente de target, reserva, unidade e idempotency key fornecida
pelo caller. A mesma evidência não pode financiar dois targets, mesmo com IDs de
workflow ou chaves diferentes.

Lifecycle:

```text
in_progress
completed
retryable
manual_review
```

Somente falha provada antes de qualquer dispatch financeiro pode ficar
`retryable`. Dispatch possível, timeout, partial write ou retorno inválido fica
`manual_review`. `completed`, `in_progress` e `manual_review` bloqueiam replay
automático.

## 11. Command e ledger financeiro

### 11.1 `PaymentSettlementCommand`

Imutável e criado somente pelo `PaymentReducer`:

```text
settlement_command_id
payment_id
payment_version
economic_signature
evidence_claim_key
operation
idempotency_key
canonical_payload
```

Mesmo ID/key com payload divergente falha fechado. Um sujeito financeiro
confirmado consome no máximo um slot de dispatch.

### 11.2 `SettlementOutcome`

```text
certainty:
  not_dispatched
  dispatched_no_effect
  settled
  partial_settlement
  dispatched_unknown
payment_registered
reservation_target_confirmed
provider_reference_fingerprint?
requires_reconciliation
claim_evidence
```

Invariantes:

- `settled` exige payment registered e target confirmado;
- `partial_settlement` e `dispatched_unknown` exigem reconciliation/manual;
- outcome incerto nunca vira `not_dispatched` por replay;
- outcome, payment state, eventos e payment outboxes persistem no mesmo commit;
- falha após fencing nunca volta para retry automático.

## 12. Efeitos pós-pagamento

Settlement e efeitos posteriores são transações logicamente separadas, mas o
commit do outcome deve persistir duravelmente os jobs exigidos.

Ownership entre tasks: a Task 9 define somente o envelope/projeção imutável e
insere os jobs `pending` no mesmo commit do outcome. A Task 10 possui claim,
lease, delivery, receipts e `PaymentOutboxWorker`; ela não cria nem repete
settlement para reparar um job ausente.

`PaymentEffectPolicy` classifica explicitamente, por
`business_unit × service × method`:

- `paid_state_transition`: obrigatório;
- `customer_payment_confirmation`: obrigatório;
- `internal_payment_email`: opcional;
- `booking_form`: required/optional/disabled explícito, sem default implícito.

Configuração incompleta para uma combinação habilitada falha fechado.

Cada job possui chave baseada em settlement + effect kind, payload imutável,
hash, lease/fencing e receipt. Falha de form/e-mail/mensagem/paid-state nunca
repete settlement. Job obrigatório ausente ou divergente falha fechado; delivery
ou redelivery nunca recria job nem repete settlement.

A transição paid é monotônica: evento antigo não regressa `paid` para pending.
Target ausente ou ambíguo vai para manual review.

## 13. Persistência

### 13.1 SQLite executável

SQLite file-backed é a prova local. O schema terá, no mínimo:

```text
handoff_workflows
handoff_events
handoff_outbox
handoff_receipts
payment_workflows
payment_events
payment_evidence_claims
payment_commands
payment_ledger
payment_outbox
payment_receipts
```

Cada domínio tem revision otimista, uniques, hashes canônicos, lease e fencing
próprios. Handoff não escreve payment ledger; payment não escreve reservation
ledger; nenhuma outbox altera ledger comercial.

### 13.2 PostgreSQL estático

DDL PostgreSQL será gerado do mesmo contrato, mas não será executado. Nenhum
claim de equivalência de locking/produção será feito. R51 permanece aberto até
prova PostgreSQL antes de migração/canary.

## 14. Workers e ports

Workers são one-shot, sem loop/sleep e sem adapter default:

```text
HandoffOutboxWorker.run_once(now)
PaymentSettlementWorker.run_once(now)
PaymentOutboxWorker.run_once(now)
PaymentReconciler.run_once(now)
```

Ports são caller-supplied e expõem identidade/versão:

```text
HandoffDeliveryPort
SettlementPort
PaymentEffectDeliveryPort
```

O reconciler não recebe `SettlementPort`. Claims pré-fence podem ser liberados;
pós-fence vão para unknown/manual review.

## 15. Atomicidade

Transações obrigatórias:

1. abrir handoff + fila ativa + outboxes;
2. criar payment workflow a partir de anchor confirmada;
3. aceitar evidência + claim global + command + ledger;
4. fence financeiro antes do dispatch;
5. outcome + payment state + eventos + payment outboxes;
6. receipt de cada outbox sem tocar outros ledgers.

Fault em qualquer statement antes do commit deixa zero estado parcial após
reopen.

## 16. Idempotência e conflito

- replay idêntico é no-op;
- replay divergente para a mesma identidade falha fechado;
- keys são derivadas de identidades canônicas, não labels;
- duplicata de webhook/turno não cria outro workflow, command, claim ou job;
- provas globais não podem ser reatribuídas;
- owner/token stale não renova, libera, fenceia, grava outcome ou receipt;
- `now == expires_at` é stale para owner atual e elegível para reclaim somente
  antes do fence correspondente.

## 17. Segurança e privacidade

- nenhum segredo, instrução de pagamento factual, receiver real, PII,
  comprovante, payload provider ou mensagem real entra no Git;
- receiver/account profiles são IDs opacos fornecidos por configuração
  confiável;
- instruções de Pix/Wise/Stripe continuam em superfícies comportamentais
  próprias; o workflow consome somente fatos estruturados;
- outputs públicos omitem IDs privados, fingerprints, hashes, status internos e
  detalhes de reconciliação;
- scanners continuam proibindo tokens, PII, links não autorizados, DB/WAL/SHM e
  logs;
- nenhuma regra mecânica roteia ou autoriza por termo isolado do lead.

## 18. Provas e gates

### 18.1 Unit/reducer

- tabela total de estados/eventos para ambos workflows;
- DTOs fechados, serialização canônica e round-trip hostil;
- handoff terminal suprime confirmação antiga;
- e-mail desativado não bloqueia ack/fila;
- payment bootstrap rejeita todo outcome diferente de `effect_confirmed`;
- método muda sem ReservationCommand;
- mudança econômica exige nova versão/confirm financeira.

### 18.2 Properties cross-phase

Gate mínimo: 20.000 casos, seed fixo, divididos igualmente entre handoff e
payment; ambos serviços, unidades e métodos precisam de counters positivos.
Oráculos bilaterais:

```text
reservation_commands_after_anchor == 0
handoff_email_failures_do_not_block_required == 0
second_settlement_commands == 0
second_dispatch_slots == 0
proof_reuses == 0
outbox_settlement_retries == 0
unknown_automatic_retries == 0
partial_transactions == 0
wrong_target_settlements == 0
```

### 18.3 Fault/restart/contention

- falhar cada statement e fronteira de handoff, evidence claim, command, fence,
  dispatch, outcome e outbox;
- no mínimo 2.000 restart schedules;
- 50 contention rounds por domínio crítico: handoff incident, payment command,
  global evidence claim e payment outbox;
- processos reais e SQLite temporário;
- logs de teste sintéticos e não versionados.

### 18.4 Mutations

Catálogo fechado deve matar mutantes que:

- aceitam payment antes de effect_confirmed;
- deixam e-mail bloquear handoff;
- deixam confirmação antiga aparecer após handoff;
- tratam Stripe/Wise como Pix;
- removem claim global de E2E/event/credit;
- aceitam receiver/amount/currency divergente;
- permitem segundo settlement slot;
- tornam post-fence retryable;
- fazem outbox reabrir ledger;
- regressam paid para pending;
- aceitam config required/disabled;
- permitem payload divergente sob mesma key.

Loader/import error, timeout ou protocolo inválido não contam como kill.

### 18.5 Closeout

- suíte completa;
- properties integrais;
- fault/restart/contention integrais;
- catálogo integral de mutations;
- validators 0–6;
- manifests/schema/checksums;
- revisão independente financeira e de handoff;
- CI em jobs paralelos com budget executável;
- commit publicado e SHA remoto conferido.

## 19. Escopo proibido

A Fase 6 não:

- edita `/home/ubuntu/chapada-leads-hermes`;
- integra runner/plugin/executor ou ManyChat;
- envia WhatsApp/e-mail/form;
- lê conta Wise/Stripe/banco;
- valida comprovante real;
- chama Cloudbeds/Bókun;
- executa PostgreSQL/Supabase/Redis/Docker;
- faz deploy, shadow, canary ou rollout;
- inicia a Fase 7.

## 20. Rollback

Como não há capability live, rollback consiste em reverter apenas os commits da
Fase 6 no repositório novo. Nenhum provider effect, mensagem, pagamento, handoff
ou deploy existirá para desfazer.

## 21. Critério de conclusão

A Fase 6 só será concluída quando os dois workflows e seus gates estiverem
fechados, os riscos atualizados, a revisão independente não tiver finding
Critical/Important, o commit estiver publicado e o CI remoto estiver verde.
Rollout permanece `NO-GO`; a Fase 7 não começa automaticamente.
