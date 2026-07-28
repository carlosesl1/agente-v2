# Maya Critical Action Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan inline. Do not use subagents for this delivery; Carlos prefers the direct controller, sandbox-first path.

**Goal:** Make Maya present one natural, concrete approval request for the current commercial proposal and allow a critical reservation/payment-initiation command only after a one-shot, version-bound, runtime-enforced confirmation.

**Architecture:** Reuse `AwaitingConfirmationState`, `SummaryPresented`, `CommercialDraft.subject_signature`, `ConfirmationRecord`, `ExecutionQueuedState`, `commit_turn_v8`, and the existing command relay fence as the durable approval mechanism. Add only a public pending-action context for the stateless Hermes child, a closed approval assertion in the model protocol, a deterministic renderer/policy, and an expiry check. The model interprets the customer’s whole message; the reducer and executor enforce action/version/signature/fresh-read consistency.

**Tech Stack:** Python 3.11, frozen dataclasses/enums, SQLite STRICT stores, Hermes child JSON protocol, pytest, existing reservation-domain reducer, ManyChat-shaped sandbox harness.

## Global Constraints

- No keyword, regex, substring, or isolated-word authorization.
- A bare `sim`, `confirmo`, `cartão`, `aprovo`, `reserva`, or `20%` never opens the gate by itself.
- One natural contextual acceptance of the current unchanged summary is sufficient.
- Approval is one-shot only; never add session or permanent customer transaction allowances.
- Runtime precedence is `deny > ask > allow`; the model never owns the authorization decision.
- The customer-visible Bókun value is the provider checkout total, fee-inclusive.
- Agency Stripe initiation is 20% of the confirmed Bókun total with `ROUND_HALF_UP`; `334.95` produces `66.99`.
- Hostel payment remains 100% according to existing policy.
- Any material change supersedes the proposal and requires a new summary.
- Strict dispatch transports only `dumps_command(command)`; private bindings remain sidecar-only.
- Unknown provider outcomes go to `manual_review`, never blind retry.
- No Bókun write, Stripe creation, ManyChat delivery, handoff, or public rollout during conversational validation.
- Preserve the pre-existing untracked `uv.lock`; never add or modify it.
- Run the full suite with a clean environment and explicit `HERMES_LEADS_AGENT_CONFIG_PATH`.

## File Structure

- Create `v2_contracts/critical_actions.py`: closed public context and approval enums.
- Modify `v2_contracts/model.py`: carry pending action in `ModelRequest` and exact approval assertion in `ModelProposal`.
- Modify `v2_adapters/hermes_model.py`: serialize pending context and parse `v2-model-proposal-v3`.
- Create `v2_application/critical_actions.py`: typed policy, public renderer, proposal digest, expiry and binding helpers.
- Modify `v2_application/conversation.py`: use deterministic summary/acknowledgement and reject invalid or expired approvals.
- Modify `v2_application/turn_executor.py`: inject pending context into both model calls and bind confirmation rereads to the same assertion.
- Modify `v2_host/settings.py`: add `critical_approval_ttl_seconds`, default `1800`.
- Modify `v2_host/production.py`: construct the reducer/policy from production payment percentages and TTL.
- Modify `config/v2_luna_system_prompt.txt`: teach the child the pending-action protocol and natural decision semantics.
- Extend `tests/test_v2_critical_actions.py`, `tests/test_v2_hermes_model_adapter.py`, `tests/test_v2_hermes_child.py`, `tests/test_v2_conversation_reducer.py`, `tests/test_v2_turn_executor.py`, `tests/test_v2_settings.py`, `tests/test_v2_production_composition.py`, and `tests/test_v2_luna_prompt.py`.
- Create a sanitized effect-denied transcript under the existing isolated E2E laboratory evidence directory.

---

### Task 1: Closed pending-action and approval-assertion contracts

**Files:**
- Create: `v2_contracts/critical_actions.py`
- Modify: `v2_contracts/model.py`
- Test: `tests/test_v2_critical_actions.py`

