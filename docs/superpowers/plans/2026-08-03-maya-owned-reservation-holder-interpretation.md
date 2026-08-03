# Maya-Owned Reservation-Holder Interpretation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove deterministic private extraction/redaction from ingress so Maya receives the original message, semantically identifies reservation-holder name/email/country, and the parent validates/persists those facts without weakening reservation-effect fences.

**Architecture:** Keep the existing closed `ModelProposal.facts` grammar. Send `InboundBatch.combined_text` unchanged on every productive model call, partition holder facts immediately after each model response, canonicalize and persist them in the private owner, reconstruct a public-only proposal, then continue through reads and summary. An authenticated journal or any accepted model holder fact marks the aggregate turn as update-bearing and permanently denies reservation commands/relays for that turn.

**Tech Stack:** Python 3.12, frozen dataclasses, SQLite private owner, pytest, Ruff, existing V2 fake audited model/read transports.

## Global Constraints

- Worktree: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`.
- Branch: `maya-v2-operational-readiness`; baseline `b110662461dca1ce5d2f15c0d6bfe366b9ce00a2`.
- Exact design authority: `docs/superpowers/specs/2026-08-03-maya-owned-reservation-holder-interpretation-design.md`.
- No regex or deterministic parser may identify or assign holder name, email, country, or typed phone before Maya.
- Maya receives `batch.combined_text` exactly, including customer-supplied PII.
- The parent canonicalizes and persists only model-produced `full_name`, `email`, and `country_code`.
- `phone_e164` remains ManyChat/WhatsApp-binding-only.
- Private facts must be stripped before reducer, public projection, typed facts, Maya proposal artifact, kernel, logs, evidence, and generic errors.
- Same-turn summary is allowed after persistence; reservation command/relay is forbidden until a later aggregate turn.
- Correction revokes the old summary and may present a new one in the same turn.
- Preserve exactly-once and at most one provider POST; use only fake transports and `.invalid` hosts.
- Run with `PYTHONDONTWRITEBYTECODE=1` and pytest `-p no:cacheprovider`.
- Official config: `/home/ubuntu/chapada-leads-hermes/config/leads_agent.yaml`.
- Stop before push, CI dispatch, deploy, runtime start, WhatsApp/ManyChat delivery, payment, or real provider traffic.

---

### Task 1: Raw model context and holder semantics contract

**Files:**
- Modify: `v2_adapters/hermes_model.py:123-137`
- Modify: `tests/test_v2_hermes_model_adapter.py:122-158`
- Test: `tests/test_v2_hermes_model_adapter.py`

**Interfaces:**
- Consumes: `ModelRequest.message: str`.
- Produces: `_request_wire(request, prompt)` with the exact message and a system suffix defining reservation-holder attribution.

- [ ] **Step 1: Replace the marker-only wire test with a raw-context RED**

Create a `ModelRequest` whose message includes distinct lead and spouse names/emails, country, and typed phone. Decode `_request_wire()` and assert:

```python
assert user["message"] == original_message
assert "[private" not in user["message"]
assert lead_email in user["message"]
assert spouse_email in user["message"]
```

Assert the system prompt contains closed semantic requirements:

```python
assert "reservation holder" in prompt
assert "spouse" in prompt
assert "third party" in prompt
assert "explicitly" in prompt
assert "phone_e164" in prompt
assert "do not guess" in prompt
```

- [ ] **Step 2: Run the RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/chapada-leads-hermes/venv/bin/python \
  -m pytest -q -p no:cacheprovider \
  tests/test_v2_hermes_model_adapter.py::test_original_private_context_reaches_maya_with_holder_semantics
```

Expected: FAIL because the current suffix describes bracket markers and collection-only behavior rather than semantic holder attribution.

- [ ] **Step 3: Replace `_PRIVATE_PROFILE_SYSTEM_SUFFIX`**

Write a closed prompt that requires:

```text
- current message is complete original customer text;
- output holder full_name/email/country_code only from semantic attribution;
- self-identification belongs to lead;
- spouse/companion/passenger/hostel/property/agency/third-party data does not belong to holder by default;
- explicit designation of another person as holder is authoritative;
- ambiguity -> natural clarification, no guessed fact/read/select/confirm/effect;
- country_code must be ISO alpha-2;
- never output phone_e164 from message;
- newly produced holder facts may accompany a read; parent persists before dispatch;
- avoid unnecessary PII echo in reply.
```

Remove every instruction about bracket markers, parent-captured values, deterministic collection-only turns, and redacted current messages.

- [ ] **Step 4: Verify Task 1 GREEN**

Run the new selector and the full adapter file. Expected: both PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py
git commit -m "feat: give Maya full holder context"
```

---

### Task 2: Remove deterministic extraction and persist Maya facts before progress

**Files:**
- Delete: `v2_application/private_customer_collection.py`
- Delete: `tests/test_v2_private_customer_collection.py`
- Modify: `v2_application/turn_executor.py:60-75, 1010-1162, 1574-1708, 1737-1817, 1938-2066, 2157-2161, 2333-2338`
- Modify: `tests/test_v2_turn_executor.py:1561-1786`

**Interfaces:**
- Consumes: `ModelProposal.facts` from first, semantic-review, or post-read model stages.
- Produces: private-owner writes before reads; public-only proposal; `private_update_turn: bool` covering authenticated journal and all accepted holder facts.

- [ ] **Step 1: Write RED proving no preprocessing and same-turn summary**

Modify the existing same-turn test so the fake Maya first response contains:

```python
facts=(
    ModelFact("full_name", holder_name),
    ModelFact("email", holder_email),
    ModelFact("country_code", "BR"),
)
```

and a lodging read. Assert:

```python
assert model.calls[0].message == original_message
assert model.calls[1].message == original_message
assert private_store.load(batch.lead_id).full_name == holder_name
assert read_port.calls == [read_request]
assert "Só para confirmar" in result.reply_chunks[0]
assert result.receipt.command_rows == ()
assert result.receipt.relay_rows == ()
```

Also inspect persisted artifacts and projection canonical bytes to prove private values are absent.

- [ ] **Step 2: Run and classify RED**

Run the exact same-turn selector. Expected: FAIL because ingress currently rewrites the model message and model-produced holder facts force `collection_only`.

- [ ] **Step 3: Delete deterministic collector imports and ingress call**

Remove:

```python
from v2_application.private_customer_collection import ...
private_collection = collect_private_customer_facts(...)
```

Construct first and follow-up requests with:

```python
message=batch.combined_text
```

Remove deterministic invalid/phone state and all `sanitized_message` references.

- [ ] **Step 4: Add a public-only proposal reconstruction helper**

After `_partition_private_customer_facts`, rebuild a proposal using `dataclasses.replace`:

```python
public_proposal = replace(proposal, facts=public_facts)
```

If accepted private facts exist, call `_persist_private_collection()` before read dispatch and recompute readiness. Rebuild the corresponding `AuditedModelTurn` from the original commitment frames and public-only proposal so no private structured fact reaches Maya public artifacts.

Use a turn flag initialized as:

```python
private_update_turn = bool(journal_fact_names)
```

and OR it with `bool(accepted_private_facts)` at every stage. Do not derive it from regex or raw-text matching.

- [ ] **Step 5: Preserve invalid and phone behavior without making phone a progress gate**

- invalid holder fields: drop, generic natural correction, no reads/commands;
- model `phone_e164`: drop unconditionally, never persist, but do not force acknowledgement-only when authenticated profile phone is already valid;
- incomplete authenticated phone: existing readiness filter allows only local knowledge reads and zero commands.

- [ ] **Step 6: Preserve no-command transformations**

Apply `_private_update_no_command_proposal()` to public-only first and final proposals before confirmation-derived reads and after post-read output. Keep assertions:

```python
if private_update_turn and execution_commands: raise TurnExecutionError(...)
if private_update_turn and (command_rows or command_relays): raise TurnExecutionError(...)
```

- [ ] **Step 7: Remove obsolete files**

Delete the extractor module and regex-focused unit tests. Confirm no source import/reference remains:

```bash
python - <<'PY'
from pathlib import Path
root = Path('.')
for p in root.rglob('*.py'):
    if '__pycache__' not in p.parts and 'collect_private_customer_facts' in p.read_text(errors='ignore'):
        raise SystemExit(p)
