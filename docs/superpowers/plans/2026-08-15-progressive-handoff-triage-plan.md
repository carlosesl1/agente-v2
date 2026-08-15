# Progressive Handoff Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Maya complete useful commercial triage before operational handoff while preserving immediate handoff for explicit human requests, sensitive complaints, and real safety concerns.

**Architecture:** Keep the existing conversation, reservation, payment, and durable handoff workflows unchanged. Implement the behavior in the existing Maya system prompt and model progression suffix, then refine only the existing closed public handoff projection so canonical reservation certainty produces useful, honest finalization wording.

**Tech Stack:** Python 3.12, pytest, Hermes tool-free model adapter, typed handoff workflow, Ruff 0.15.10.

## Global Constraints

- Do not add a workflow, state machine, database table, agent, provider service, dependency, or configuration flag.
- Do not weaken provider evidence, confirmation, idempotency, reservation, payment, or handoff receipt gates.
- Immediate handoff remains mandatory for an explicit human request, sensitive complaint, or real safety concern.
- Negotiation and operational failures use progressive triage whenever useful customer facts can still be collected safely.
- Never claim a reservation, payment, human reading, or delivery without the corresponding canonical evidence.
- A terminal handoff emits no stale collection or confirmation question.
- Deployment smoke creates no reservation, payment link, handoff, or outbound message.

---

### Task 1: Progressive Maya handoff behavior

**Files:**
- Modify: `tests/test_v2_luna_prompt.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `v2_adapters/hermes_model.py`

**Interfaces:**
- Consumes: existing closed intents `inform` and `request_handoff`, existing typed facts/read requests, existing `handoff_status` semantics.
- Produces: prompt rules and high-salience runtime guidance only; no new wire field or effect authority.

- [ ] **Step 1: Write failing prompt-contract tests**

Add tests that require these exact behavioral invariants:

```python
def test_progressive_handoff_collects_useful_details_before_operational_transfer() -> None:
    assert "HANDOFF IMEDIATO" in PROMPT
    assert "TRIAGEM ANTES DO HANDOFF" in PROMPT
    assert "pedido explícito para falar com uma pessoa" in PROMPT
    assert "reclamação sensível" in PROMPT
    assert "risco ou dúvida real de segurança" in PROMPT
    assert "desconto, cupom ou negociação" in PROMPT
    assert "continue coletando" in PROMPT
    assert "Já temos tudo para realizar sua reserva" in PROMPT


def test_progressive_handoff_never_overclaims_reservation_or_payment() -> None:
    assert "resultado da reserva for incerto" in PROMPT
    assert "não afirme que ela foi criada nem que não foi criada" in PROMPT
    assert "não repita nem recrie a reserva" in PROMPT
```

Add a model-adapter suffix assertion that requires progression before operational handoff but preserves immediate exceptions:

```python
def test_commercial_progression_suffix_distinguishes_immediate_and_progressive_handoff() -> None:
    assert "PROGRESSIVE HANDOFF TRIAGE" in hermes_model._COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX
    assert "explicit human request, sensitive complaint, or real safety concern" in hermes_model._COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX
    assert "continue collecting the next useful safe commercial facts" in hermes_model._COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_v2_luna_prompt.py \
  tests/test_v2_hermes_model_adapter.py -k 'progressive_handoff or commercial_progression_suffix'
```

Expected: FAIL because the new rules and suffix text do not exist.

- [ ] **Step 3: Implement the minimal prompt change**

Replace the current broad handoff rule with two closed groups:

```text
HANDOFF IMEDIATO
- Use request_handoff immediately, with no collection question, for an explicit human request, a sensitive complaint, or a real safety concern.

TRIAGEM ANTES DO HANDOFF
- For discount/coupon/negotiation and operational reservation/payment difficulty, continue collecting the next useful safe commercial facts before request_handoff.
- Before option selection, collect service, product/preferences, dates, adults and children.
- After option selection, collect every field already required by the existing reservation contract.
- If reservation outcome is uncertain, do not claim creation or non-creation.
- Payment failure never repeats or recreates the reservation.
```

Add equivalent concise English guidance to `_COMMERCIAL_PROGRESSION_SYSTEM_SUFFIX`. Do not add a schema field, state flag, model parser branch, or deterministic keyword router.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run the same command from Step 2.

Expected: all selected tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add config/v2_luna_system_prompt.txt v2_adapters/hermes_model.py \
  tests/test_v2_luna_prompt.py tests/test_v2_hermes_model_adapter.py
git diff --cached --check
git commit -m "fix(v2): triage leads before operational handoff"
```

---

### Task 2: Evidence-aware closed operational handoff copy

**Files:**
- Modify: `tests/test_phase6_handoff.py`
- Modify: `reservation_followup/handoff.py`

**Interfaces:**
- Consumes: `ExecutionOutcome | None`, `HandoffStatus`, and the existing `project_handoff_public_reply(...)` interface.
- Produces: the same `PublicHandoffProjection`; no signature, enum, wire, store, or workflow change.

- [ ] **Step 1: Write failing projection tests**

Update exact safe-copy expectations so active operational handoff says:

