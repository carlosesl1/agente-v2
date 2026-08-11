# Maya V2 Minimal Conversation Envelope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` and execute every task in order with causal RED/GREEN evidence. This plan is intentionally executed inline without subagents because the controller/model authority boundary is the critical path.

**Goal:** Remove parent-known mechanics and duplicated prose from Maya's productive response, require strict V8 structured output, and reject unsupported material claims while preserving Maya as the sole public author.

**Architecture:** Maya returns eight conversational/semantic fields. `HermesModelAdapter` projects those fields into the existing internal `ModelProposal` using exact parent authority. A tool-free child requests a strict provider JSON Schema. A separate closed reviewer checks only material provider-backed claims and can request one Maya-owned correction; it never writes public text or authorizes effects.

**Tech stack:** Python 3.12, frozen dataclasses, closed JSON, OpenAI Responses/Codex through Hermes Agent 0.19.0, SQLite boundary store, pytest, Ruff.

**Design authority:** `docs/superpowers/specs/2026-08-11-maya-v2-minimal-conversation-envelope-design.md`

**Starting commit:** `ce5d5e80384da0ae05fc39ddd84e859b9b42b336`

## Global constraints

- Do not add regex, substring, keyword, alias, stage, or deterministic natural-language interpretation.
- Do not inspect, change, retry, block, mask, or fail public text because it contains customer-supplied data.
- Do not let controller/application code author, append, prepend, replace, translate, or canonicalize Maya's customer-facing text.
- Preserve all reservation, payment, handoff, provider, receipt, capability, idempotency, writer/reconciler, and replay gates.
- Real-model tests use the real model only; every commercial provider is fake and every effect path remains disabled.
- No push, deploy, restart, canary, provider write, or promotion.
- Keep V1-V7 read compatibility for frozen fixtures. Productive requests require V8.
- One expensive conversational gate is run only after freezing an immutable candidate SHA.

## Baseline

The full unfiltered suite at the starting commit produced `1731 passed, 7 failed, 2953 subtests passed`. The seven failures are the already documented stale Phase 7/index contracts and not regressions:

```text
tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only
tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts
tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch
tests/test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path
tests/test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution
tests/test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing
tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```

Every canonical command below explicitly deselects only those seven exact nodes.

---

### Task 1: Add causal RED tests for the minimal V8 envelope

**Files:**
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`
- Read: `v2_adapters/hermes_model.py:51-73,451-678,803-881`
- Read: `v2_contracts/model.py:275-535`

**Interfaces:**
- Productive response has exactly eight keys: `intent`, `reply_chunks`, `facts`, `read_requests`, `selected_choice_refs`, `selection_requested`, `pending_action_disposition`, `passengers`.
- `reply_chunks` contains `{text, expects_reply}` objects.
- `_proposal(..., request=ModelRequest)` adds parent-owned mechanics.

- [ ] Add `test_v8_question_is_authored_once_and_parent_binds_source_event` with a payload that contains no schema/event ID and has a final `expects_reply=true` chunk. Assert internal `source_event_id` equals the request, `reply_chunks` preserve exact bytes, and `clarification_question` is the final text.
- [ ] Add `test_v8_rejects_multiple_or_nonfinal_expects_reply_chunks` for both invalid shapes.
- [ ] Add `test_v8_has_only_eight_conversational_fields` and reject every removed V7 field in a V8 productive response.
- [ ] Add `test_v8_read_identity_and_locale_are_parent_owned`: input read contains no `request_id` or `locale`; internal `ReadRequest` receives canonical event-derived ID and authenticated request locale.
- [ ] Add `test_v8_confirm_binding_is_parent_owned`: `intent=confirm` with live pending action gets exact summary/action/basis; the same payload without pending action fails closed.
- [ ] Add `test_v8_has_no_effect_output_and_internal_effects_are_empty`.
- [ ] Run only those tests and witness failures because V8 is unsupported.

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_hermes_model_adapter.py -k 'v8_'
```

