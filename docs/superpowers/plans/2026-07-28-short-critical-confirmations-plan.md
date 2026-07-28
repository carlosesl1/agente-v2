# Short Critical Confirmations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute inline; do not dispatch subagents for this delivery.

**Goal:** Let Maya accept short natural affirmations such as “Sim”, “Pode reservar”, “Pode sim”, “Confirmado” and “Isso mesmo” when exactly one authenticated critical-action proposal is pending, current and unchanged, without weakening any runtime authorization gate.

**Architecture:** Remove the executor’s text-length heuristic and let the Hermes child classify the complete message against `pending_action`. Keep authority entirely in the existing typed v3 assertion plus runtime checks for pending state, exact version/actions/basis, empty material facts, scope projection, capability, TTL, fresh provider reread, atomic command creation, idempotency and fencing. Informational detours preserve the pending workflow; a later short affirmative may consume it while it remains valid.

**Tech Stack:** Python 3.11, frozen typed contracts, Hermes child JSON protocol v3, existing V2 reducer/executor, pytest, isolated real-model sandbox with zero effect clients/stores/toolsets.

## Global Constraints

- No regex, keyword list, substring, minimum token count or magic phrase may authorize a critical action.
- A short affirmative is valid only with exactly one authenticated `PendingCriticalActionContext` that is pending, unexpired and materially unchanged.
- The same short text without `pending_action` cannot emit a valid confirmation grant.
- `intent="confirm"` must keep `facts=()`, exact `confirmed_summary_version`, exact `confirmed_action_kinds` and `approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE`.
- “Sim, mas agora são duas pessoas”, another date/product/payment, refusal, uncertainty and questions do not confirm.
- Informational detours do not revoke the proposal; expiry, refusal, supersession, material change, handoff or consumption do.
- Preserve `deny > ask > allow`, kill switch, write window and payment-method capability checks.
- Preserve provider reread before command, TTL checks before each reread and before commit, atomic command/relay persistence, one-shot replay/idempotency and read-back-only success language.
- Real-model validation must have no provider writer, operational store, Stripe client, ManyChat sender, handoff sender or public deployment path.
- Do not modify, stage or commit the pre-existing untracked `uv.lock`.
- Do not deploy, push, create a booking, create a payment link or send a public message.

## File Structure

- Modify `tests/test_v2_turn_executor.py`: replace tests for the removed lexical heuristic with parameterized end-to-end short-confirmation coverage while retaining stale/expired/scope tests.
- Modify `tests/test_v2_luna_prompt.py`: assert semantic short confirmation and absence of the old bare-yes rejection.
- Modify `tests/test_v2_conversation_reducer.py`: prove an informational detour preserves the pending authenticated proposal and a later typed confirmation consumes it once.
- Modify `v2_application/turn_executor.py`: remove `_REFERENCE_TOKEN_RE`, `_has_contextual_reference_shape`, and the block that rewrites typed short confirmations to `inform`.
- Modify `config/v2_luna_system_prompt.txt`: classify short affirmatives as `confirm` when `pending_action` exists and all terms remain unchanged.
- Modify `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/run_probe.py`: expand the real-model, zero-effect matrix to short positive forms and closed negative forms, including “Sim” without pending context.
- Create `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/evidence-short-confirmations/`: sanitized transcript, summary, counters and report for the exact final prompt hash.
- Modify `docs/superpowers/specs/2026-07-27-natural-reservation-confirmation-design.md`: change status from review to implemented only after all validation passes.

---

### Task 1: Lock the desired semantics with failing tests