```python
def test_operational_handoff_copy_uses_canonical_reservation_certainty() -> None:
    no_effect = project_handoff_public_reply(
        active_handoff(),
        outcome(certainty=ExecutionCertainty.CALLED_NO_EFFECT),
    )
    assert no_effect.public_text == (
        "A reserva não foi criada. Já temos seus dados e os detalhes da reserva. "
        "Uma pessoa da nossa equipe vai assumir a conversa e concluir essa etapa por aqui."
    )

    uncertain = project_handoff_public_reply(
        active_handoff(),
        outcome(certainty=ExecutionCertainty.CALLED_UNKNOWN),
    )
    assert uncertain.public_text.startswith(
        "Ainda não sabemos se a reserva foi criada. Já temos seus dados"
    )

    confirmed = project_handoff_public_reply(active_handoff(), outcome())
    assert confirmed.public_text == (
        "A reserva foi criada. Uma pessoa da nossa equipe vai assumir a conversa "
        "e ajudar você a finalizar o atendimento por aqui."
    )
```

Retain tests proving absent outcome does not invent reservation truth and terminal handoff suppresses stale questions.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_phase6_handoff.py -k 'operational_handoff_copy or terminal_handoff or projection_uses_only_canonical_certainty'
```

Expected: FAIL because active handoff still uses the generic fixed sentence.

- [ ] **Step 3: Implement minimal closed-copy selection**

Keep `project_handoff_public_reply(...)` and `PublicHandoffProjection` unchanged. Replace the single active-handoff sentence with a closed mapping selected only from `reservation_outcome.certainty`:

```python
_ACTIVE_HANDOFF_TEXT = {
    None: "Uma pessoa da nossa equipe vai assumir a conversa e continuar seu atendimento por aqui.",
    ExecutionCertainty.NOT_CALLED: "Já temos seus dados e os detalhes da reserva. Uma pessoa da nossa equipe vai assumir a conversa e concluir essa etapa por aqui.",
    ExecutionCertainty.CALLED_NO_EFFECT: "Já temos seus dados e os detalhes da reserva. Uma pessoa da nossa equipe vai assumir a conversa e concluir essa etapa por aqui.",
    ExecutionCertainty.CALLED_UNKNOWN: "Já temos seus dados e os detalhes da reserva. Uma pessoa da nossa equipe vai assumir a conversa e concluir essa etapa por aqui.",
    ExecutionCertainty.EFFECT_CONFIRMED: "Uma pessoa da nossa equipe vai assumir a conversa e ajudar você a finalizar o atendimento por aqui.",
}
```

Use the same mapping in DTO validation and projection. Keep cancelled/completed copy unchanged. Do not accept caller-authored copy.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run the command from Step 2, then:

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_phase6_handoff.py \
  tests/test_phase6_handoff_worker.py \
  tests/test_v2_manychat_handoff_delivery.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_turn_executor.py -k 'handoff'
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add reservation_followup/handoff.py tests/test_phase6_handoff.py
git diff --cached --check
git commit -m "fix(v2): explain operational handoff progress"
```

---

### Task 3: Regression, image, and no-effect rollout

**Files:**
- No production source changes expected.
- Build/deploy artifacts remain outside the repository under `/home/ubuntu/workspace/agente-v2-canary-deploy`.

**Interfaces:**
- Consumes: Tasks 1–2 commits and existing `Dockerfile.v2`, Compose rollout, health/readiness endpoints.
- Produces: one immutable image and consistent API/worker/router deployment with previous digest preserved for rollback.

- [ ] **Step 1: Run affected regression suites**

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_v2_luna_prompt.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_phase6_handoff.py \
  tests/test_phase6_handoff_worker.py \
  tests/test_v2_manychat_handoff_delivery.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_recovery.py
```

Expected: PASS with zero failures.

- [ ] **Step 2: Run static and boundary checks**

```bash
uvx ruff@0.15.10 check \
  v2_adapters/hermes_model.py reservation_followup/handoff.py \
  tests/test_v2_luna_prompt.py tests/test_v2_hermes_model_adapter.py \
  tests/test_phase6_handoff.py
python -m compileall -q v2_adapters/hermes_model.py reservation_followup/handoff.py
git diff --check
python scripts/check_fasttrack_boundaries.py
```

Expected: all commands exit 0.

- [ ] **Step 3: Run isolated no-effect behavior smoke**

Construct Maya requests with fake leads and mocked/closed effects for:

- explicit human request → immediate `request_handoff` and no question;
- negotiation with missing dates/party → `inform` and one useful question;
- reservation finalization difficulty with complete data → progressive handoff copy;
- confirmed reservation plus payment difficulty → no new reservation command.

Assert zero provider writes, zero payment-link creates, zero handoff delivery, and zero ManyChat sends.

- [ ] **Step 4: Build and promote immutable image**

Build `Dockerfile.v2` with the exact Git revision label, push to the local registry, preserve the current Compose/env/runtime directory as rollback, promote API/worker/router to the same digest, and do not expose secrets in output.

- [ ] **Step 5: Verify runtime and Git**

Verify:

```text
API/worker/router revision = exact final Git SHA
API/worker/router image = exact immutable digest
public /healthz = alive
public /readyz = ready
worker recent error markers = 0
Git worktree clean
local HEAD = origin branch HEAD
```

Do not send a WhatsApp/ManyChat message or create any external commercial effect during this verification.