Expected: causal failures at schema/field decoding, not test setup.

- [ ] Commit only RED tests:

```bash
git add tests/test_v2_hermes_model_adapter.py tests/test_v2_profile_and_model_grammar.py
git commit -m "test: expose Maya V8 mechanical envelope burden"
```

---

### Task 2: Define one shared strict V8 schema

**Files:**
- Create: `v2_contracts/model_wire.py`
- Create: `tests/test_v2_model_wire.py`
- Modify: `v2_contracts/__init__.py` only if exports are required

**Interfaces:**
- Export `V8_RESPONSE_FIELDS` and `V8_RESPONSE_JSON_SCHEMA`.
- Export `canonical_v8_response_schema()` only if callers need a defensive deep copy.
- Schema name: `v2_model_proposal_v8` at provider level; no `schema` key in model output.

- [ ] RED: assert exactly eight top-level properties, all required, `additionalProperties=false`, one/two reply chunks, and bounded arrays.
- [ ] Implement a closed JSON Schema:
  - `intent`: exact five-value enum;
  - reply item: exact `{text, expects_reply}`;
  - facts: exact `{name, value}`, bounded and runtime cross-validated;
  - reads: exact tagged union for knowledge, lodging, activity, room description, and activity description with no request/event/locale IDs;
  - selected choice refs: max two;
  - `selection_requested`: boolean;
  - `pending_action_disposition`: `null|preserve|revoke`;
  - passengers: exact bounded objects with required nullable values.
- [ ] Return immutable-by-convention constants; tests mutate a deep copy, not the shared object.
- [ ] Run:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_model_wire.py
```

- [ ] Commit:

```bash
git add v2_contracts/model_wire.py tests/test_v2_model_wire.py
git commit -m "feat: define strict Maya V8 response schema"
```

---

### Task 3: Decode V8 and project parent-owned mechanics

**Files:**
- Modify: `v2_adapters/hermes_model.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_turns.py`
- Modify: `tests/test_v2_package_confirmation.py`

**Interfaces:**
- Keep legacy `_proposal(payload, source_event_id, require_v7=...)` behavior for V1-V7 fixtures.
- Add a productive V8 decoder that receives the complete `ModelRequest`.
- New productive calls require V8; explicit legacy fixture calls remain supported.

- [ ] Add `_v8_reply_chunks()` that validates exact objects, one/two items, at most one `expects_reply=true`, and true only on the final item. Return exact text tuple plus optional exact clarification text.
- [ ] Add `_v8_read_request(value, request, index)`:
  - validate the exact tagged read fields;
  - set `request_id=f"{request.source_event_id}:read:{canonical-kind-suffix}"`;
  - set locale from `request.locale` only where the internal `ReadRequest` accepts it;
  - parse ISO dates and delegate remaining type/range checks to `ReadRequest`.
- [ ] For `intent=confirm`, require `request.pending_action`; bind exact summary version/action tuple and `ApprovalBasis.CONTEXTUAL_REFERENCE`. For non-confirm, leave all three empty.
- [ ] Set `source_event_id` from `request`, `effect_proposals=()`, and derive internal clarification from the final reply item.
- [ ] Map `pending_action_disposition` to internal `pending_disposition` and retain all existing `ModelProposal` invariants.
- [ ] In `HermesModelAdapter._attempt`, decode productive frames as V8 with the request. Keep explicit legacy decoding only in compatibility/unit call sites.
- [ ] Remove protocol repair instructions about copying source IDs or duplicating clarification. Protocol repair may still retry one malformed structured frame but cannot add parent-authored text.
- [ ] Run focused tests:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turns.py \
  tests/test_v2_package_confirmation.py
```

- [ ] Commit:

```bash
git add v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turns.py tests/test_v2_package_confirmation.py
git commit -m "feat: project Maya V8 semantics onto parent authority"
```

---

### Task 4: Replace offer IDs with bounded choice references

