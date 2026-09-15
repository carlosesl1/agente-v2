# GPT 5.6 Terra High Runtime Implementation Plan

> **Execution:** Inline in the current session. Subagents are prohibited by the operator.

**Goal:** Deploy the Maya V2 WhatsApp agent on `openai-codex/gpt-5.6-terra` with reasoning effort `high` and verify it end to end.

**Architecture:** Keep `openai-codex` and all commercial/controller contracts unchanged. Extend the existing closed child command with one required `--reasoning-effort high` option and pass its validated value to `AIAgent.reasoning_config`; bind the change into a new Git SHA, OCI digest and runtime authority manifest.

**Tech stack:** Python 3.12, pytest, Hermes Agent 0.19.0, Docker/Compose, SQLite, ManyChat and isolated WAHA test client.

## Global constraints

- V2 only; V3, legacy and Maya Ops are not mutation targets.
- No reservation, payment, provider POST, handoff or unrelated commercial effect in qualification.
- Every message sent to Maya starts with `>>>`.
- No blind resend after an ambiguous channel result.
- Preserve exact rollback artifacts and state mounts.

### Task 1: Causal model/reasoning contract

**Files:**
- Modify: `tests/test_v2_hermes_child.py`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_phase8_ops_artifacts.py`

- [ ] Change expected model values to `gpt-5.6-terra`.
- [ ] Require exactly one `--reasoning-effort high` in the structured child command.
- [ ] Assert the child passes `{"enabled": True, "effort": "high"}` to `AIAgent`.
- [ ] Run the three focused tests and preserve the expected RED caused by the old Luna contract/missing reasoning argument.

### Task 2: Minimal implementation

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/hermes_child.py`
- Modify: `compose.v2.yaml`
- Modify: `Dockerfile.v2`
- Create: `config/v2_terra_system_prompt.txt`
- Remove: `config/v2_luna_system_prompt.txt`
- Update directly affected test fixtures containing the controlled model identity.

- [ ] Change the controlled model to `openai-codex/gpt-5.6-terra`.
- [ ] Parse a required reasoning effort and accept only `high` for the productive contract.
- [ ] Pass the exact high reasoning configuration to `AIAgent`.
- [ ] Rename the versioned prompt without changing its bytes.
- [ ] Run focused and directly affected suites GREEN.
- [ ] Run Ruff, compileall, diff check and the canonical test gate.
- [ ] Commit the immutable successor.

### Task 3: Image and isolated rollout

**Files:**
- Create operational evidence under `/home/ubuntu/workspace/v2-terra-high-727d3625/`.
- Create a release-specific Compose/env bundle under `/home/ubuntu/workspace/agente-v2-canary-deploy/`.

- [ ] Build the exact commit with OCI revision label.
- [ ] Execute image-level tests and a direct tool-free Terra/high smoke.
- [ ] Push to the local registry and record the immutable digest/image ID.
- [ ] Back up state and preserve the predecessor rollback pointer.
- [ ] Roll out only the isolated test contact and verify `/readyz`, queues, mounts and exact model/reasoning evidence.

### Task 4: WhatsApp smoke and GA promotion

- [ ] Snapshot conversational and commercial-effect counters.
- [ ] Send one neutral `>>>` message with a unique operation ID through the authorized isolated WAHA client.
- [ ] Reconcile an ambiguous send without retry.
- [ ] Require one processed turn, Terra provider/model, high reasoning, delivered response and WhatsApp read-back.
- [ ] Require zero reservation/payment/provider-write deltas and restore `send_enabled=false`.
- [ ] Promote the same digest to GA, update authority atomically and verify all six V2 containers.
- [ ] Run final READ → VERIFY and save a sanitized final receipt.
