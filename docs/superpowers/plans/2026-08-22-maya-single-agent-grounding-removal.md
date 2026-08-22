# Maya Single-Agent Grounding Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every active second-model grounding-review path so Maya alone interprets provider observations and authors replies while the controller retains deterministic contract and effect validation.

**Architecture:** Delete the `grounding-v1` model contract from the adapter and child wrapper, remove its schema and Bókun trigger field, and retain evidence semantics in Maya's single active system prompt. Preserve V8 structured output, typed observations, protocol repair, choice binding, receipts, idempotency, and effect gates.

**Tech Stack:** Python 3.12, pytest, Ruff 0.15.10, SQLite copied-state canary, Docker/OCI, Hermes Agent 0.19.0 with GPT-5.6 Luna.

## Global Constraints

- Maya is the only semantic agent; no replacement reviewer, hidden model call, or lexical public-text validator.
- The parent validates only closed schemas, bindings, confirmation, idempotency, persistence, commands, receipts, and effects.
- Historical specs and plans remain unchanged.
- Provider writes, payments, handoff, e-mail, and outbound delivery stay closed during copied-state validation.
- Specific price, availability, links, reservation, payment, and operational claims remain bound to typed observations or receipts.
- Use `PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest` and Ruff `0.15.10`.
- Build and promote only from a clean committed tree with OCI revision equal to the commit SHA.

---

### Task 1: Prove the active runtime invokes only Maya

**Files:**
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_hermes_child.py`
- Modify: `tests/test_v2_structured_output.py`

**Interfaces:**
- Consumes: `HermesModelAdapter.complete_audited(request)`, `run_structured()`, and `maya_v8_request_overrides()`.
- Produces: regression tests requiring one model persona and rejecting `grounding-v1`.

- [ ] **Step 1: Replace reviewer-positive tests with a failing single-agent adapter test**

Add a test that creates a request containing an activity observation with `grounding_review_required=True`, supplies exactly one Maya V8 child result, calls `complete_audited()`, and asserts the exact Maya text is returned and the subprocess command list never contains `--contract`.

```python
def test_complete_audited_never_launches_a_second_semantic_model() -> None:
    seen: list[tuple[str, ...]] = []
    response = _v8_bytes(reply_chunks=["Resposta da Maya baseada na observação atual."])

    def run(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + response,
            stderr=b"",
        )

    turn = HermesModelAdapter(..., run=run, ...).complete_audited(
        _request_with_activity_observation()
    )
    assert turn.proposal.reply_chunks == (
        "Resposta da Maya baseada na observação atual.",
    )
    assert len(seen) == 1
    assert all("--contract" not in command for command in seen)
```

- [ ] **Step 2: Replace child reviewer tests with a failing contract rejection test**

```python
def test_structured_child_rejects_grounding_contract() -> None:
    with pytest.raises(ValueError, match="response contract"):
        asyncio.run(
            run_structured(
                ("hermes", "--profile", "leads", "-m", "gpt-5.6-luna",
                 "--provider", "openai-codex", "--contract", "grounding-v1"),
                CLOSED_REQUEST,
                agent_factory=FailIfCalled,
                profile_resolver=lambda _: "/tmp/hermes",
                session_db_factory=FakeSessionDB,
            )
        )
```

- [ ] **Step 3: Replace grounding schema tests with a failing single-export test**

```python
def test_structured_output_exposes_only_maya_v8_contract() -> None:
    import v2_host.structured_output as module
    assert not hasattr(module, "GROUNDING_REVIEW_JSON_SCHEMA")
    assert not hasattr(module, "grounding_review_request_overrides")
    assert module.__all__ == ["maya_v8_request_overrides"]
```

- [ ] **Step 4: Run the three focused tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_v2_hermes_model_adapter.py::test_complete_audited_never_launches_a_second_semantic_model \
  tests/test_v2_hermes_child.py::test_structured_child_rejects_grounding_contract \
  tests/test_v2_structured_output.py::test_structured_output_exposes_only_maya_v8_contract
```

Expected: all fail because the active reviewer, child contract, and schema still exist.

---

### Task 2: Remove reviewer runtime, child contract, schema, and payload trigger