**Files:**
- Modify: `v2_adapters/hermes_model.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_package_confirmation.py`
- Modify: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Model observation wire exposes `choice_ref`; it does not expose `offer_id`.
- Internal observations and `ModelProposal.target_offer_id(s)` continue using exact opaque offer IDs.

- [ ] RED: create current observations with multiple lodging/activity offers; assert wire contains deterministic `lodging:1`, `lodging:2`, `activity:1`, etc., contains no public `offer_id`, and V8 selection maps only current refs to exact internal IDs.
- [ ] Implement one `_choice_index(request.observations)` that traverses closed public payloads without interpreting customer text. It may inspect typed payload keys/offer arrays only.
- [ ] Replace each model-visible `offer_id` with a unique type-local `choice_ref`; preserve all other sanitized public fields.
- [ ] Let room-description reads use `choice_ref` and project back to `offer_id`.
- [ ] Map zero/one/two selected refs to internal `target_offer_id`/`target_offer_ids` using existing service/package invariants.
- [ ] Reject stale, duplicate, unknown, same-kind package, or more-than-two refs before planning.
- [ ] Run focused selection/package/executor tests.
- [ ] Commit:

```bash
git add v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_package_confirmation.py tests/test_v2_turn_executor.py
git commit -m "feat: give Maya bounded conversational choice references"
```

---

### Task 5: Reduce the Maya prompt to V8 conversational duties

**Files:**
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `tests/test_v2_luna_prompt.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`

**Interfaces:**
- Prompt describes only the eight V8 fields.
- Confirmation review also uses V8 and parent-owned binding.

- [ ] RED: assert the prompt no longer asks Maya to emit/copy `schema`, `source_event_id`, read `request_id`, read `locale`, `effect_proposals`, `target_offer_id(s)`, `confirmed_summary_version`, `confirmed_action_kinds`, `approval_basis`, or duplicated `clarification_question`.
- [ ] Rewrite the closed contract section and examples to V8:
  - author one/two `{text, expects_reply}` messages;
  - emit semantic facts/reads/choices only;
  - use `intent=confirm` without copying bindings;
  - use `pending_action_disposition` only for preserve/revoke;
  - never claim beyond observations.
- [ ] Update read and selection examples to omit mechanical IDs and use `choice_ref`.
- [ ] Reduce `_CONFIRMATION_REVIEW_SYSTEM_PROMPT`, `_PROTOCOL_REPAIR_SUFFIX`, and correction suffix to V8; do not add phrase lists or more retry loops.
- [ ] Run prompt/grammar/adapter tests and commit.

```bash
git add config/v2_luna_system_prompt.txt v2_adapters/hermes_model.py \
  tests/test_v2_luna_prompt.py tests/test_v2_profile_and_model_grammar.py
git commit -m "refactor: leave Maya only conversational V8 duties"
```

---

### Task 6: Prove and wire strict provider structured output

**Files:**
- Modify: `scripts/phase8_hermes_child.py`
- Modify: `v2_host/hermes_child.py`
- Modify: `tests/test_v2_hermes_child.py`
- Modify: `tests/test_phase8_fasttrack_sandbox.py`
- Modify: `compose.v2.yaml` only if the direct-child argument contract changes
- Read/verify: Hermes Agent 0.19.0 `agent/transports/codex.py` and `agent/codex_responses_adapter.py`

**Interfaces:**
- `AIAgent(..., request_overrides={"extra_body": {"text": {"format": ...}}})` for Codex Responses.
- Tools remain empty; session DB/logs live only in a temporary `0700` directory.
- Result wire remains `PHASE8_RESULT\0` plus canonical V8 JSON.

