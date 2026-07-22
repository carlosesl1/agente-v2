# Phase 8 Sandbox Cloudbeds Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Carlos proibiu subagentes neste caminho; execute inline.

**Goal:** Permitir que o sandbox Maya consulte preço e disponibilidade reais de hospedagem via Cloudbeds, mantendo todos os efeitos externos mecanicamente bloqueados.

**Architecture:** O modelo continua sem ferramentas e emite no máximo um `LodgingAvailabilityReadRequest` fechado. O pai valida, chama um child efêmero allowlisted dentro do container Chapada via `docker exec` sem shell, reenvia a observação sanitizada ao modelo e grava somente o turno final e o hash da observação em SQLite.

**Tech Stack:** Python 3.12, `unittest`, SQLite STRICT, Docker CLI, runtime Cloudbeds v2 já instalado em `/app/.venv`.

## Global Constraints

- Executar inline, sem subagentes.
- Somente `cloudbeds_consultar_hospedagem_v2` pode ser chamado.
- `HERMES_LEADS_MODE=shadow`, `HERMES_LEADS_DRY_RUN=false` e `HERMES_CLOUDBEDS_READONLY_ENABLED=true` no child.
- Live sends, Cloudbeds/Bókun writes, Stripe, Wise, outboxes, ManyChat, Supabase e Redis devem estar fechados ou removidos no child.
- Provider/model I/O ocorre fora de transação SQLite.
- Nenhum ID interno ou payload bruto atravessa o DTO sanitizado.
- Uma única regressão proporcional; sem suíte pesada, build, deploy ou rollout.

---

### Task 1: Contratos fechados de request e observation

**Files:**
- Modify: `reservation_boundary/sandbox.py`
- Modify: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- Produces: `LodgingAvailabilityReadRequest.from_mapping(value)`, `to_canonical_bytes()`.
- Produces: `LodgingAvailabilityObservation.from_canonical_bytes(payload)`, `to_canonical_bytes()`, `canonical_hash()`.
- Extends: `SandboxModelResponse.read_requests: tuple[LodgingAvailabilityReadRequest, ...]`.

- [ ] **Step 1: Write failing contract tests**

Add tests that build canonical model JSON with `read_requests` and assert:

```python
response = SandboxModelResponse.from_canonical_bytes(
    _response(reads=[{
        "kind": "lodging_availability",
        "arguments": {
            "check_in": "2026-08-10",
            "check_out": "2026-08-12",
            "adults": 2,
            "children": 0,
        },
    }])
)
self.assertEqual(response.read_requests[0].adults, 2)
```

Reject: extra keys, duplicate/multiple reads, boolean counts, malformed dates and `check_out <= check_in`. Add an observation fixture proving canonical round-trip, five-option cap and rejection of internal identifiers such as `room_type_id`.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -B -m unittest -v tests.test_phase8_fasttrack_sandbox
```

Expected: FAIL because `read_requests` and the contract classes do not exist.

- [ ] **Step 3: Implement minimal contracts**

In `sandbox.py`:

```python
@dataclass(frozen=True, slots=True)
class LodgingAvailabilityReadRequest:
    check_in: str
    check_out: str
    adults: int
    children: int

    @classmethod
    def from_mapping(cls, value: object) -> "LodgingAvailabilityReadRequest": ...
    def to_canonical_bytes(self) -> bytes: ...

@dataclass(frozen=True, slots=True)
class LodgingAvailabilityObservation:
    status: str
    availability_confirmed: bool
    price_confirmed: bool
    options: tuple[dict[str, object], ...]
    public_summary: str
    raw_provider_payload_returned: bool

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "LodgingAvailabilityObservation": ...
    def to_canonical_bytes(self) -> bytes: ...
    def canonical_hash(self) -> str: ...
```

Extend the exact model response fields with mandatory `read_requests`; permit only zero or one request.

- [ ] **Step 4: Verify GREEN**

Run the same selector. Expected: all Task 1 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/sandbox.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat: define sandbox lodging read contracts"
```

### Task 2: Allowlisted Cloudbeds child and sanitized adapter

**Files:**
- Create: `scripts/phase8_cloudbeds_read_child.py`
- Modify: `reservation_boundary/sandbox.py`
- Modify: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- Produces: `SandboxReadPort.read(request: LodgingAvailabilityReadRequest) -> bytes`.
- Produces: `CloudbedsDockerRead(project_root, container="chapada-leads-hermes", timeout=30, run=_run_command)`.
- Child stdin: canonical `LodgingAvailabilityReadRequest` bytes.
- Child stdout: `PHASE8_CLOUDBEDS_RESULT\x00` followed by canonical observation bytes.

- [ ] **Step 1: Write failing adapter tests**

Use injected `fake_run` to assert exact no-shell command shape:

```python
adapter = CloudbedsDockerRead(
    project_root=Path("/workspace/project"),
    run=fake_run,
)
observation = adapter.read(request)
self.assertEqual(observation.status, "ok")
self.assertIn("/app/.venv/bin/python", calls[0][0])
self.assertIn("HERMES_CLOUDBEDS_WRITE_ENABLED=false", calls[0][0])
```