**Interfaces:**
- Produces `CriticalActionKind`, `ApprovalBasis`, and `PendingCriticalActionContext`.
- Extends `ModelRequest.pending_action`.
- Extends `ModelProposal.confirmed_action_kinds` and `ModelProposal.approval_basis`.

- [ ] **Step 1: Write failing contract tests**

Add tests proving exact types, canonical ordering, UTC expiry, public-only fields, and confirm-only assertions:

```python
def test_pending_action_context_is_closed_public_and_canonical() -> None:
    context = PendingCriticalActionContext(
        summary_version=3,
        action_kinds=(
            CriticalActionKind.BOOK_ACTIVITY,
            CriticalActionKind.INITIATE_PAYMENT,
        ),
        public_summary=(
            "Vou reservar o Roteiro dos 4Ps em 18/11/2026 para 1 pessoa, "
            "pelo total final de R$ 334,95 já com a taxa, e gerar o link "
            "do sinal de R$ 66,99 no cartão."
        ),
        expires_at=datetime(2026, 7, 28, 6, 30, tzinfo=timezone.utc),
    )
    assert context.action_kinds == tuple(sorted(context.action_kinds, key=str))
    assert not hasattr(context, "offer_id")
    assert not hasattr(context, "subject_signature")
    assert not hasattr(context, "provider_ref")


def test_confirm_requires_bound_action_scope_and_basis() -> None:
    with pytest.raises(InvalidModelProposal, match="approval assertion"):
        ModelProposal(
            source_event_id="batch:approval-missing",
            intent="confirm",
            reply_chunks=("Pode seguir com essa reserva.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(),
            approval_basis=None,
        )
```

Also test that non-confirm intents reject confirmation fields and that
`signed_callback` is an unknown basis in protocol v3. A future ManyChat inbound
contract may add it only after it carries verifiable callback metadata.

- [ ] **Step 2: Run RED tests**

Run:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_critical_actions.py
```

Expected: collection/import failure because `v2_contracts.critical_actions` does not exist.

- [ ] **Step 3: Implement the minimal exact contracts**

Use frozen, slotted dataclasses and string enums:

```python
class CriticalActionKind(str, Enum):
    RESERVE_LODGING = "reserve_lodging"
    BOOK_ACTIVITY = "book_activity"
    BOOK_PACKAGE = "book_package"
    INITIATE_PAYMENT = "initiate_payment"
    MODIFY_RESERVATION = "modify_reservation"
    CANCEL_RESERVATION = "cancel_reservation"
    CHARGE_OR_CAPTURE = "charge_or_capture"
    REFUND = "refund"
    SHARE_HANDOFF_DATA = "share_handoff_data"


class ApprovalBasis(str, Enum):
    CONTEXTUAL_REFERENCE = "contextual_reference"


@dataclass(frozen=True, slots=True)
class PendingCriticalActionContext:
    summary_version: int
    action_kinds: tuple[CriticalActionKind, ...]
    public_summary: str
    expires_at: datetime
```

Validation must reject duplicate/empty actions, untrimmed or empty public text, non-UTC expiry, unknown enums, and booleans passed as integer versions. `ModelProposal.__post_init__` must require version/actions/basis together only for `intent == "confirm"` and forbid them otherwise.

- [ ] **Step 4: Run GREEN tests and surrounding model tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_critical_actions.py tests/test_v2_turns.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add v2_contracts/critical_actions.py v2_contracts/model.py \
  tests/test_v2_critical_actions.py tests/test_v2_turns.py
git commit -m "feat: add closed critical approval contracts"
```

---

### Task 2: Deterministic critical-action policy and natural renderer

**Files:**
- Create: `v2_application/critical_actions.py`
- Modify: `v2_application/conversation.py`
- Test: `tests/test_v2_critical_actions.py`
- Test: `tests/test_v2_conversation_reducer.py`