- [ ] Add unit RED proving the child passes the exact strict schema in `request_overrides`, does not invoke CLI text extraction, and rejects a final response that is not one exact V8 object.
- [ ] Add an isolated capability probe script/test that sends a minimal synthetic V8 request through `openai-codex`/`gpt-5.6-luna`, with no business providers or tools. Record request schema digest, provider/model, exit, and canonical response digest; never record credentials.
- [ ] Execute the real capability probe before production transport edits. If the endpoint rejects strict output, stop this task and report the blocker; do not implement Markdown extraction or extra retries.
- [ ] On success, make both child paths use direct tool-free `AIAgent` with the same shared schema and request overrides. For the production child:
  - parse only exact `--profile`, `--provider`, and `-m/--model` values from the configured command;
  - resolve the named profile with Hermes' supported profile resolver while retaining the mounted root Hermes home;
  - use disposable `SessionDB` and logs;
  - never load memory, tools, soul, context files, or durable sessions.
- [ ] Delete `_extract_json()` fence/brace scanning from the production child. Canonicalize only a complete parsed object.
- [ ] Run child/sandbox/composition tests and one post-change real provider probe.
- [ ] Commit:

```bash
git add scripts/phase8_hermes_child.py v2_host/hermes_child.py \
  tests/test_v2_hermes_child.py tests/test_phase8_fasttrack_sandbox.py compose.v2.yaml
git commit -m "feat: require structured Maya V8 generation"
```

---

### Task 7: Add a narrow material-grounding reviewer

**Files:**
- Modify: `v2_contracts/model.py`
- Modify: `v2_contracts/ports.py`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Add closed request/decision contracts for material grounding.
- Reviewer categories: `price_availability`, `policy`, `operational_status`, `safety_eligibility_suitability`.
- Decision: `accept|unsupported_claim`, bound to exact proposal and observation digests.

- [ ] RED: fake provider observation describes Buracão but has no age/suitability guidance; Maya proposes “67 anos não impede e não exige confirmação”; reviewer returns unsupported; executor must request exactly one Maya-owned correction and commit only the corrected reply.
- [ ] RED: second unsupported/malformed result yields zero public reply/command/relay/delivery/provider-write rows.
- [ ] RED: a reply repeating synthetic customer name/e-mail/phone never invokes the reviewer by itself.
- [ ] Add frozen contracts:
  - `GroundingCategory` enum;
  - `GroundingReviewRequest` with exact current message, exact sanitized observations, exact Maya chunks, proposal digest, observation digest, and closed category tuple;
  - `GroundingReviewDecision` with exact bound digests and unsupported categories;
  - audited review frame/turn type if needed for transcript authentication.
- [ ] Extend `AuditedModelPort` with `review_grounding()` and implement it in `HermesModelAdapter` using a tool-free strict schema separate from V8. It has no facts, reads, choices, confirmation, handoff, effect, or public-text output.
- [ ] Invoke the reviewer only after accepted observations when the typed read kind or exact operational context can support a material claim category. Category selection comes from read kind/status types, never prose.
- [ ] On `unsupported_claim`, call the existing one-shot `_request_public_reply_correction()` with `UNSUPPORTED_OBSERVATION_CLAIM`, exact observations, and no new reads/effects. Maya remains the sole author.
- [ ] Prevent nested progress/selection/confirmation/correction loops. One reviewer and one correction maximum per turn.
- [ ] Run focused contract/adapter/executor tests and commit.

```bash
git add v2_contracts/model.py v2_contracts/ports.py v2_adapters/hermes_model.py \
  v2_application/turn_executor.py tests/test_v2_profile_and_model_grammar.py \
  tests/test_v2_hermes_model_adapter.py tests/test_v2_turn_executor.py
git commit -m "feat: gate unsupported material claims without rewriting Maya"
```

---

### Task 8: Make missing Buracão evidence explicit

**Files:**
- Modify: `v2_adapters/bokun.py:229-258`
- Modify: `tests/test_v2_bokun_party_reads.py`
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `tests/test_v2_turn_executor.py`
- Modify only the external qualification runner copy under the new evidence directory; never mutate the historical runner in place
- Read/verify unchanged unless the provider exposes typed guidance: `v2_adapters/provider_http.py:1277-1292`

