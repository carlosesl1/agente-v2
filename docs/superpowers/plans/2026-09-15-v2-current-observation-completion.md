# Maya V2 Current-Observation Completion Implementation Plan

> **Execution:** Inline in the current session. Subagents are prohibited by the operator.

**Goal:** Make Maya V2 reliably answer current positive provider observations with authenticated availability and exact total/currency, without controller text parsing, rewriting, or a secondary semantic reviewer.

**Architecture:** Add one dynamic high-salience post-observation suffix in `v2_adapters/hermes_model.py`, emitted only when `ModelRequest.observations` is non-empty. Keep the existing two-frame read flow, V8 contract, byte-exact Maya prose ownership, provider adapters, controller authority and effect gates unchanged.

**Tech stack:** Python 3.12, pytest, Hermes Agent/OpenAI Codex Terra high, Docker/Compose, SQLite, ManyChat and isolated WAHA.

## Global constraints

- V2 only; V3, legacy and Maya Ops are not mutation targets.
- No regex, keyword, substring or alias routing/validation.
- No controller inspection or rewrite of Maya public text.
- No secondary semantic model or reviewer.
- No reservation, payment, provider POST or handoff in this correction/retest.
- Every WhatsApp test message starts with `>>>` and uses a fresh operation ID once.
- Preserve exact current runtime and state as rollback.

---

### Task 1: Conditional post-observation authority

**Files:**
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `v2_adapters/hermes_model.py`

**Interfaces:**
- Consumes: `ModelRequest.observations` and existing `_request_wire(request, system_prompt, now=...) -> bytes`.
- Produces: `_CURRENT_OBSERVATION_COMPLETION_SUFFIX: str`, conditionally appended to the system prompt.

- [ ] Add a failing test that builds a lodging `ReadObservation` with `total_amount="600.00"` and `currency="BRL"`, serializes `_request_wire()`, and requires both the unchanged public values and the post-observation completion suffix.
- [ ] Add a failing companion assertion that a request without observations does not receive the suffix.
- [ ] Run the exact tests under `env -i`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and explicit `HERMES_LEADS_AGENT_CONFIG_PATH`; require failure only because the suffix does not exist.
- [ ] Define the concise suffix next to the existing turn-completion authority.
- [ ] Append it exactly once in `_request_wire()` only when `request.observations` is non-empty.
- [ ] Rerun the exact tests GREEN.
- [ ] Commit the causal implementation and evidence update.

### Task 2: Static and canonical qualification

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create operational evidence under `/home/ubuntu/workspace/v2-observation-completeness-727d3625/`.

**Interfaces:**
- Consumes: final Task 1 commit.
- Produces: exact test/static receipts bound to its SHA/tree.

- [ ] Run `tests/test_v2_hermes_model_adapter.py` and `tests/test_v2_turn_executor.py`.
- [ ] Run all directly affected model, contract and prompt suites.
- [ ] Run pinned Ruff 0.15.10, compileall, diff check and fast-track boundary guard.
- [ ] Run the canonical clean-environment pytest gate.
- [ ] Record counts, commands and exit codes in a sanitized evidence receipt.
- [ ] Commit evidence-only documentation if needed and freeze the final candidate SHA/tree.

### Task 3: Immutable image and model-level causal smoke

**Files:**
- Create: release-specific build/deploy evidence under `/home/ubuntu/workspace/v2-observation-completeness-727d3625/`.

**Interfaces:**
- Consumes: exact final candidate SHA/tree.
- Produces: immutable registry reference, image ID, OCI revision and real-model smoke receipt.

- [ ] Build a new OCI image with the exact Git revision label; do not relabel the predecessor.
- [ ] Inspect revision, image ID, prompt source and Terra/high runtime settings.
- [ ] Run image-level tests.
- [ ] Submit one tool-free real-model request carrying the causal `600.00 BRL` observation; require a grounded final response and one semantic model invocation for that request.
- [ ] Push the immutable tag to the local registry and record digest/image ID.

### Task 4: Isolated runtime and WhatsApp causal retest

**Files:**
- Create: private and sanitized receipts under `/home/ubuntu/workspace/v2-observation-completeness-727d3625/`.
- Modify only release-specific isolated deployment artifacts required by the canonical rollout mechanism.

**Interfaces:**
- Consumes: exact Task 3 image.
- Produces: isolated runtime health/model identity and WhatsApp read/delivery/effect reconciliation.

- [ ] Capture pre-rollout runtime identity, state/database hashes, effect counters, WAHA state and rollback pointer.
- [ ] Promote only the authorized isolated contact to the candidate image.
- [ ] Verify `/readyz`, model `openai-codex/gpt-5.6-terra`, reasoning `high`, mounts, restart/OOM state and `send_enabled=false` baseline.
- [ ] Send one fresh `>>>` lodging date-change query with a unique operation ID and no retry.
- [ ] Reconcile provider observation, all outbox chunks, channel receipt and WhatsApp read-back.
- [ ] Require the authenticated total in the answer, one processed turn, no duplicate, and zero reservation/payment/handoff/provider-write deltas.
- [ ] Restore `send_enabled=false` and verify authority.

### Task 5: GA promotion and final verification

**Files:**
- Modify only canonical authority/deploy artifacts through the established rollout program.
- Create final sanitized result and closure report.

**Interfaces:**
- Consumes: exact isolated candidate identity and green causal evidence.
- Produces: aligned GA/test runtime authority with preserved predecessor rollback.

- [ ] Back up GA config/state metadata and validate rollback before mutation.
- [ ] Promote the same immutable digest to GA atomically.
- [ ] Verify all GA/test containers, readiness, exact SHA/tree/image/model/reasoning, restart/OOM state and unchanged mounts.
- [ ] Execute the canonical READ → VERIFY sequence and require `runtime authority: OK`.
- [ ] Save sanitized JSON/Markdown receipts and hashes; report remaining limitations without overclaiming provider writes.
