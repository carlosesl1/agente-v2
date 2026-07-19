# Fase 6 — Handoff e pagamentos separados — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar `HandoffWorkflow` e `PaymentWorkflow` como workflows irmãos, duráveis e independentes da reserva, provando handoff obrigatório sem dependência de e-mail e settlement financeiro exactly-once somente após `effect_confirmed`.

**Architecture:** Um package puro `reservation_followup` possui DTOs fechados, reducers, projeções, DDL comum SQLite/PostgreSQL, UnitOfWork SQLite e workers one-shot com ports injetados. Handoff e pagamento usam tabelas, ledgers, claims e outboxes separados; apenas primitivas operacionais são compartilhadas. Nenhum componente cria ou modifica `ReservationCommand`.

**Tech Stack:** Python 3.12 stdlib, `dataclasses`, `enum`, `json`, `hashlib`, `sqlite3`, `unittest`, `multiprocessing`; SQLite local executável e PostgreSQL apenas DDL estático.

## Global Constraints

- Base imutável da fase: `6c65c2612aefce4b217dcd0308e33dd68e1dc7db`.
- Branch/worktree: `phase6-handoff-payments` em `.worktrees/phase6-handoff-payments`.
- `PaymentWorkflow` só nasce depois de `ExecutionCertainty.EFFECT_CONFIRMED`.
- Handoff exige `queue_state` e `customer_acknowledgement`; `internal_email` é opcional e desativado por padrão.
- Handoff terminal suprime confirmação/missing-slots antigos na resposta pública.
- Troca de método sem alteração econômica não cria `ReservationCommand` nem reabre a reserva.
- Alteração de amount/currency/receiver/business-unit/target cria nova versão e confirmação financeira, nunca novo comando de reserva.
- Pix, Wise e Stripe possuem evidence types e claim keys distintos; nenhum método entra pelo schema de outro.
- Pix visual é evidência comercial aceita, nunca claim de confirmação bancária.
- Claims globais independem de target, unidade e caller idempotency key.
- Um sujeito financeiro confirmado consome no máximo um slot de settlement.
- Falha comprovadamente pré-dispatch pode ter retry finito; falha pós-fence, partial ou unknown vai para manual review/reconciliation.
- Falha de outbox, formulário, e-mail ou paid-state nunca repete settlement.
- SQLite file-backed é executável; PostgreSQL é somente DDL estático/regenerável e não será executado.
- Python stdlib e `unittest`; nenhuma dependência externa nova.
- Workers e reconciler são one-shot, sem loop/sleep e sem adapter externo default.
- `/home/ubuntu/chapada-leads-hermes` permanece estritamente somente leitura.
- Não executar Hermes, LLM, ManyChat, SMTP, Stripe, Wise, Pix/banco, Cloudbeds, Bókun, provider, delivery, PostgreSQL, Supabase, Redis, Docker, deploy, shadow, canary ou rollout.
- Nenhum segredo, PII, comprovante, mensagem real, payload bruto, DB/WAL/SHM ou log entra no Git.
- Nenhuma autorização ou roteamento mecânico depende de palavra/substrings do lead.
- Gate integral: 20.000 properties, seed fechado; 2.000 restart schedules; 50 contention rounds por domínio crítico; catálogo integral de mutations.
- Rollout comercial permanece `NO-GO`; Fase 7 não começa automaticamente.

---

## File Structure

```text
reservation_followup/
  __init__.py          # API pública fechada
  types.py             # enums, DTOs e invariantes compartilhados
  serialization.py     # wire JSON fechado e hashes canônicos
  handoff.py           # policy, eventos, reducer e projeção de handoff
  payment.py           # anchors, evidence, reducer, command e outcomes
  projection.py        # payloads privados/públicos e effect jobs
  schema.py            # contrato DDL SQLite/PostgreSQL
  sqlite_store.py      # UoW, optimistic revision, ledgers, claims e outboxes
  workers.py           # ports e workers one-shot
  properties.py        # properties cross-phase
schemas/phase6/
  sqlite.sql
  postgresql.sql
scripts/
  generate_phase6_schema.py
  generate_phase6_manifest.py
  run_phase6_properties.py
  run_phase6_faults.py
  run_phase6_mutations.py
  validate_phase6.py
tests/
  phase6_helpers.py
  test_phase6_types.py
  test_phase6_handoff.py
  test_phase6_payment.py
  test_phase6_schema.py
  test_phase6_sqlite_store.py
  test_phase6_handoff_worker.py
  test_phase6_payment_worker.py
  test_phase6_payment_outbox.py
  test_phase6_properties.py
  test_phase6_fault_injection.py
  test_phase6_mutation_runner.py
  test_phase6_closeout.py
```

---

### Task 1: DTOs compartilhados, policy e serialização fechada

**Files:**
- Create: `reservation_followup/__init__.py`
- Create: `reservation_followup/types.py`
- Create: `reservation_followup/serialization.py`
- Create: `tests/test_phase6_types.py`
- Create: `tests/phase6_helpers.py`

**Interfaces:**
- Produces: `BusinessUnit`, `PaymentMethod`, `EffectRequirement`, `HandoffStatus`, `PaymentStatus`, `SettlementCertainty`, `ConfirmedReservationAnchor`, `HandoffEffectPolicy`, `PaymentEffectPolicy`, `PaymentSubject`, `to_wire_json(value) -> str`, `from_wire_json(text, expected_type)` and `semantic_hash(value) -> str`.
- Consumes: `reservation_domain.ExecutionOutcome`, `ExecutionCertainty`, `ServiceKind` and canonical identifier/hash/date rules already proved in Fases 2–5.

- [ ] **Step 1: Write closed-enum and exact-type RED tests**