**Interfaces:**
- Activity description payload contains exact nullable `age_guidance` and `suitability_guidance`.

- [ ] Add RED tests in `tests/test_v2_bokun_party_reads.py` for a transport response with no guidance and one with explicit synthetic guidance.
- [ ] In `BokunReadAdapter._description()`, emit `null` when the transport response has no exact guidance. Accept only `None` or a non-empty trimmed string; never derive guidance from age words, difficulty labels, or generic descriptions.
- [ ] Keep `BokunHTTPTransport` unchanged while the upstream payload has no dedicated typed guidance field. Its absence is authority-preserving and becomes explicit null in the read adapter.
- [ ] Update Maya prompt: null means unknown, not allowed or prohibited.
- [ ] Update the copied fake provider payload used by Task 10 only; no real provider call.
- [ ] Run provider/read/executor tests and commit.

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_bokun_party_reads.py tests/test_v2_turn_executor.py -k 'description or grounding or buracao'
git add v2_adapters/bokun.py tests/test_v2_bokun_party_reads.py \
  config/v2_luna_system_prompt.txt tests/test_v2_turn_executor.py
git commit -m "feat: represent unknown activity suitability evidence explicitly"
```

---

### Task 9: Deterministic verification and independent audit

**Files:**
- Verify all modified code/tests/docs
- Update: `docs/refactor/ACTIVE.md` with local candidate identity and explicit rollout prohibition

- [ ] Run focused suite:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_model_wire.py \
  tests/test_v2_profile_and_model_grammar.py \
  tests/test_v2_luna_prompt.py \
  tests/test_v2_hermes_child.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_package_confirmation.py \
  tests/test_v2_turns.py
```

- [ ] Run Ruff critical checks and compileall:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m ruff check \
  --select E9,F401,F821 v2_contracts v2_adapters v2_application v2_host scripts tests
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m compileall -q \
  v2_contracts v2_adapters v2_application v2_host scripts
```

- [ ] Run canonical suite with exactly the seven baseline deselections:

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  --deselect tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only \
  --deselect tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts \
  --deselect tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch \
  --deselect tests/test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path \
  --deselect tests/test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution \
  --deselect tests/test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing \
  --deselect tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```

- [ ] Run `git diff --check`, inspect every changed hunk, verify no new customer-text parser/regex/alias/keyword branch, and verify no customer-data output mechanism.
- [ ] Obtain one independent immutable-candidate audit for Critical/Important/Minor findings. Fix findings causally and rerun affected gates.
- [ ] Commit the deterministic candidate and record commit/tree/patch digest. Do not push.

---

### Task 10: One real-conversation qualification gate

**Files:**
- Reuse immutable historical runner from `/home/ubuntu/maya-v2-model-owned-evidence-9165a51/runner/run_random_conversations.py`
- Create a new external evidence directory under `/home/ubuntu/`
- Do not modify the repository during qualification

- [ ] Authenticate candidate commit/tree, clean worktree, runner digest, model/provider, disposable Hermes home, and fake-provider configuration.
- [ ] Run exact historical messages:
  - `vague_date`: 10/10;
  - `same_day_invalid_range`: 10/10;
  - `activity_buracao`: 10/10;
  - complete Matrices A and B.
- [ ] Adjudicate every public reply and verify:
  - zero copied-ID/duplicated-question repairs;
  - zero malformed JSON;
  - zero unsupported material claims;
  - exact Maya-authored chunks preserved;
  - zero deterministic fallback;
  - zero real business providers;
  - zero external effects.
- [ ] Preserve every failure. A later pass does not erase an earlier failing repetition.
- [ ] Write authenticated `AUDIT.md`, `AUDIT.json`, and SHA-256 manifest outside the repository.
- [ ] If all acceptance criteria pass, report conversational `GO` for local candidate only. Otherwise report `NO-GO` with causal ownership.
- [ ] Do not deploy, push, restart, open canary, or promote.