**Interfaces:**
- Produces `CriticalActionDisposition`, `CriticalActionPolicy`, `pending_action_context`, `critical_proposal_digest`, and `approval_assertion_matches`.
- Consumes exact `AwaitingConfirmationState`, locale, current time, TTL, and configured payment percentages.

- [ ] **Step 1: Write RED policy and rendering tests**

Add tests for `deny > ask > allow`, unsupported capabilities, exact PT-BR rendering, expiry, deposit rounding, and no internal tokens:

```python
def test_bokun_summary_explains_exact_effect_and_fee_inclusive_deposit() -> None:
    context = pending_action_context(
        _agency_awaiting(total="334.95", payment_method="stripe"),
        locale="pt-BR",
        now=NOW,
        approval_ttl=timedelta(minutes=30),
        agency_payment_percentage=20,
        hostel_payment_percentage=100,
    )
    assert context is not None
    assert context.action_kinds == (
        CriticalActionKind.BOOK_ACTIVITY,
        CriticalActionKind.INITIATE_PAYMENT,
    )
    assert context.public_summary == (
        "Só para confirmar: vou reservar o Roteiro dos 4Ps em 18/11/2026 "
        "para 1 pessoa, pelo total final de R$ 334,95 já com a taxa, e depois "
        "gerar o link do sinal de R$ 66,99 no cartão. Posso fazer essa reserva?"
    )
    forbidden = ("BRL", "stripe", "offer:", "product:", "subject_signature")
    assert not any(item in context.public_summary for item in forbidden)


def test_unsupported_cancellation_is_denied_even_when_model_requests_it() -> None:
    policy = CriticalActionPolicy(enabled=frozenset({
        CriticalActionKind.BOOK_ACTIVITY,
        CriticalActionKind.INITIATE_PAYMENT,
    }))
    assert policy.classify(CriticalActionKind.CANCEL_RESERVATION) \
        is CriticalActionDisposition.DENY
```

Add package, lodging, singular/plural, `pix`, `wise`, and card labels. Verify proposal digest changes for any action, version, signature, public summary, or expiry change and does not contain private material in the public context.

- [ ] **Step 2: Run RED tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_critical_actions.py \
  tests/test_v2_conversation_reducer.py::test_selection_builds_authoritative_summary_without_command