```python
class Phase6SharedTypeTests(unittest.TestCase):
    def test_anchor_requires_exact_effect_confirmed_outcome(self) -> None:
        for certainty in (
            ExecutionCertainty.NOT_CALLED,
            ExecutionCertainty.CALLED_NO_EFFECT,
            ExecutionCertainty.CALLED_UNKNOWN,
        ):
            with self.subTest(certainty=certainty), self.assertRaises(ValueError):
                confirmed_anchor(outcome=outcome(certainty=certainty))

    def test_handoff_policy_requires_queue_and_customer_ack(self) -> None:
        with self.assertRaises(ValueError):
            HandoffEffectPolicy(
                queue_state=EffectRequirement.DISABLED,
                customer_acknowledgement=EffectRequirement.REQUIRED,
                internal_email=EffectRequirement.OPTIONAL,
            )

    def test_payment_policy_requires_explicit_booking_form_classification(self) -> None:
        with self.assertRaises(ValueError):
            PaymentEffectPolicy(
                paid_state_transition=EffectRequirement.REQUIRED,
                customer_payment_confirmation=EffectRequirement.REQUIRED,
                internal_payment_email=EffectRequirement.OPTIONAL,
                booking_form=None,
            )
```

- [ ] **Step 2: Run RED and preserve provenance**

Run:

```bash
python3 -m unittest tests.test_phase6_types -v >/tmp/phase6-task1-red.out 2>&1
```

Expected: nonzero exit with `ModuleNotFoundError: No module named 'reservation_followup'`. Record command, exit, failure class and SHA-256 in `docs/refactor/evidence/phase-06/red-result-types.json`; do not version raw output.

- [ ] **Step 3: Implement exact enums and policy guards**

```python
class BusinessUnit(str, Enum):
    HOSTEL = "hostel"
    AGENCY = "agency"

class PaymentMethod(str, Enum):
    PIX = "pix"
    WISE = "wise"
    STRIPE = "stripe"

class EffectRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    DISABLED = "disabled"

@dataclass(frozen=True, slots=True)
class HandoffEffectPolicy:
    queue_state: EffectRequirement
    customer_acknowledgement: EffectRequirement
    internal_email: EffectRequirement

    def __post_init__(self) -> None:
        if self.queue_state is not EffectRequirement.REQUIRED:
            raise ValueError("queue_state must be required")
        if self.customer_acknowledgement is not EffectRequirement.REQUIRED:
            raise ValueError("customer_acknowledgement must be required")
        if self.internal_email not in (
            EffectRequirement.OPTIONAL,
            EffectRequirement.DISABLED,
        ):
            raise ValueError("internal_email must be optional or disabled")
```

`ConfirmedReservationAnchor.__post_init__` must recompute outcome hash, require exact `EFFECT_CONFIRMED`, nonempty provider reference, positive amount, three-letter uppercase currency, exact business unit/service, opaque target/receiver IDs and UTC timestamps. `PaymentEffectPolicy` requires paid-state and customer confirmation, permits optional/disabled internal email, and requires exact non-null classification for booking form.

- [ ] **Step 4: Implement duplicate-key-safe canonical wire JSON**

```python
def to_wire_json(value: object) -> str:
    payload = encode_closed_dataclass(value)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def semantic_hash(value: object) -> str:
    return hashlib.sha256(to_wire_json(value).encode("utf-8")).hexdigest()
```

Decoder rules: reject duplicate keys, unknown/missing fields, bool-as-int, float-as-int, noncanonical enum strings, naive datetimes, compact/non-UTC timestamps, nonfinite numbers and unsupported schema version. Two decoded mutable-looking payloads must not share nested objects.

- [ ] **Step 5: Run focused GREEN and hostile round-trips**

Run:

```bash
python3 -m unittest tests.test_phase6_types -v
python3 -m unittest tests.test_phase2_serialization tests.test_phase5_types -v
```

Expected: all pass; Fases 2/5 unchanged.

- [ ] **Step 6: Commit and request review**

```bash
git add reservation_followup tests/phase6_helpers.py tests/test_phase6_types.py docs/refactor/evidence/phase-06/red-result-types.json
git commit -m "feat(phase-6): add closed follow-up contracts"
```

Reviewer gate: type/schema compliance first, quality second. Do not open Task 2 with Critical/Important findings.

---

### Task 2: HandoffWorkflow puro, policy e precedência terminal

**Files:**
- Create: `reservation_followup/handoff.py`
- Create: `tests/test_phase6_handoff.py`
- Modify: `reservation_followup/__init__.py`
- Modify: `reservation_followup/serialization.py`

**Interfaces:**
- Produces: `HandoffRequested`, `HandoffAcknowledged`, `HandoffEffectFailed`, `HandoffCancelled`, `HandoffWorkflow`, `HandoffTransition`, `new_handoff(event, policy)`, `reduce_handoff(state, event)` and `project_handoff_public_reply(state, reservation_outcome) -> PublicHandoffProjection`.
- Invariant: reducer accepts structured events only; no lead text input.

- [ ] **Step 1: Write RED for F18, duplicate incidents and terminal precedence**

```python
class Phase6HandoffTests(unittest.TestCase):
    def test_email_disabled_still_opens_queue_and_customer_ack(self) -> None:
        transition = new_handoff(
            handoff_requested(),
            HandoffEffectPolicy.default_email_disabled(),
        )
        self.assertEqual(transition.state.status, HandoffStatus.ACKNOWLEDGEMENT_PENDING)
        self.assertEqual(
            [job.kind for job in transition.effect_jobs],
            [HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT],
        )
        self.assertTrue(transition.state.queue_active)

    def test_terminal_handoff_suppresses_stale_confirmation_question(self) -> None:
        projection = project_handoff_public_reply(
            active_handoff(),
            reservation_outcome=None,
            stale_confirmation_question="confirmar novamente?",
        )
        self.assertNotIn("confirm", projection.public_text.casefold())
        self.assertEqual(projection.next_action, PublicNextAction.WAIT_FOR_HUMAN)
```