Assert all write/send/payment gates are false, external persistence credentials are blank, only Cloudbeds read credentials remain inherited, child source is passed to `python -c`, nonzero exit/marker absence/internal option keys fail closed.

- [ ] **Step 2: Verify RED**

Run the focused sandbox selector. Expected: FAIL because adapter/child do not exist.

- [ ] **Step 3: Implement child and adapter**

The child:

```python
raw = cloudbeds_consultar_hospedagem_v2(
    check_in=request.check_in,
    check_out=request.check_out,
    adults=request.adults,
    children=request.children,
)
```

Parse the result with duplicate-key rejection; map status to `ok|no_bookable_options|provider_error`; retain at most five options and only public fields; emit marker plus canonical observation. The parent loads the committed child source and calls:

```python
(
    "docker", "exec", "-i",
    "-e", "HERMES_LEADS_MODE=shadow",
    "-e", "HERMES_LEADS_DRY_RUN=false",
    "-e", "HERMES_LEADS_ALLOW_LIVE_SENDS=false",
    "-e", "HERMES_CLOUDBEDS_READONLY_ENABLED=true",
    # every write/payment/outbox gate false and unrelated credential blank
    container, "/app/.venv/bin/python", "-c", child_source,
)
```

Parse and authenticate the observation before return.

- [ ] **Step 4: Verify GREEN**

Run the focused selector. Expected: all Task 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/sandbox.py scripts/phase8_cloudbeds_read_child.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat: add allowlisted Cloudbeds sandbox reader"
```

### Task 3: Two-call conversation loop and atomic observation journal

**Files:**
- Modify: `reservation_boundary/sandbox.py`
- Modify: `scripts/run_phase8_sandbox.py`
- Modify: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- `SandboxConversation(..., reads: SandboxReadPort | None = None)`.
- `SQLiteSandboxStore.append_turn(..., observation: LodgingAvailabilityObservation | None)`.
- `SandboxTurnResult.read_observation: LodgingAvailabilityObservation | None`.

- [ ] **Step 1: Write failing orchestration tests**

Queue two model responses: first contains one lodging read; second contains none. Inject a fake read port and assert:

```python
result = runner.submit(session_id="lead-1", message="10 a 12 de agosto, 2 adultos")
self.assertEqual(len(model.calls), 2)
self.assertIn("READ_OBSERVATION=", model.calls[1][1][-1][1])
self.assertEqual(result.reply, "Encontrei uma opção...")
self.assertEqual(store.read_observation_count("lead-1"), 1)
```

Also assert: no read port fails without persistence; a second read request in the follow-up response fails without persistence; provider error observation can be answered conservatively; model/read calls happen before `BEGIN IMMEDIATE` by using a store probe that reports no active transaction.

- [ ] **Step 2: Verify RED**

Run the focused selector. Expected: FAIL because orchestration and observation table are absent.

- [ ] **Step 3: Implement orchestration and journal**

Add `sandbox_read_observations` with FK `(session_id, ordinal)` and exact hash. On first read request:

1. call `reads.read(request)` outside SQLite;
2. parse observation;
3. call model again with `history + user + assistant(first JSON) + user(READ_OBSERVATION=...)`;
4. require final response `read_requests == ()`;
5. atomically persist final public turn, observation and blocked effects.

Update system prompt to describe eight exact keys and the one-read protocol. Update CLI to instantiate `CloudbedsDockerRead(project_root=_PROJECT_ROOT)`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -B -m unittest -v tests.test_phase8_fasttrack_sandbox
```

Expected: all sandbox tests PASS.

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/sandbox.py scripts/run_phase8_sandbox.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat: ground sandbox lodging replies in Cloudbeds reads"
```

### Task 4: Real read-only conversation gate

**Files:**
- No production source changes expected.
- Private evidence only: `/home/ubuntu/.local/share/agente-v2-phase8-evidence/sandbox/cloudbeds-read-*`.

**Interfaces:**
- Uses: `scripts/run_phase8_sandbox.py`.

- [ ] **Step 1: Run proportional regression**

```bash
python3 -B -m unittest -v \
  tests.test_phase8_fasttrack_sandbox \
  tests.test_phase8_entry
git diff --check
python3 -B -m py_compile \
  reservation_boundary/sandbox.py \
  scripts/phase8_cloudbeds_read_child.py \
  scripts/phase8_hermes_child.py \
  scripts/run_phase8_sandbox.py
```

Expected: zero failures and zero syntax/diff errors.

- [ ] **Step 2: Run a new three-turn real-model conversation**

Use an isolated SQLite DB and a new session. Ask for lodging, provide `2026-08-10` to `2026-08-12` for two adults, then request reservation/cobrança. Expected:

- a real Cloudbeds observation is journaled, whether `ok`, `no_bookable_options` or `provider_error`;
- any shown availability/price exactly matches the observation;
- reservation/cobrança effects remain `sandbox_effects_disabled`;
- no ManyChat, reservation, payment or operational state write occurs.

- [ ] **Step 3: Freeze candidate**

```bash
git status --short -uall
git log -1 --format='%H %T'
```

Expected: clean worktree and immutable candidate identity. Save only sanitized evidence privately.