**Files:**
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_host/hermes_child.py`
- Modify: `v2_host/structured_output.py`
- Modify: `scripts/phase8_hermes_child.py`
- Modify: `v2_adapters/bokun.py`
- Modify: `v2_contracts/model.py`
- Modify: `tests/test_v2_bokun_party_reads.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`
- Modify: reviewer-only sections in `tests/test_v2_hermes_model_adapter.py`, `tests/test_v2_hermes_child.py`, and `tests/test_v2_structured_output.py`

**Interfaces:**
- Consumes: standard Maya V8 `_complete_audited()` path and typed `ReadObservation` payloads.
- Produces: `complete_audited()` returning Maya's audited turn directly; child and structured-output modules supporting Maya V8 only.

- [ ] **Step 1: Delete reviewer constants and helpers from the adapter**

Remove `_GROUNDING_REVIEW_SYSTEM_PROMPT`, `_grounding_review_required`, `_grounding_review_wire`, `_grounding_decision`, `_grounding_review`, and `_apply_grounding_review`. Change:

```python
def complete_audited(self, request: ModelRequest) -> AuditedModelTurn:
    return self._complete_audited(request, allow_protocol_repair=True)
```

Remove now-unused `PublicReplyCorrectionReason` imports only if no remaining active adapter path consumes them.

- [ ] **Step 2: Restrict the child wrapper to Maya V8**

Delete grounding imports and conditional overrides. Make `_response_contract()` accept no explicit contract or only `maya-v8`; `grounding-v1` must raise `ValueError`. In `run_structured()`, always use:

```python
request_overrides = maya_v8_request_overrides()
```

Apply the same removal to `scripts/phase8_hermes_child.py`.

- [ ] **Step 3: Delete the grounding structured-output schema**

Keep only `maya_v8_request_overrides()` and:

```python
__all__ = ["maya_v8_request_overrides"]
```

- [ ] **Step 4: Remove the Bókun trigger and obsolete correction reason**

Delete `"grounding_review_required": True` from activity-description public payloads and update the exact payload assertion. Delete `UNSUPPORTED_OBSERVATION_CLAIM` from `PublicReplyCorrectionReason` and its grammar test only after searching the active source tree and confirming no remaining consumer.

- [ ] **Step 5: Delete reviewer-only tests and retain single-agent tests**

Remove helpers and tests that generate `supported|unsupported` reviewer decisions. Keep tests for exact Maya text, protocol repair, typed observations, and the new no-second-model contract.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run the Task 1 command plus:

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_profile_and_model_grammar.py
```

Expected: PASS.

- [ ] **Step 7: Commit the runtime removal**

```bash
git add v2_adapters/hermes_model.py v2_host/hermes_child.py \
  v2_host/structured_output.py scripts/phase8_hermes_child.py \
  v2_adapters/bokun.py v2_contracts/model.py \
  tests/test_v2_hermes_model_adapter.py tests/test_v2_hermes_child.py \
  tests/test_v2_structured_output.py tests/test_v2_bokun_party_reads.py \
  tests/test_v2_profile_and_model_grammar.py
git commit -m "refactor(v2): make Maya the sole semantic agent"
```

---

### Task 3: Consolidate evidence semantics in Maya's prompt

**Files:**
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `tests/test_v2_luna_prompt.py`

**Interfaces:**
- Consumes: sanitized provider `public_payload`, canonical payment policy, and existing receipt-bound effect context.
- Produces: one active prompt that gives Maya the former review-critical evidence semantics directly.

- [ ] **Step 1: Add failing prompt-contract assertions**

Add or update one test asserting the active prompt explicitly contains all of these semantic owners:

```python
def test_single_maya_prompt_owns_provider_evidence_semantics() -> None:
    assert "`available=true` confirma disponibilidade" in PROMPT
    assert "`available=false` confirma indisponibilidade" in PROMPT
    assert "metadados de grupo nunca substituem" in PROMPT
    assert "age_guidance=null ou suitability_guidance=null" in PROMPT
    assert "sinal de 20%" in PROMPT
    assert "só afirme execução" in PROMPT
```

- [ ] **Step 2: Run the focused test and verify RED**

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_v2_luna_prompt.py::test_single_maya_prompt_owns_provider_evidence_semantics
```

Expected: FAIL on missing exact single-agent evidence wording.

- [ ] **Step 3: Add minimal evidence rules to the existing relevant sections**

State literal activity availability semantics beside the Bókun price rule, clarify that group metadata never overrides it, retain null suitability semantics in recommendation, and add a short receipt rule in technical/effect limits:

```text
- `available=true` confirma disponibilidade ...; `available=false` confirma indisponibilidade ...
- Metadados de grupo nunca substituem a disponibilidade atual do provider.
- Só afirme execução, reserva, pagamento, handoff ou envio quando houver o recibo correspondente.
```

Do not add another persona, reviewer, or controller text interpretation.

- [ ] **Step 4: Run prompt tests and verify GREEN**

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q tests/test_v2_luna_prompt.py
```

Expected: PASS.

- [ ] **Step 5: Commit prompt consolidation**

```bash
git add config/v2_luna_system_prompt.txt tests/test_v2_luna_prompt.py
git commit -m "fix(v2): give Maya complete evidence semantics"
```

---

### Task 4: Remove active operational guidance for the retired reviewer