Add tests for identical replay no-op, divergent incident conflict, optional e-mail failure, acknowledgement receipt, unknown event fail-closed and exact state/event matrix.

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_phase6_handoff -v >/tmp/phase6-task2-red.out 2>&1
```

Expected: import failure for `reservation_followup.handoff`; preserve sanitized RED envelope.

- [ ] **Step 3: Implement closed reducer and policy-derived jobs**

```python
def new_handoff(
    event: HandoffRequested,
    policy: HandoffEffectPolicy,
) -> HandoffTransition:
    state = HandoffWorkflow.from_request(event, policy)
    jobs = [HandoffEffectJob.customer_acknowledgement(state)]
    if policy.internal_email is EffectRequirement.OPTIONAL:
        jobs.append(HandoffEffectJob.internal_email(state, required=False))
    return HandoffTransition(state=state, events=(event,), effect_jobs=tuple(jobs))
```

Transition matrix must be literal and bilateral. `internal_email` failure records its own job failure and leaves queue/ack lifecycle unchanged. Public projection order is safety rewrite → provider outcome → terminal handoff → prior follow-up; projection never exposes reason internals, IDs, hashes or stale questions.

- [ ] **Step 4: Run GREEN and domain regressions**

```bash
python3 -m unittest tests.test_phase6_handoff -v
python3 -m unittest tests.test_phase4_replays tests.test_phase5_properties -v
```

- [ ] **Step 5: Commit and review**

```bash
git add reservation_followup tests/test_phase6_handoff.py docs/refactor/evidence/phase-06/red-result-handoff.json
git commit -m "feat(phase-6): add independent handoff workflow"
```

Review must prove no lexical routing and no path where e-mail blocks queue/ack.

---

### Task 3: Payment anchor, subjects and method-specific evidence

**Files:**
- Create: `reservation_followup/payment.py`
- Create: `tests/test_phase6_payment.py`
- Modify: `reservation_followup/__init__.py`
- Modify: `reservation_followup/serialization.py`

**Interfaces:**
- Produces: `PaymentSubject`, `PixVisualEvidence`, `VerifiedWiseCredit`, `VerifiedStripeEvent`, `PaymentEvidence`, `evidence_claim_key(evidence) -> str`, `validate_evidence(subject, evidence) -> VerifiedPaymentEvidence`.
- Consumes: `ConfirmedReservationAnchor`, method/account/receiver profile IDs from trusted configuration.

- [ ] **Step 1: Write RED payment-bootstrap and cross-method tests**

```python
class Phase6PaymentEvidenceTests(unittest.TestCase):
    def test_only_effect_confirmed_anchor_can_bootstrap_payment(self) -> None:
        for certainty in ExecutionCertainty:
            if certainty is ExecutionCertainty.EFFECT_CONFIRMED:
                continue
            with self.subTest(certainty=certainty), self.assertRaises(ValueError):
                PaymentSubject.from_anchor(anchor_for(certainty))

    def test_wise_and_stripe_cannot_enter_pix_contract(self) -> None:
        subject = payment_subject(method=PaymentMethod.PIX)
        for evidence in (verified_wise_credit(), verified_stripe_event()):
            with self.subTest(evidence=type(evidence).__name__), self.assertRaises(ValueError):
                validate_evidence(subject, evidence)
```

Add matrix for Pix amount/currency/receiver/status/E2E; Wise signer/account/window/ambiguity/signature; Stripe account/event/type/signature; proof identity entropy; closed fields; no raw proof or PII.

- [ ] **Step 2: Run RED and record exact failure**

```bash
python3 -m unittest tests.test_phase6_payment.Phase6PaymentEvidenceTests -v >/tmp/phase6-task3-red.out 2>&1
```

- [ ] **Step 3: Implement exact union and claim keys**

```python
PaymentEvidence = PixVisualEvidence | VerifiedWiseCredit | VerifiedStripeEvent


def evidence_claim_key(evidence: PaymentEvidence) -> str:
    if type(evidence) is PixVisualEvidence:
        return f"pix:{evidence.normalized_e2e}"
    if type(evidence) is VerifiedWiseCredit:
        return f"wise:{evidence.transaction_fingerprint}"
    if type(evidence) is VerifiedStripeEvent:
        return f"stripe:{evidence.stripe_account_profile_id}:{evidence.event_id}"
    raise TypeError("unsupported payment evidence type")
```

Pix validation uses exact receiver profile identity and `amount_minor`, not substring/display name. Wise/Stripe require `signature_verified is True`, exact account profile, amount/currency and canonical verification hash. Claim keys never include target/idempotency key.

- [ ] **Step 4: Prove method change versus economic change**

Tests require:

```text
same anchor + same economics + PIX→WISE => same economic_signature
same target + amount+1 => different signature/payment_version
same amount + receiver change => different signature/payment_version
method-only change => zero ReservationCommand objects
```

- [ ] **Step 5: Run GREEN and hostile serialization**

```bash
python3 -m unittest tests.test_phase6_payment tests.test_phase6_types -v
```

- [ ] **Step 6: Commit and financial review**

```bash
git add reservation_followup tests/test_phase6_payment.py docs/refactor/evidence/phase-06/red-result-payment-evidence.json
git commit -m "feat(phase-6): validate isolated payment evidence"
```

Reviewer must inspect Pix wording/identity, Wise/Stripe separation and cross-target claim identity.

---

### Task 4: Payment reducer, financial confirmation, command and outcome

**Files:**
- Modify: `reservation_followup/payment.py`
- Create: `reservation_followup/projection.py`
- Create: `tests/test_phase6_payment_reducer.py`
- Modify: `reservation_followup/__init__.py`

**Interfaces:**
- Produces: `PaymentMethodSelected`, `FinancialSummaryRecorded`, `FinancialConfirmationReceived`, `PaymentEvidenceRecorded`, `SettlementStarted`, `SettlementFinished`, `PaymentExpired`, `PaymentCancelled`, `PaymentSettlementCommand`, `SettlementOutcome`, `PaymentTransition`, `new_payment(anchor, policy)`, `reduce_payment(state, event)`.
- Command owner: only `PaymentReducer` constructs `PaymentSettlementCommand`.

- [ ] **Step 1: Write RED for one financial command and no reservation command**

```python
class Phase6PaymentReducerTests(unittest.TestCase):
    def test_verified_evidence_after_financial_confirmation_emits_one_command(self) -> None:
        state = payment_after_natural_financial_confirmation()
        first = reduce_payment(state, PaymentEvidenceRecorded(pix_evidence()))
        second = reduce_payment(first.state, PaymentEvidenceRecorded(pix_evidence()))
        self.assertEqual(len(first.commands), 1)
        self.assertEqual(second.commands, ())
        self.assertIsInstance(first.commands[0], PaymentSettlementCommand)
        self.assertFalse(any(isinstance(item, ReservationCommand) for item in first.commands))