PY
```

- [ ] **Step 8: Verify Task 2 GREEN**

Run the same-turn selector, invalid model-fact selector, crash/retry selector, and full `tests/test_v2_turn_executor.py`. Expected: PASS.

- [ ] **Step 9: Commit Task 2**

```bash
git add -A v2_application/private_customer_collection.py \
  tests/test_v2_private_customer_collection.py \
  v2_application/turn_executor.py tests/test_v2_turn_executor.py
git commit -m "feat: persist Maya-interpreted holder facts"
```

---

### Task 3: Lead-versus-third-party semantic witnesses

**Files:**
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py`

**Interfaces:**
- Consumes: semantic `ModelProposal.facts`; the controller has no raw-text attribution logic.
- Produces: causal fixtures for self-holder, third-party non-holder, explicitly designated other holder, ambiguity, and typed phone.

- [ ] **Step 1: Add parametrized attribution fixtures**

Create cases where the raw messages contain regex-shaped valid PII:

```text
"Eu sou Ana Lead... Minha esposa Beatriz..."
"A acompanhante é Beatriz..."
"O e-mail do hostel é..."
"Beatriz será a titular da reserva..."
```

Drive each through `FakeAuditedModel` with semantically correct output:

- self-holder -> Ana facts;
- spouse/companion/hostel mention -> no holder facts and clarification/inform reply;
- explicitly designated Beatriz -> Beatriz facts.

Assert private-store state follows only the model's structured holder facts, not which regex-shaped values appear in input.

- [ ] **Step 2: Add ambiguity RED**

Use a message containing two complete candidate identities with no holder designation. Fake Maya returns no private facts and a natural question. Assert no private row, provider read, command, or relay.

The test initially fails if any deterministic extraction remains.

- [ ] **Step 3: Add conversational-phone witness**

Give the message a typed phone different from authenticated ManyChat. Fake Maya may maliciously emit `phone_e164`; assert:

```python
assert effective_customer.phone_e164 == authenticated_phone
assert "phone_e164" not in private_store.load(...).present_fact_names
assert typed_phone not in public projection/artifacts/errors
```

When the authenticated binding lacks/fails freshness, assert zero provider reads/commands even though typed phone is present.

- [ ] **Step 4: Add prompt-bound semantic unit tests**

Assert the adapter suffix explicitly carries the third-party rules and that no test expects regex attribution. These are contract tests for the real Maya child; fake model tests prove parent behavior independently.

- [ ] **Step 5: Run Task 3 tests**

Run new selectors, full executor, full Hermes adapter, and split-origin E2E. Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add tests/test_v2_turn_executor.py \
  tests/test_v2_split_origin_cloudbeds_e2e.py \
  tests/test_v2_hermes_model_adapter.py
git commit -m "test: bind holder identity to Maya semantics"
```

---

### Task 4: Corrections, retries, artifacts, and one-shot execution

**Files:**
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify only if a RED requires it: `v2_application/turn_executor.py`

**Interfaces:**
- Consumes: private update flag and authenticated journal.
- Produces: fresh-summary correction, crash/retry safety, committed replay, technical-artifact privacy, and exactly-once provider evidence.

- [ ] **Step 1: Convert split-origin E2E to model-owned holder facts**

Remove reliance on extractor-shaped text. Have the first fake Maya proposal emit holder facts plus the read request. Preserve the flow:

```text
collect + read + summary -> later confirm -> one fake Cloudbeds POST -> committed replay
```

Assert one summary turn is removed and later natural confirmation remains mandatory.

- [ ] **Step 2: Convert correction witness**

For a pending summary, provide original text containing the corrected holder identity and have Maya return corrected private facts plus read/selection. Assert:

- old summary/capability is invalidated;
- new summary is bound to corrected snapshot;
- same correction turn has zero command/relay;
- a rogue `confirm` proposal is demoted before derived confirmation reads;
- later confirmation binds to new summary only.

- [ ] **Step 3: Convert crash/retry journal witness**

Inject boundary failure after private persistence but before boundary receipt. Retry the same aggregate batch with the original raw message. Assert the authenticated journal:

- does not force acknowledgement-only;
- keeps `private_update_turn=True`;
- permits a new summary;
- never permits command/relay in that aggregate turn;
- rejects divergent event hash/material generically.

- [ ] **Step 4: Add artifact privacy witness**

Query `boundary_turn_artifacts`, public projection, receipt, outboxes, and object `repr`. Assert raw holder facts and Cloudbeds reservation ID are absent from technical artifacts while the fake model call received the original message.

Only frame hashes/commitments may bind raw transient exchanges.

- [ ] **Step 5: Verify one-shot fake Cloudbeds path**

Assert:

```python
assert transport.post_calls == 1
assert replay.deduplicated is True
assert transport.post_calls == 1
```

Use `httpx.MockTransport` and `.invalid`; never use network.

- [ ] **Step 6: Run Task 4 tests and commit**

Run full split-origin E2E and relevant provider/monotonic/exactly-once files. Commit only after green:

```bash
git add tests/test_v2_split_origin_cloudbeds_e2e.py \
  tests/test_v2_turn_executor.py v2_application/turn_executor.py