**Files:**
- Remove or rewrite outside Git repo through skill manager: `references/material-grounding-review-recovery.md`
- Verify unchanged: historical `docs/superpowers/specs/*` and `docs/superpowers/plans/*` predating this change

**Interfaces:**
- Consumes: approved policy that historical repository documents remain immutable.
- Produces: no current runbook instructing operators to tune or recover the retired reviewer.

- [ ] **Step 1: Search active source and operational guidance**

Search for:

```text
grounding-v1
GROUNDING_REVIEW
grounding_review_required
UNSUPPORTED_OBSERVATION_CLAIM
material-grounding reviewer
```

Classify repository matches under historical specs/plans separately from active code/tests. No active runtime/test match may remain.

- [ ] **Step 2: Retire the skill reference**

Remove `references/material-grounding-review-recovery.md` from the umbrella skill because its procedure is no longer valid. Do not edit historical repository specs/plans.

- [ ] **Step 3: Verify no current runbook recommends the reviewer**

Use `skill_view`/search and require zero active operational references.

---

### Task 5: Run regression and copied-state single-agent canary

**Files:**
- No production source changes expected
- Temporary copied stores under deployment runtime; remove after verification

**Interfaces:**
- Consumes: committed single-agent candidate and copied production SQLite stores.
- Produces: test evidence and a no-side-effect production-shaped event receipt.

- [ ] **Step 1: Run affected regression suites**

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_hermes_child.py \
  tests/test_v2_structured_output.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_luna_prompt.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_inbox_relay_workers.py \
  tests/test_v2_reads.py
```

Expected: PASS.

- [ ] **Step 2: Run full project verification**

```bash
PYTHONPATH="$PWD" uv run --extra runtime --extra dev pytest -q
uvx ruff@0.15.10 check .
python -m compileall -q reservation_boundary reservation_confirmation \
  reservation_domain reservation_execution reservation_followup \
  reservation_lookup v2_adapters v2_application v2_contracts v2_host v2_ops deploy
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: zero failures and `fasttrack-boundaries: OK`.

- [ ] **Step 3: Build an immutable candidate image**

Commit any final verified changes, then build with `VCS_REF=$(git rev-parse HEAD)`, push `127.0.0.1:5000/agente-v2:<short-sha>`, resolve its manifest digest, and verify the OCI revision label equals the commit SHA.

- [ ] **Step 4: Run copied-state event canary**

Copy production SQLite stores into an isolated writable directory. Reset only event `manychat-event:1e03eba2a2335080aeda48f5a15de267` to pending in the copies. Start only the inbox worker composition from the candidate image with outbound delivery and provider writes absent/closed. Instrument subprocess commands categorically and require:

```text
status=processed
turn_receipt_hash present
model_calls >= 1
all commands omit --contract grounding-v1
command_count=0
no reservation/payment/handoff/e-mail/public delivery
```

Inspect the committed public chunks and require literal consistency with the copied event's authenticated Bókun observation.

- [ ] **Step 5: Remove copied-state artifacts**

Delete the isolated copied stores and confirm production databases were not modified by the canary.

---

### Task 6: Promote and verify production

**Files:**
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/v2-candidate-<short-sha>.env`
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/compose-<short-sha>.yaml`
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/runtime/ga-runtime-image-metadata-<short-sha>.json`

**Interfaces:**
- Consumes: verified immutable image digest and current GA compose configuration.
- Produces: API, worker, and router running the same single-agent revision/digest.

- [ ] **Step 1: Push source and verify remote SHA**

```bash
git push origin HEAD
git fetch origin
test "$(git rev-parse HEAD)" = "$(git rev-parse @{u})"
```

- [ ] **Step 2: Generate digest-bound deployment artifacts**

Clone the latest compose/env shape, replace candidate SHA, manifest digest, and metadata path, validate with `docker compose config -q`, and verify metadata labels/repo digest.

- [ ] **Step 3: Recreate API, worker, and router**

```bash
docker compose --env-file v2-candidate-<short-sha>.env \
  -f compose-<short-sha>.yaml up -d --force-recreate --remove-orphans
```

Wait for API and router health `healthy` and worker running.

- [ ] **Step 4: Verify runtime identity and health**

For each container require `Config.Image` equals the manifest-digest reference, OCI revision equals the commit SHA, restart count is `0`, and OOM is `false`. Require clean synchronized Git.

- [ ] **Step 5: Verify the deployed source cannot launch the reviewer**

Inside the worker image, import active modules and assert no grounding schema/helper exists; search `/app` active Python/config files for the retired symbols while excluding historical docs. Do not send another WhatsApp message automatically.

- [ ] **Step 6: Update task status and report evidence**

Report exact commit, digest, test counts, copied-state canary result, health, and zero external effects.