```

Add tests for method-only switch, economic version increment, stale financial confirmation, divergent evidence replay, outcome certainty matrix, `partial_settlement`/`dispatched_unknown` manual review and monotonic paid state.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_payment_reducer -v >/tmp/phase6-task4-red.out 2>&1
```

- [ ] **Step 3: Implement literal transition table and command derivation**

```python
def _settlement_command_for(
    state: PaymentWorkflow,
    evidence: VerifiedPaymentEvidence,
) -> PaymentSettlementCommand:
    payload = canonical_settlement_payload(state.subject, evidence)
    return PaymentSettlementCommand(
        settlement_command_id=stable_id("settlement", state.payment_id, state.payment_version),
        payment_id=state.payment_id,
        payment_version=state.payment_version,
        economic_signature=state.subject.economic_signature,
        evidence_claim_key=evidence.claim_key,
        operation=SettlementOperation.REGISTER_AND_CONFIRM,
        idempotency_key=stable_id("payment-idem", state.payment_id, state.payment_version),
        canonical_payload=payload,
    )
```

No handler imports or calls reservation reducer. Identical event fingerprint is no-op; divergent ID/fingerprint raises conflict before state change.

- [ ] **Step 4: Implement closed outcome projection**

`SETTLED` requires both `payment_registered=True` and `reservation_target_confirmed=True`. `PARTIAL_SETTLEMENT` and `DISPATCHED_UNKNOWN` require `requires_reconciliation=True`, transition to `MANUAL_REVIEW` and enqueue no success claim. `NOT_DISPATCHED` may become retryable only before fence.

- [ ] **Step 5: Run GREEN and reservation regressions**

```bash
python3 -m unittest tests.test_phase6_payment_reducer -v
python3 -m unittest tests.test_phase2_domain tests.test_phase4_properties tests.test_phase5_worker -v
```

- [ ] **Step 6: Commit and review**

```bash
git add reservation_followup tests/test_phase6_payment_reducer.py docs/refactor/evidence/phase-06/red-result-payment-reducer.json
git commit -m "feat(phase-6): reduce financial workflow independently"
```

---

### Task 5: Schema Phase 6 e DDL determinístico

**Files:**
- Create: `reservation_followup/schema.py`
- Create: `scripts/generate_phase6_schema.py`
- Create: `schemas/phase6/sqlite.sql`
- Create: `schemas/phase6/postgresql.sql`
- Create: `tests/test_phase6_schema.py`

**Interfaces:**
- Produces: `render_sqlite() -> str`, `render_postgresql() -> str`, `SCHEMA_VERSION = 1` for the Phase 6 package.
- Tables: exactly the eleven tables listed in the design.

- [ ] **Step 1: Write RED for tables, constraints and cross-ledger separation**

Tests require:

```text
handoff_workflows, handoff_events, handoff_outbox, handoff_receipts
payment_workflows, payment_events, payment_evidence_claims, payment_commands,
payment_ledger, payment_outbox, payment_receipts
```

Assert FK closure, unique incident/evidence/command/idempotency keys, positive fencing tokens, closed status checks, one dispatch slot, payload hashes and no FK/write path from handoff/payment outbox to reservation execution ledger.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_schema -v >/tmp/phase6-task5-red.out 2>&1
```

- [ ] **Step 3: Implement one logical schema renderer**

Use immutable table/column/check definitions and dialect emitters. PostgreSQL uses compatible types/constraints but remains `postgresql_executed=false`. SQLite enables foreign keys and strict transaction behavior.

- [ ] **Step 4: Generate tracked DDL and prove drift zero**

```bash
python3 scripts/generate_phase6_schema.py \
  --sqlite schemas/phase6/sqlite.sql \
  --postgresql schemas/phase6/postgresql.sql
python3 scripts/generate_phase6_schema.py \
  --sqlite /tmp/phase6-sqlite.sql \
  --postgresql /tmp/phase6-postgresql.sql
diff -u schemas/phase6/sqlite.sql /tmp/phase6-sqlite.sql
diff -u schemas/phase6/postgresql.sql /tmp/phase6-postgresql.sql
python3 -m unittest tests.test_phase6_schema -v
```

- [ ] **Step 5: Commit and schema review**

```bash
git add reservation_followup/schema.py scripts/generate_phase6_schema.py schemas/phase6 tests/test_phase6_schema.py docs/refactor/evidence/phase-06/red-result-schema.json
git commit -m "feat(phase-6): add separated follow-up schema"
```

---

### Task 6: SQLite UnitOfWork — atomic handoff e payment bootstrap

**Files:**
- Create: `reservation_followup/sqlite_store.py`
- Create: `tests/test_phase6_sqlite_store.py`
- Modify: `tests/phase6_helpers.py`

**Interfaces:**
- Produces: `SQLiteFollowupUnitOfWork.open(path_or_connection)`, `open_handoff`, `load_handoff`, `apply_handoff`, `open_payment`, `load_payment`, `apply_payment`.
- Atomicity: state/event/jobs in one transaction; exact replay no-op; divergence conflict.

- [ ] **Step 1: Write statement-by-statement rollback RED**

```python
class Phase6FollowupStoreTests(unittest.TestCase):
    def test_every_handoff_open_statement_fault_rolls_back_after_reopen(self) -> None:
        for fail_after in range(expected_handoff_open_statements()):
            with self.subTest(fail_after=fail_after):
                path = temporary_db_path()
                with self.assertRaises(sqlite3.DatabaseError):
                    open_handoff_with_trigger_fault(path, fail_after)
                self.assertEqual(reopen_counts(path), zero_handoff_counts())