**Files:**
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_luna_prompt.py`
- Modify: `tests/test_v2_conversation_reducer.py`

**Interfaces:**
- Consumes: `PendingCriticalActionContext`, `ModelProposal`, `V2TurnExecutor`, `V2ConversationReducer` and existing test fixtures.
- Produces: executable acceptance criteria for short positive forms, no-pending denial and informational-detour preservation.

- [ ] **Step 1: Replace the lexical-helper import and assertions**

Remove `_has_contextual_reference_shape` from the import block in `tests/test_v2_turn_executor.py` and delete these obsolete assertions from `test_critical_confirmation_binding_rejects_expiry_and_scope_drift`:

```python
assert _has_contextual_reference_shape("Sim") is False
assert _has_contextual_reference_shape("👍") is False
assert _has_contextual_reference_shape("Pode fazer isso") is False
assert _has_contextual_reference_shape(
    "Pode reservar esse passeio e gerar o link do sinal no cartão."
) is True
```

Do not remove the assertions for version, action scope, basis, expiry, missing pending context or material scope drift.

- [ ] **Step 2: Parameterize the integrated confirmation test with natural short forms**

Add this decorator above `test_confirmed_turn_commits_reservation_command_and_relay_atomically` and accept `confirmation_text: str` in its signature:

```python
@pytest.mark.parametrize(
    "confirmation_text",
    (
        "Sim",
        "Pode reservar",
        "Pode sim",
        "Confirmado",
        "Isso mesmo",
        "Sim, por favor",
        "Pode reservar esse passeio e gerar o link do sinal no cartão.",
    ),
)
def test_confirmed_turn_commits_reservation_command_and_relay_atomically(
    tmp_path,
    confirmation_text: str,
) -> None:
```

Use the parameter as the inbound text:

```python
second_event = InboundEvent(
    # existing identity fields remain unchanged
    text=confirmation_text,
    # existing remaining fields remain unchanged
)
```

For every parameter, retain assertions that the confirmation creates exactly one command/relay, returns the deterministic acknowledgement and that replay returns the same receipt without a second command.

- [ ] **Step 3: Add an informational-detour reducer regression**

Add a focused test in `tests/test_v2_conversation_reducer.py` using the existing `_awaiting_from_ready`, `_boundary`, `_projection`, `_proposal`, `_profile` and `_read_for_component` helpers:

```python
def test_informational_detour_preserves_pending_proposal_for_later_short_confirmation() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(
            service=ServiceKind.LODGING,
            workflow_id="workflow:short-confirm-after-inform",
        )
    )
    reducer = _reducer()
    informed = reducer.reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=ModelProposal(
            source_event_id="event:inform-before-short-confirm",
            intent="inform",
            reply_chunks=("O sinal é o valor informado no resumo.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert informed.next_state.workflow == awaiting
    assert informed.commands == ()

    confirmed = reducer.reduce(
        state=informed.next_state,
        projection=informed.projection,
        proposal=ModelProposal(
            source_event_id="event:short-confirm-after-inform",
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=awaiting.draft.version,
            confirmed_action_kinds=(
                CriticalActionKind.INITIATE_PAYMENT,
                CriticalActionKind.RESERVE_LODGING,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=2),
    )
    assert len(confirmed.commands) == 1
```

The helper creates a lodging proposal whose exact pending action tuple is `INITIATE_PAYMENT` plus `RESERVE_LODGING`; keep that tuple explicit in this regression.

- [ ] **Step 4: Change prompt contract assertions**

Replace the old bare-yes assertion in `tests/test_v2_luna_prompt.py` with exact positive and closed-negative checks:

```python
assert "Uma confirmação afirmativa curta é válida" in PROMPT
for example in ("“Sim”", "“Pode reservar”", "“Pode sim”", "“Confirmado”", "“Isso mesmo”"):
    assert example in PROMPT
assert "sem `pending_action` nunca autoriza" in PROMPT
assert "facts=[]" in PROMPT
assert "“Sim” isolado, emoji" not in PROMPT
assert "sim, mas" in PROMPT.casefold()
```

Keep all v3, exact action scope, approval basis and no-callback assertions.

- [ ] **Step 5: Run RED tests**

Run:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_turn_executor.py::test_confirmed_turn_commits_reservation_command_and_relay_atomically \
  tests/test_v2_conversation_reducer.py::test_informational_detour_preserves_pending_proposal_for_later_short_confirmation \
  tests/test_v2_luna_prompt.py::test_luna_prompt_requires_contextual_v3_critical_approval
```

Expected before implementation: short executor cases fail because the runtime rewrites messages below the token threshold to `inform`; the prompt assertion fails because it still explicitly rejects bare “Sim”. The reducer detour case may already pass, documenting preserved behavior rather than requiring new production code.

---

### Task 2: Remove lexical authorization and teach semantic short confirmation

**Files:**
- Modify: `v2_application/turn_executor.py`
- Modify: `config/v2_luna_system_prompt.txt`
- Test: `tests/test_v2_turn_executor.py`
- Test: `tests/test_v2_conversation_reducer.py`
- Test: `tests/test_v2_luna_prompt.py`

**Interfaces:**
- Consumes: typed `ModelProposal` v3 emitted with a `PendingCriticalActionContext`.
- Produces: short affirmative messages reach `_critical_confirmation_bound` unchanged; the existing runtime remains the only grant authority.

- [ ] **Step 1: Delete the text-shape heuristic**

In `v2_application/turn_executor.py`, delete:

```python
_REFERENCE_TOKEN_RE: Final = re.compile(r"[^\W_]+", re.UNICODE)


def _has_contextual_reference_shape(message: str) -> bool:
    if type(message) is not str:
        raise TypeError("message must be exact text")
    tokens = tuple(_REFERENCE_TOKEN_RE.findall(message))
    return len(tokens) >= 4 and sum(len(item) for item in tokens) >= 16
```

Keep `re` imported because `_ID_RE` and `_HASH_RE` still use it.

- [ ] **Step 2: Delete the typed-confirmation downgrade**

Delete this block immediately after the first proposal is validated:

```python
if (
    pending_action is not None
    and first_proposal.intent == "confirm"
    and not _has_contextual_reference_shape(batch.combined_text)
):
    first_proposal = replace(
        first_proposal,
        intent="inform",
        reply_chunks=(
            "Para autorizar, diga naturalmente qual reserva e qual pagamento devo fazer.",
        ),
        read_requests=(),
        confirmed_summary_version=None,
        confirmed_action_kinds=(),
        approval_basis=None,
    )
```

Do not change `_critical_confirmation_bound`, `_critical_model_reads_allowed`, `_confirmation_read_requests`, per-read expiry validation, reducer validation or pre-commit expiry validation.

- [ ] **Step 3: Rewrite only the short-confirmation prompt rules**

Replace the old rules at lines 12–13 of `config/v2_luna_system_prompt.txt` with:

```text
- Uma confirmação afirmativa curta é válida quando `pending_action` existe e a mensagem aprova a proposta sem pergunta, recusa, incerteza, condição ou mudança material. Exemplos não exaustivos: “Sim”, “Pode reservar”, “Pode sim”, “Confirmado”, “Isso mesmo”, “Correto”, “Pode seguir” e “Sim, por favor”.
- Esses exemplos não são palavras-gatilho nem allowlist. Interprete a mensagem inteira contra `pending_action`; sem `pending_action` nunca autoriza, e você nunca inventa versão, ações ou base.
- Uma conversa informativa intermediária não cancela `pending_action`. Enquanto a proposta estiver presente, a confirmação curta posterior pode usar intent=confirm.
```

Keep the rules that a valid confirm uses `facts=[]`, exact version/actions and `contextual_reference`; keep “sim, mas...” as `adjust`, refusal as revocation and questions/uncertainty as non-confirmation.

- [ ] **Step 4: Run GREEN focused tests**

Run:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_turn_executor.py::test_confirmed_turn_commits_reservation_command_and_relay_atomically \
  tests/test_v2_turn_executor.py::test_critical_confirmation_binding_rejects_expiry_and_scope_drift \
  tests/test_v2_turn_executor.py::test_approval_expiring_during_model_call_starts_zero_confirmation_reads \
  tests/test_v2_turn_executor.py::test_approval_expiring_between_reducer_and_commit_persists_zero_effect_rows \
  tests/test_v2_conversation_reducer.py::test_informational_detour_preserves_pending_proposal_for_later_short_confirmation \
  tests/test_v2_luna_prompt.py
```

Expected: all selected tests pass. The parameterized executor test must report seven passing cases and preserve one command plus idempotent replay per isolated case.

- [ ] **Step 5: Run the broader approval regression**

Run:

```bash
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

Expected: exit code 0 with no failures. Record the fresh count from stdout.

- [ ] **Step 6: Commit the runtime change**

Run:

```bash
git diff --check
git add \
  v2_application/turn_executor.py \
  config/v2_luna_system_prompt.txt \
  tests/test_v2_turn_executor.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_luna_prompt.py
git commit -m "fix: accept short bound critical confirmations"
```

Expected: only those five files enter the commit; `uv.lock` remains untracked.

---

### Task 3: Validate the real model with zero effects

**Files:**
- Modify: `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/run_probe.py`
- Create: `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/evidence-short-confirmations/transcript.json`
- Create: `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/evidence-short-confirmations/summary.json`
- Create: `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/evidence-short-confirmations/effect-counters.json`
- Create: `/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/evidence-short-confirmations/report.md`

**Interfaces:**
- Consumes: exact final `config/v2_luna_system_prompt.txt` and `openai-codex/gpt-5.6-luna` through the existing zero-tool child command.
- Produces: sanitized evidence that short forms classify correctly while all external effect counters remain zero.

- [ ] **Step 1: Expand the sandbox case contract**

Change each case to `(case_id, message, expected_intent, has_pending_action)` and use this exact matrix:

```python
CASES = (
    ("short_yes", "Sim", "confirm", True),
    ("short_reserve", "Pode reservar", "confirm", True),
    ("short_can_yes", "Pode sim", "confirm", True),
    ("short_confirmed", "Confirmado", "confirm", True),
    ("short_exactly", "Isso mesmo", "confirm", True),
    ("short_please", "Sim, por favor", "confirm", True),
    (
        "long_confirmation",
        "Pode reservar esse passeio para essa data e gerar o link do sinal no cartão.",
        "confirm",
        True,
    ),
    ("no_pending_short_yes", "Sim", "inform", False),
    ("uncertain", "Talvez, ainda estou pensando.", "inform", True),
    ("question", "Quanto fica o sinal mesmo?", "inform", True),
    ("material_change", "Sim, mas agora são duas pessoas.", "adjust", True),
    ("refusal", "Não reserve, deixa para depois.", "adjust", True),
)
```

Build each `ModelRequest` with:

```python
pending_action=pending if has_pending_action else None
```

For `confirm`, require exact version, exact actions, `contextual_reference`, `facts == ()` and zero model effect proposals. Record model `read_requests`, but do not treat them as authority or a sandbox failure: the parent ignores child-authored reads during confirmation and derives the authenticated reread from the frozen draft. For all non-confirm cases, require no confirmation fields and zero effect proposals.

- [ ] **Step 2: Run the real-model sandbox**

Run from the repository worktree:

```bash
env -u V2_APPROVAL_SANDBOX_CASE -u V2_APPROVAL_SANDBOX_RAW_ONLY \
  HERMES_HOME=/home/ubuntu/.hermes \
  PYTHONPATH=/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout \
  V2_APPROVAL_SANDBOX_REPO=/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout \
  V2_APPROVAL_SANDBOX_EVIDENCE_DIR=/home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/evidence-short-confirmations \
  V2_APPROVAL_SANDBOX_PROMPT_FILE=/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout/config/v2_luna_system_prompt.txt \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python \
  /home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/run_probe.py
```

Expected: `case_count=12`, `passed_count=12`, `failed_cases=[]`, `verdict="pass"`, `toolsets=[]`, `business_effect_clients=[]`, and `operational_stores=[]`.

- [ ] **Step 3: Write and verify zero-effect evidence**

Write `effect-counters.json` with observed integer counters:

```json
{
  "provider_write_calls": 0,
  "stripe_links_created": 0,
  "manychat_messages_sent": 0,
  "handoffs_sent": 0,
  "payment_completed": 0,
  "model_effect_proposals": 0
}
```

Write `report.md` with the exact HEAD, prompt SHA-256, runner SHA-256, twelve case outcomes and a statement that the harness imports no writer/store/sender clients. Scan every evidence file for secrets, tokens, private bindings, real customer PII and internal provider references before reporting success.

---

### Task 4: Final regression, documentation and candidate verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-27-natural-reservation-confirmation-design.md`
- Verify: all tracked files in the current worktree

**Interfaces:**
- Consumes: committed runtime change and passing real-model evidence.
- Produces: one final verified HEAD with an implemented design status and no public rollout.

- [ ] **Step 1: Mark the design implemented**

Change only the status line to:

```text
Status: implementado e validado em sandbox sem efeitos; rollout público não autorizado
```

Add a short validation note under the criteria recording the fresh focused count, full regression count, real-model `12/12`, evidence directory and zero-effect counters.

- [ ] **Step 2: Run static checks**

Run:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m compileall -q \
  v2_contracts v2_application v2_adapters v2_host \
  tests/test_v2_critical_actions.py tests/test_v2_turn_executor.py
git diff --check
```

Expected: exit code 0 and no output from either check.

- [ ] **Step 3: Run the clean full regression**

Run:

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

Expected: exit code 0, no failures and exactly seven documented deselections. Record the fresh pass/subtest counts; do not reuse earlier counts.

- [ ] **Step 4: Commit documentation and verify provenance**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-27-natural-reservation-confirmation-design.md
git commit -m "docs: record short confirmation validation"
git diff HEAD^ HEAD --check
git status --short
git log -4 --oneline
sha256sum config/v2_luna_system_prompt.txt \
  /home/ubuntu/workspace/maya-v2-approval-sandbox-20260728/run_probe.py
```

Expected: worktree contains only `?? uv.lock`; the report hashes match this exact final prompt and runner. Do not push or deploy.

## Self-Review Results

- Spec coverage: short affirmative forms, no-pending denial, informational detour, material change, uncertainty, questions, refusal, TTL, exact v3 binding, reread, atomicity, replay and zero effects each map to an explicit test or sandbox case.
- Scope boundary: no provider, payment, ManyChat, handoff, deployment or capability behavior changes; only semantic classification and the obsolete text-length downgrade are changed.
- Placeholder scan: no `TBD`, `TODO`, `FIXME`, “implement later”, or unspecified test step remains.
- Type consistency: `PendingCriticalActionContext`, `ModelProposal`, `ApprovalBasis.CONTEXTUAL_REFERENCE`, `confirmed_summary_version` and `confirmed_action_kinds` match the existing production contracts.
- Safety: `confirm` still forbids material facts; all authorization remains runtime-bound; real-model validation has zero tools, clients and operational stores.

## Execution Mode

Execute inline in this session using `superpowers:executing-plans`, following RED → GREEN → focused regression → zero-effect real-model sandbox → clean full regression. Do not dispatch subagents or perform public rollout.