```

Expected: failures because policy/renderer functions do not exist and the old summary contains `BRL`/`stripe`.

- [ ] **Step 3: Implement policy and renderer**

Use a pure enum policy:

```python
class CriticalActionDisposition(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CriticalActionPolicy:
    enabled: frozenset[CriticalActionKind]

    def classify(
        self, action: CriticalActionKind | None
    ) -> CriticalActionDisposition:
        if action is None:
            return CriticalActionDisposition.ALLOW
        if action in self.enabled:
            return CriticalActionDisposition.ASK
        return CriticalActionDisposition.DENY
```

Derive the customer-facing context only from `AwaitingConfirmationState.draft` and configured percentages. Use `Decimal` and `ROUND_HALF_UP`; format PT-BR currency/date without locale-global state. `expires_at` is `summary.presented_at + approval_ttl`, with the initial production TTL of 1800 seconds. Derive the private digest with a domain prefix such as `maya-critical-action-proposal-v1\0` over canonical action scope, draft id/version/signature, public text, and expiry.

Replace both package and single-offer ad-hoc `summary_text` blocks in `conversation.py` with this renderer. Do not trust `proposal.reply_chunks` for summary text.

- [ ] **Step 4: Run GREEN tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_critical_actions.py tests/test_v2_conversation_reducer.py \
  tests/test_v2_payment_initiation.py
```

Expected: all selected tests pass, including `334.95 -> 66.99`.

- [ ] **Step 5: Commit**

```bash
git add v2_application/critical_actions.py v2_application/conversation.py \
  tests/test_v2_critical_actions.py tests/test_v2_conversation_reducer.py
git commit -m "feat: render natural critical action approvals"
```

---

### Task 3: Hermes child protocol v3 and natural semantic classification

**Files:**
- Modify: `v2_adapters/hermes_model.py`
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_hermes_child.py`
- Modify: `tests/test_v2_luna_prompt.py`

**Interfaces:**
- Serializes `pending_action` as public-only JSON.
- Parses `v2-model-proposal-v3` with `confirmed_action_kinds` and `approval_basis`.
- Keeps v1/v2 readable for non-confirm historical/fallback frames but forbids old-schema confirmation from entering the effect path.

- [ ] **Step 1: Write RED wire/parser tests**

Prove the child sees only this shape:

```json
{
  "pending_action": {
    "summary_version": 1,
    "action_kinds": ["book_activity", "initiate_payment"],
    "public_summary": "Só para confirmar: ...",
    "expires_at": "2026-07-28T06:30:00+00:00"
  }
}
```

Assert forbidden strings (`offer:`, `product:`, `provider_ref`, `subject_signature`, `binding`, customer email/phone) are absent from stdin. Add parser cases for contextual confirmation, ambiguity, adjustment, signed callback, unknown action, and v2 confirmation rejection.

- [ ] **Step 2: Run RED tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_hermes_model_adapter.py tests/test_v2_hermes_child.py \
  tests/test_v2_luna_prompt.py
```

Expected: failures because protocol v3 and prompt rules are absent.

- [ ] **Step 3: Implement protocol v3**

Add v3 response fields exactly:

```python
_RESPONSE_FIELDS_V3 = frozenset((
    *_RESPONSE_FIELDS_V2,
    "confirmed_action_kinds",
    "approval_basis",
))
```

Serialize `pending_action` in `_request_wire`. Parse actions and basis into exact enums. The repair suffix must demand v3 whenever `request.pending_action is not None`.

Update the system prompt with these semantic rules:

- If there is no `pending_action`, never emit `confirm`.
- If the message naturally and unambiguously authorizes the current action (for example, “pode reservar esse passeio e gerar o link”), emit `confirm`, the current version, exact action list, and `contextual_reference`.
- A bare isolated assent is ambiguous and must not emit `confirm`.
- Any changed term, including “sim, mas…”, is `adjust`.
- Questions about fee, deposit, payment method, or policy are `inform`.
- Protocol v3 has no callback basis; unknown bases are rejected closed.
- Never expose action IDs, provider names, gates, reducers, digests, or schema vocabulary to the customer.

- [ ] **Step 4: Run GREEN tests**

Run the same selected test command. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add v2_adapters/hermes_model.py config/v2_luna_system_prompt.txt \
  tests/test_v2_hermes_model_adapter.py tests/test_v2_hermes_child.py \
  tests/test_v2_luna_prompt.py
git commit -m "feat: bind natural confirmation to pending action context"
```

---

### Task 4: Runtime gate, expiry, reread binding, and deterministic acknowledgement

**Files:**
- Modify: `v2_application/turn_executor.py`
- Modify: `v2_application/conversation.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_conversation_reducer.py`

**Interfaces:**
- Injects one identical `PendingCriticalActionContext` into both model calls.
- Accepts a confirm only when version, action list, basis, current state, TTL, signature, profile, fresh provider read, and subject all agree.
- Emits no command for ambiguous, stale, expired, changed, denied, or unsupported actions.

- [ ] **Step 1: Write RED executor/reducer tests**

Add tests for:

1. natural contextual confirmation creates one command and one relay;
2. isolated assent classified as `inform` creates zero reads/commands;
3. action-list mismatch creates zero provider reads/commands;
4. expired summary creates zero provider reads/commands and asks for a refreshed summary;
5. `sim, mas duas pessoas` is adjustment and supersedes the old proposal;
6. follow-up model call cannot swap approval basis/action scope after the parent-derived reread;
7. same aggregate event replay returns the same receipt and never duplicates a command;
8. profile/provider/price divergence stays fail-closed.

The positive fake model proposal must include:

```python
ModelProposal(
    source_event_id=second_batch.batch_id,
    intent="confirm",
    reply_chunks=("Pode reservar esse passeio e gerar o link.",),
    facts=(),
    read_requests=(),
    effect_proposals=(),
    confirmed_summary_version=1,
    confirmed_action_kinds=(
        CriticalActionKind.BOOK_ACTIVITY,
        CriticalActionKind.INITIATE_PAYMENT,
    ),
    approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
)
```

- [ ] **Step 2: Run RED tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_turn_executor.py tests/test_v2_conversation_reducer.py
```

Expected: new tests fail before code changes.

- [ ] **Step 3: Implement the gate using existing atomic state transition**

In `_prepare`, derive `pending_action` once from the fenced current state and pass the exact same value to first and follow-up `ModelRequest` values. Extend `_confirmation_read_requests` to require a matching, unexpired assertion before provider reads. Compare all confirmation fields if the second model call follows derived reads.

In `V2ConversationReducer.reduce`, before `ConfirmationRecorded`:

```python
match = approval_assertion_matches(
    workflow=state.workflow,
    pending_action=pending_action,
    proposal=proposal,
    now=instant,
)
if match is ApprovalMatch.EXPIRED:
    return no_command("approval_expired", REFRESH_SUMMARY_TEXT)
if match is not ApprovalMatch.MATCH:
    return no_command("stale_confirmation", CLARIFY_APPROVAL_TEXT)
```

Keep `ConfirmationRecord -> ExecutionQueuedState -> ReservationCommand` in the same existing reducer decision and `commit_turn_v8` transaction. Replace model-provided confirmation copy with deterministic acknowledgement:

```text
Perfeito — vou processar sua reserva agora.
```

Do not claim the reservation or payment link exists yet.

- [ ] **Step 4: Run GREEN tests plus atomic fault injection**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_turn_executor.py tests/test_v2_conversation_reducer.py \
  tests/test_v2_reservation_relay.py tests/test_v2_reads.py
```

Expected: all pass; injected commit failure leaves zero command/relay rows.

- [ ] **Step 5: Commit**

```bash
git add v2_application/turn_executor.py v2_application/conversation.py \
  tests/test_v2_turn_executor.py tests/test_v2_conversation_reducer.py
git commit -m "feat: enforce one-shot critical action approval"
```

---

### Task 5: Production configuration and capability policy

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_production_composition.py`

**Interfaces:**
- Adds `critical_approval_ttl_seconds: int = 1800` from `V2_CRITICAL_APPROVAL_TTL_SECONDS`.
- Builds enabled action kinds from actual provider/payment gates, not from model output.

- [ ] **Step 1: Write RED settings/composition tests**

Assert default 1800 seconds, reject bool/zero/negative/non-integer values, and prove:

- Bókun write + payment-link gates enable `book_activity` and `initiate_payment`;
- Cloudbeds write enables `reserve_lodging`;
- disabled provider capability remains `deny`;
- cancel/modify/capture/refund remain `deny` in this release.

- [ ] **Step 2: Run RED tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_settings.py tests/test_v2_production_composition.py
```

Expected: failures for missing setting and policy wiring.

- [ ] **Step 3: Implement settings and composition wiring**

Parse the environment exactly and construct `CriticalActionPolicy` from the actual gates. Pass TTL and percentages into `V2ConversationReducer`/critical-action renderer. Never infer capabilities from prompt text or customer language.

- [ ] **Step 4: Run GREEN tests**

Run the same command. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add v2_host/settings.py v2_host/production.py \
  tests/test_v2_settings.py tests/test_v2_production_composition.py
git commit -m "feat: configure critical approval policy"
```

---

### Task 6: Real-model effect-denied conversation and full regression

**Files:**
- Modify only if needed: `/home/ubuntu/workspace/maya-v2-real-whatsapp-stripe-20260727T153701Z/state-e2e/maya_e2e_turn.py`
- Create: `/home/ubuntu/workspace/maya-v2-real-whatsapp-stripe-20260727T153701Z/evidence/natural-critical-approval-transcript.jsonl`
- Create: `/home/ubuntu/workspace/maya-v2-real-whatsapp-stripe-20260727T153701Z/evidence/natural-critical-approval-report.md`

**Interfaces:**
- Uses the real Hermes child/model.
- Uses fresh isolated SQLite state.
- Allows provider reads only if needed; blocks all provider writes, payment initiation, handoff, ManyChat delivery, and public API startup.

- [ ] **Step 1: Run focused tests and static checks on one HEAD**

```bash
git diff --check
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_critical_actions.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_hermes_child.py \
  tests/test_v2_luna_prompt.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_settings.py \
  tests/test_v2_production_composition.py \
  tests/test_v2_reads.py \
  tests/test_v2_payment_initiation.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Build a local immutable test candidate**

Build from the exact clean HEAD, with only `?? uv.lock` allowed. Record image ID/digest. Do not push GHCR and do not deploy.

- [ ] **Step 3: Prove all external effect gates are closed before conversation**

Record sanitized booleans/counters showing:

```text
bokun_writes_enabled=false
cloudbeds_writes_enabled=false
stripe_links_enabled=false
wise_instructions_enabled=false
pix_instructions_enabled=false
manychat_delivery=false
handoff_delivery=false
public_api_started=false
```

Abort if any value is true. Do not rely only on prompt instructions; workers must be absent or mechanically gated.

- [ ] **Step 4: Execute the real-model natural dialogue**

Use fictitious identity and November 2026. Required approval turn:

```text
Pode reservar exatamente esse passeio para essa data e gerar o link do sinal no cartão.
```

Expected:

- the preceding Maya message is the natural deterministic summary;
- the first acceptance turn returns typed `confirm` for the current action/version;
- Maya does not repeat the summary;
- public acknowledgement is “Perfeito — vou processar sua reserva agora.”;
- at most one command is prepared in isolated state;
- zero reservation workers, payment workers, handoff workers, or public-delivery workers execute.

Run negative conversations in fresh states for bare “sim”, “sim, mas duas pessoas”, question about 20%, old-version acceptance, timeout, and denial. Expected command count is zero for each.

- [ ] **Step 5: Verify effect counters and sanitized evidence**

Read back every isolated SQLite store and provider-side test surface available. Record:

```text
provider_write_calls=0
stripe_links_created=0
manychat_messages_sent=0
handoffs_sent=0
payment_completed=0
```

Sanitize secrets, PII, private bindings, raw tokens, provider credentials, and private hashes from the transcript.

- [ ] **Step 6: Run full clean regression**

```bash
env -i \
  HOME=/home/ubuntu \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.json \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  --deselect=tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only \
  --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts \
  --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch \
  --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path \
  --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution \
  --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing \
  --deselect=tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```

Expected: exit code 0 with no failures. Report the real count from stdout; never copy an older count.

- [ ] **Step 7: Final verification and commit**

```bash
git diff --check
git status --short
```

Expected: only `?? uv.lock`. Commit any remaining tracked test/documentation changes, verify the candidate HEAD, image digest, effect counters, transcript, and regression all refer to the same code.

## Self-Review Results

- Spec coverage: reservation/payment initiation, one-shot approval, natural copy, exact version/action binding, expiry, reread, atomic consumption, read-back, denial, ambiguity, replay, provider uncertainty, and effect-denied validation each map to a task.
- Scope boundary: cancel/modify/capture/refund are classified now but remain disabled; their provider implementations are separate future projects.
- Every task names concrete files, commands, expected failures, and recovery behavior.
- Type consistency: `PendingCriticalActionContext`, `CriticalActionKind`, `ApprovalBasis`, `confirmed_action_kinds`, and `approval_basis` have one spelling across tasks.
- Safety: real-model validation never starts effect workers and never reuses the existing real Bókun booking or Stripe link.

## Execution Mode

Use **inline execution in this session**, sandbox-first, following the tasks in order with RED/GREEN commits and one integrated regression/review at the end. Do not dispatch mapping/review subagents for this delivery.