```

Mirror for payment anchor. Add optimistic revision, tampered state/event hash, duplicate key, exact replay and divergent replay tests.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_sqlite_store -v >/tmp/phase6-task6-red.out 2>&1
```

- [ ] **Step 3: Implement explicit transactions and consistency gates**

```python
@contextmanager
def _transaction(self):
    self._connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        self._connection.rollback()
        raise
    else:
        self._connection.commit()
```

Every load recomputes canonical hashes and validates event replay/state consistency before returning. Constructors accept no external capability.

- [ ] **Step 4: Run GREEN, reopen and quick-check**

```bash
python3 -m unittest tests.test_phase6_sqlite_store -v
python3 -m unittest tests.test_phase5_sqlite_store -v
```

Every file-backed test closes/reopens and executes `PRAGMA quick_check` plus `PRAGMA foreign_key_check`.

- [ ] **Step 5: Commit and review**

```bash
git add reservation_followup/sqlite_store.py tests/test_phase6_sqlite_store.py tests/phase6_helpers.py docs/refactor/evidence/phase-06/red-result-store.json
git commit -m "feat(phase-6): persist follow-up workflows atomically"
```

---

### Task 7: Handoff outbox, claims e worker one-shot

**Files:**
- Create: `reservation_followup/workers.py`
- Create: `tests/test_phase6_handoff_worker.py`
- Modify: `reservation_followup/sqlite_store.py`
- Modify: `reservation_followup/types.py`

**Interfaces:**
- Produces: `HandoffDeliveryPort`, `HandoffOutboxClaim`, `HandoffReceipt`, `HandoffOutboxWorker.run_once(now)`; store methods `claim_handoff_outbox`, `release_handoff_outbox`, `complete_handoff_outbox`.

- [ ] **Step 1: Write RED for required/optional isolation and stale fencing**

Tests prove:

- customer ack receipt advances acknowledgement;
- internal email disabled creates no row;
- optional email failure does not regress ack/queue;
- stale owner/token/lease cannot release or complete;
- `now == expires_at` is stale;
- same receipt replay is no-op, divergent receipt conflicts;
- delivery failure touches no reservation/payment ledger.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_handoff_worker -v >/tmp/phase6-task7-red.out 2>&1
```

- [ ] **Step 3: Implement worker and separate outbox API**

```python
class HandoffOutboxWorker:
    def run_once(self, *, now: datetime) -> HandoffWorkerResult:
        claim = self._store.claim_handoff_outbox(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return HandoffWorkerResult.idle()
        try:
            receipt = self._delivery.deliver(claim.message)
        except Exception:
            self._store.release_handoff_outbox(claim, now=now)
            return HandoffWorkerResult.retryable_failure(claim.message.message_id)
        self._store.complete_handoff_outbox(claim, receipt, now=now)
        return HandoffWorkerResult.delivered(claim.message.message_id)
```

Port includes `delivery_id`/`delivery_version`; no default transport.

- [ ] **Step 4: Run GREEN and F18 witness regression**

```bash
python3 -m unittest tests.test_phase6_handoff_worker tests.test_phase6_handoff -v
python3 -m characterization.harness
```

Expected: the full immutable characterization corpus remains green; do not change
characterization behavior or source fixtures.

- [ ] **Step 5: Commit and review**

```bash
git add reservation_followup tests/test_phase6_handoff_worker.py docs/refactor/evidence/phase-06/red-result-handoff-worker.json
git commit -m "feat(phase-6): deliver handoff effects independently"
```

---

### Task 8: Global evidence claims e settlement ledger

**Files:**
- Modify: `reservation_followup/sqlite_store.py`
- Modify: `reservation_followup/types.py`
- Create: `tests/test_phase6_payment_claims.py`

**Interfaces:**
- Produces: `claim_payment_evidence`, `record_payment_command`, `claim_settlement`, `release_pre_dispatch_settlement`, `fence_settlement`, `record_settlement_outcome`, `load_evidence_claim`.
- Claim lifecycle: `in_progress`, `completed`, `retryable`, `manual_review`.

- [ ] **Step 1: Write RED for cross-target replay and one slot**

```python
class Phase6PaymentClaimTests(unittest.TestCase):
    def test_same_pix_e2e_cannot_pay_two_targets_or_business_units(self) -> None:
        store.claim_payment_evidence(payment_a(), pix_evidence(e2e="E2E123456789ABC"))
        for payment in (payment_b(), payment_other_business_unit()):
            with self.subTest(payment=payment.payment_id), self.assertRaises(ConflictError):
                store.claim_payment_evidence(payment, pix_evidence(e2e="E2E123456789ABC"))
```

Repeat for Wise fingerprint and Stripe account/event. Add same command divergent payload, two concurrent claims, stale token and slot exactly one.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_payment_claims -v >/tmp/phase6-task8-red.out 2>&1
```

- [ ] **Step 3: Implement CAS/uniques and permanent fence**

Evidence claim insert occurs atomically with command/ledger. Pre-fence release increments claim/fencing token and may return retryable within finite preparation budget. Fencing consumes slot 1 permanently. No method can set a fenced ledger back to queued/retryable.

- [ ] **Step 4: Run GREEN and integrity probes**

```bash
python3 -m unittest tests.test_phase6_payment_claims tests.test_phase6_sqlite_store -v
```

Tamper SQL for claim key, target, command hash, status and token must fail before claim/fence.

- [ ] **Step 5: Commit and independent financial review**

```bash
git add reservation_followup tests/test_phase6_payment_claims.py docs/refactor/evidence/phase-06/red-result-payment-claims.json
git commit -m "feat(phase-6): claim payment evidence globally"
```

---

### Task 9: Settlement worker e reconciliação conservadora

**Files:**
- Modify: `reservation_followup/workers.py`
- Create: `reservation_followup/reconciliation.py`
- Create: `tests/test_phase6_payment_worker.py`
- Create: `tests/test_phase6_reconciliation.py`

**Interfaces:**
- Produces: `SettlementPort.prepare(request)`, `SettlementPort.dispatch(permit)`, `PaymentSettlementWorker.run_once(now)`, `PaymentReconciler.run_once(now)`.
- Reconciler receives store only, never settlement port.

- [ ] **Step 1: Write RED for dispatch certainty matrix**

Tests cover:

```text
prepare retryable failure → pre-fence requeue, provider calls 0
prepare terminal failure → NOT_DISPATCHED terminal, provider calls 0
exception after fence → DISPATCHED_UNKNOWN, manual review
partial settlement → PARTIAL_SETTLEMENT, manual review
invalid NOT_DISPATCHED returned by dispatch → promoted to DISPATCHED_UNKNOWN
second run after unknown/partial → idle, provider delta 0
```

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_payment_worker tests.test_phase6_reconciliation -v >/tmp/phase6-task9-red.out 2>&1
```

- [ ] **Step 3: Implement preparation/fence/dispatch/outcome order**

```python
request = self._port.prepare(claim.command)
permit = self._store.fence_settlement(claim, request, now=now)
try:
    outcome = self._port.dispatch(permit)
except Exception:
    outcome = SettlementOutcome.dispatched_unknown(
        claim_evidence=permit.request_hash,
        requires_reconciliation=True,
    )
self._store.record_settlement_outcome(claim, permit, outcome, now=now)
```

Any invalid post-fence return is unknown. `NOT_DISPATCHED` is accepted only from preparation before fencing.

- [ ] **Step 4: Implement restart recovery without port**

Expired pre-fence claims may return to retryable/queued. Expired post-fence ledgers atomically become manual review with unknown outcome and required outbox jobs; never dispatch.

- [ ] **Step 5: Run GREEN and repeated-worker proof**

```bash
python3 -m unittest tests.test_phase6_payment_worker tests.test_phase6_reconciliation -v
python3 -m unittest tests.test_phase5_worker tests.test_phase5_reconciliation -v
```

- [ ] **Step 6: Commit and review**

```bash
git add reservation_followup tests/test_phase6_payment_worker.py tests/test_phase6_reconciliation.py docs/refactor/evidence/phase-06/red-result-settlement-worker.json
git commit -m "feat(phase-6): settle payments behind permanent fencing"
```

---

### Task 10: Payment effect outbox e paid-state monotônico

**Files:**
- Modify: `reservation_followup/projection.py`
- Modify: `reservation_followup/sqlite_store.py`
- Modify: `reservation_followup/workers.py`
- Create: `tests/test_phase6_payment_outbox.py`

**Interfaces:**
- Produces: `PaymentEffectKind`, `PaymentEffectJob`, `PaymentEffectDeliveryPort`, `PaymentOutboxWorker.run_once(now)` and payment outbox claim/release/complete store methods.

- [ ] **Step 1: Write RED for policy matrix and no settlement replay**

Tests prove:

- settled outcome atomically persists required paid-state + customer confirmation jobs;
- booking form follows exact required/optional/disabled policy;
- internal e-mail optional failure leaves payment paid;
- delivery failure changes no payment ledger/provider count;
- provider settled + enqueue fault can be replayed to create missing jobs without new settlement command;
- old event cannot regress paid state;
- target missing/ambiguous goes to manual review.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_payment_outbox -v >/tmp/phase6-task10-red.out 2>&1
```

- [ ] **Step 3: Implement deterministic effect projection**

```python
def required_payment_effects(
    outcome: SettlementOutcome,
    policy: PaymentEffectPolicy,
) -> tuple[PaymentEffectJob, ...]:
    if outcome.certainty is not SettlementCertainty.SETTLED:
        return (PaymentEffectJob.manual_review(outcome),)
    jobs = [
        PaymentEffectJob.paid_state(outcome, required=True),
        PaymentEffectJob.customer_confirmation(outcome, required=True),
    ]
    jobs.extend(policy_jobs(outcome, policy))
    return tuple(jobs)
```

Every key is `settlement_command_id + effect_kind`; divergent payload conflicts. Outbox methods contain no SQL reference to `payment_ledger` except read-only consistency checks.

- [ ] **Step 4: Run GREEN and bilateral ledger counters**

```bash
python3 -m unittest tests.test_phase6_payment_outbox -v
python3 -m unittest tests.test_phase5_outbox -v
```

- [ ] **Step 5: Commit and review**

```bash
git add reservation_followup tests/test_phase6_payment_outbox.py docs/refactor/evidence/phase-06/red-result-payment-outbox.json
git commit -m "feat(phase-6): persist post-payment effects independently"
```

---

### Task 11: Properties cross-phase handoff/payment

**Files:**
- Create: `reservation_followup/properties.py`
- Create: `scripts/run_phase6_properties.py`
- Create: `tests/test_phase6_properties.py`

**Interfaces:**
- Produces: `run_followup_properties(cases, seed) -> FollowupPropertyReport` and CLI with minimum 20.000 normal cases; smaller loads require `--smoke`.

- [ ] **Step 1: Write RED for nonvacuous bilateral counters**

Required positive counters:

```text
handoff_cases, payment_cases, email_disabled_cases, method_switches,
economic_version_changes, pix_cases, wise_cases, stripe_cases,
evidence_conflicts, pre_fence_recoveries, post_fence_manual_reviews,
required_effect_deliveries, optional_effect_failures
```

Safety counters are exactly those from the spec and must all equal zero. Totals must reconstruct from rows; no `passed=true` shortcut.

- [ ] **Step 2: Run RED smoke**

```bash
python3 -m unittest tests.test_phase6_properties -v >/tmp/phase6-task11-red.out 2>&1
```

- [ ] **Step 3: Implement deterministic cross-phase generation**

Each payment case starts from `new_workflow`, traverses lookup/summary/confirmation/execution in-memory to a real `effect_confirmed` anchor, then traverses PaymentWorkflow and SQLite. Handoff cases cover pre-reservation, post-success and manual-review origins. Distribution uses global index modulo a closed mode catalog and is independent of `PYTHONHASHSEED`.

- [ ] **Step 4: Add process sharding only in CLI**

`reservation_followup` imports no subprocess/concurrency capability. CLI uses deterministic nonoverlapping ranges and at most four workers. Every shard performs quick DB checks; sampled deep audits are deterministic.

- [ ] **Step 5: Run smoke and integral gate**

```bash
python3 scripts/run_phase6_properties.py --cases 160 --seed 2026071906 --smoke
python3 scripts/run_phase6_properties.py \
  --cases 20000 \
  --seed 2026071906 \
  --write docs/refactor/evidence/phase-06/property-result.json
```

Expected: 20.000/20.000, 10.000 handoff + 10.000 payment, all positive obligations exercised and all safety counters zero.

- [ ] **Step 6: Commit and performance review**

```bash
git add reservation_followup/properties.py scripts/run_phase6_properties.py tests/test_phase6_properties.py docs/refactor/evidence/phase-06/property-result.json docs/refactor/evidence/phase-06/red-result-properties.json
git commit -m "test(phase-6): prove follow-up workflow properties"
```

---

### Task 12: Fault injection, restart e contention multiprocesso

**Files:**
- Create: `scripts/run_phase6_faults.py`
- Create: `tests/test_phase6_fault_injection.py`
- Create: `tests/test_phase6_concurrency.py`

**Interfaces:**
- Produces: closed `FAULT_POINTS`, `run_fault_matrix`, `run_restart_schedules`, `run_contention`; CLI writes three deterministic envelopes.

- [ ] **Step 1: Define independent closed fault manifest in tests**

Fault groups must include:

```text
handoff: before event, state, required outbox, optional outbox, commit, claim,
delivery, receipt
payment bootstrap: before anchor, state, event, commit
claim/command: before evidence claim, command, ledger, commit
settlement: after claim, prepare, fence, during dispatch, before outcome,
before state, before outboxes, commit
payment effects: claim, delivery, receipt
```

Tests compare both directions; runner constants cannot self-certify.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_fault_injection tests.test_phase6_concurrency -v >/tmp/phase6-task12-red.out 2>&1
```

- [ ] **Step 3: Implement transaction triggers and process crashes**

Use temporary SQLite files and real child processes. Post-dispatch crash rows record:

```text
provider_calls_setup_baseline
provider_calls_after_child
provider_calls_final
provider_calls_during_recovery
```

Post-dispatch exact invariant: final minus after-child equals zero.

- [ ] **Step 4: Implement restart scheduler and four contention domains**

Seed `2026071906`, 2.000 deterministic schedules. Contention runs 50 rounds each for handoff incident, payment command, global evidence claim and payment outbox. Each round records winners, tokens, provider delta, child errors/exits, partial transactions and violations.

- [ ] **Step 5: Run focused and integral gates**

```bash
python3 -m unittest tests.test_phase6_fault_injection tests.test_phase6_concurrency -v
python3 scripts/run_phase6_faults.py \
  --seed 2026071906 \
  --restart-schedules 2000 \
  --contention-rounds 50 \
  --write-fault-matrix docs/refactor/evidence/phase-06/fault-matrix.json \
  --write-restart docs/refactor/evidence/phase-06/restart-result.json \
  --write-concurrency docs/refactor/evidence/phase-06/concurrency-result.json
```

- [ ] **Step 6: Commit and adversarial review**

```bash
git add scripts/run_phase6_faults.py tests/test_phase6_fault_injection.py tests/test_phase6_concurrency.py docs/refactor/evidence/phase-06
git commit -m "test(phase-6): prove crash and contention recovery"
```

---

### Task 13: Mutation catalog material e runner fechado

**Files:**
- Create: `scripts/run_phase6_mutations.py`
- Create: `tests/test_phase6_mutation_runner.py`
- Modify: affected Phase 6 test files only when a surviving material mutant exposes a missing oracle.

**Interfaces:**
- Produces: immutable `MUTANTS` catalog with nonempty `name/path/old/new/test`; runner validates green baseline, exact target count one and structured unittest protocol.

- [ ] **Step 1: Write RED for catalog schema and false kills**

Tests reject empty fields, `old == new`, target count not one, empty SQL replacement, loader/import error, timeout, invalid protocol and baseline failure. None count as kill.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase6_mutation_runner -v >/tmp/phase6-task13-red.out 2>&1
```

- [ ] **Step 3: Implement at least the twelve required mutant classes**

Use one or more material mutants per class from Design §18.4. Mutants run only in temporary copies. At least one mutant must target each of: handoff policy, handoff precedence, payment bootstrap, method separation, global claim, amount/receiver validation, dispatch slot, post-fence retry, outbox isolation, paid monotonicity, config closure and divergent replay.

- [ ] **Step 4: Run under multiple hash seeds and integral catalog**

```bash
PYTHONHASHSEED=1 python3 -m unittest tests.test_phase6_mutation_runner -v
PYTHONHASHSEED=777 python3 -m unittest tests.test_phase6_mutation_runner -v
python3 scripts/run_phase6_mutations.py \
  --write docs/refactor/evidence/phase-06/mutation-result.json
```

Every mutant: baseline exit 0, loader false, target count 1, mutant exit positive, killed true, error null.

- [ ] **Step 5: Commit and split review**

```bash
git add scripts/run_phase6_mutations.py tests/test_phase6_mutation_runner.py docs/refactor/evidence/phase-06
git commit -m "test(phase-6): kill follow-up safety mutations"
```

Review properties/performance and mutations/baselines independently.

---

### Task 14: Manifests, validator, CI e closeout

**Files:**
- Create: `scripts/generate_phase6_manifest.py`
- Create: `scripts/validate_phase6.py`
- Create: `tests/test_phase6_closeout.py`
- Create: `.github/workflows/phase6.yml`
- Create: `docs/refactor/evidence/phase-06/README.md`
- Create: `docs/refactor/evidence/phase-06/adversarial-review.md`
- Create: `docs/refactor/evidence/phase-06/schema-manifest.json`
- Create: `docs/refactor/evidence/phase-06/package-manifest.json`
- Create: `docs/refactor/evidence/phase-06/validation-result.json`
- Create: `docs/refactor/evidence/phase-06/performance-result.json`
- Create: `docs/refactor/evidence/phase-06/SHA256SUMS`
- Modify: `README.md`
- Modify: `docs/refactor/README.md`
- Modify: `docs/refactor/evidence/README.md`
- Modify: `docs/refactor/06-risk-register.md`
- Modify: `docs/refactor/phases/phase-06-handoff-and-payments.md`

**Interfaces:**
- Validator reconstructs all envelopes from closed independent operational contract; no runner-derived expected catalog.
- CI runs previous validators, full suite, properties, faults/restart/contention and mutations in independent 15-minute jobs plus aggregate gate.

- [ ] **Step 1: Write RED for missing closeout and hollow envelopes**

Tests must reject missing/extra files, stale hashes, hollow rows, wrong cardinality, duplicate IDs, bool/float/string protocol integers, reduced runner catalogs, nested live claims, DB/log artifacts, comment-only workflow commands and false live/PostgreSQL claims.

- [ ] **Step 2: Run and preserve RED**

```bash
python3 -m unittest tests.test_phase6_closeout -v >/tmp/phase6-task14-red.out 2>&1
```

- [ ] **Step 3: Implement deterministic manifests and closed validator**

`SHA256SUMS` includes all Phase 6 package files, mutation targets, tests, scripts, DDL, workflow, design/plan, phase/risk docs and evidence except itself. Use `rglob`; exclude DB/WAL/SHM/log. Validator scans AST for live capabilities, outbox→ledger writes and cross-workflow ownership.

- [ ] **Step 4: Build parallel CI workflow**

Jobs:

```text
static-validation
full-suite
properties
fault-restart-contention
mutations
phase6-gate
```

Each has `timeout-minutes: 15`; no workload reduction. Aggregate `needs` all five jobs and no `if: always()`.

- [ ] **Step 5: Run fresh terminal local gates**

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_phase6_properties.py --cases 20000 --seed 2026071906 --write docs/refactor/evidence/phase-06/property-result.json
python3 scripts/run_phase6_faults.py --seed 2026071906 --restart-schedules 2000 --contention-rounds 50 --write-fault-matrix docs/refactor/evidence/phase-06/fault-matrix.json --write-restart docs/refactor/evidence/phase-06/restart-result.json --write-concurrency docs/refactor/evidence/phase-06/concurrency-result.json
python3 scripts/run_phase6_mutations.py --write docs/refactor/evidence/phase-06/mutation-result.json
python3 scripts/generate_phase6_manifest.py --write
for phase in 0 1 2 3 4 5 6; do python3 scripts/validate_phase${phase}.py; done
python3 scripts/generate_phase6_manifest.py --check
python3 -m compileall -q reservation_domain reservation_lookup reservation_confirmation reservation_execution reservation_followup characterization scripts tests
git diff --check
git diff --cached --check
```

Use `PHASE1_LEGACY_SOURCE=/path-not-present-in-ci` where required. Record fresh elapsed/RSS/output SHA; raw output stays `/tmp`.

- [ ] **Step 6: Run final independent reviews**

Split scopes:

1. handoff policy/precedence and optional effects;
2. payment evidence/claims/settlement certainty;
3. validator/manifests/CI/docs/scope.

Any Critical/Important requires RED reproduction, fix, affected gate rerun, commit and narrow re-review.

- [ ] **Step 7: Commit implementation closeout**

```bash
git add .github README.md docs reservation_followup schemas scripts tests
git diff --cached --check
git commit -m "feat(phase-6): complete isolated handoff and payments"
```

- [ ] **Step 8: Publish and verify remote CI**

```bash
git push -u origin phase6-handoff-payments
```

Verify local SHA equals remote branch SHA. Integrate using the user-selected branch workflow; observe validators 0–6 and Phase 6 CI. Record run IDs/URLs in `ci-result.json`, create terminal documentary commit, push, and verify `main == origin/main == remote`.

- [ ] **Step 9: Close phase without starting Phase 7**

Update phase page to `concluída, publicada e com CI remoto verde`, mark R54–R62 only when evidence supports mitigation, keep R51 open, keep rollout `NO-GO`, and state `phase7_started=false`.

---

## Plan Self-Review Checklist

- [x] Every design requirement maps to at least one task.
- [x] No task imports or executes a live capability.
- [x] `PaymentWorkflow` bootstrap is tested against all non-confirmed outcomes.
- [x] Handoff required/optional semantics and terminal precedence are tested.
- [x] Method-only and economic-change paths are distinct.
- [x] Pix/Wise/Stripe evidence types and global claim identities are distinct.
- [x] Permanent post-fence safety and no outbox→settlement replay are fault-tested.
- [x] Properties begin from real cross-phase state, not preloaded follow-up state.
- [x] Mutation expected values are independent of runner catalogs.
- [x] All generated/shared manifests are refreshed after shared documentation changes.
- [x] Fase 7 and rollout remain blocked.