git commit -m "test: preserve private correction and one-shot booking"
```

---

### Task 5: Documentation, qualification, and final review

**Files:**
- Modify: `v2_application/private_customer_facts.py:1-6`
- Modify: `docs/refactor/ACTIVE.md`
- Modify skill reference: `bounded-software-task-execution/references/private-profile-and-sqlite-owner-boundaries.md` via `skill_manage`

**Interfaces:**
- Produces: current architectural documentation, exact local qualification evidence, frozen SHA/tree, and independent read-only verdict.

- [ ] **Step 1: Correct stale privacy documentation**

Update the private-owner module docstring: stored values never enter public projection/artifacts/logs/evidence, but the raw current customer message is authorized transient model context. Remove statements claiming only markers may reach Maya.

Update the skill reference to distinguish transient model context from durable technical evidence and to prohibit deterministic holder attribution.

- [ ] **Step 2: Run focused suites**

At minimum:

```bash
pytest -q -p no:cacheprovider \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_customer_collection.py \
  tests/test_v2_private_customer_facts.py \
  tests/test_v2_effective_customer_profile.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_split_origin_cloudbeds_e2e.py
```

Then run proportional provider/payment/composition regressions.

- [ ] **Step 3: Run official local suite once on the final candidate**

Use clean environment and seven authenticated historical exclusions:

```bash
env -i HOME=/home/ubuntu \
  PATH=/home/ubuntu/chapada-leads-hermes/venv/bin:/usr/bin:/bin \
  PYTHONDONTWRITEBYTECODE=1 \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
    --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution \
    --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing \
    --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path \
    --deselect=tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only \
    --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch \
    --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts \
    --deselect=tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```

- [ ] **Step 4: Run static gates**

Run Ruff on V2 plus the boundary SQLite files, `scripts/check_fasttrack_boundaries.py`, compileall, and `git diff --check`.

- [ ] **Step 5: Update and commit `ACTIVE.md`**

Record exact focused counts, official count, static gates, operational closure, and the candidate code SHA/tree. Commit the ledger and freeze the resulting HEAD/tree with a clean worktree.

- [ ] **Step 6: Obtain independent exact-SHA review**

Review must authenticate HEAD/tree/status and inspect the delta from `b110662...`. Required focus:

- no deterministic private extractor/import/gate;
- exact original message on every Maya productive call;
- semantic third-party attribution prompt/tests;
- parent validation and private persistence before reads;
- private stripping before public reduction/artifacts;
- ManyChat-only phone;
- correction/new summary/no-command;
- authenticated retry journal;
- at-most-one provider POST and private reservation ID;
- no lateral protocol relaxation.

Require `VERDICT: CLEAR` or causal `VERDICT: BLOCKED`.

- [ ] **Step 7: Stop boundary**

Do not push, create PR, dispatch remote CI, deploy, start runtime, send WhatsApp/ManyChat, initiate payment, or call a real provider. Report exact local evidence and review verdict only.
