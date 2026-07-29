# Maya V2 Extended Real-Model Shadow Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute an isolated twelve-scenario real-model shadow matrix that tests stale authority, replay, concurrency, provider drift/failure, profile gaps, safety constraints, noisy language and multi-service continuity without any external effect.

**Architecture:** Keep the immutable production candidate inside its pinned OCI image. Mount a new lab runner that imports the already-qualified V2 composition, replaces only parent-owned provider/profile/clock ports with deterministic scenario scripts, and records one private SQLite journal per scenario. The harness owns event replay, batched events and interleaved leads; the candidate runtime continues to own model interpretation, proposal state, authorization, commands and relays.

**Tech Stack:** Python 3.12, `unittest`, SQLite, Docker, Hermes real-model child, Maya V2 contracts/application/host composition, JSONL evidence and SHA-256 manifests.

## Global Constraints

- Candidate start revision: `3490fcf4f49506fec5c3b0602624c76627ff25a4`.
- Candidate start image: `sha256:4d9d30fc49da8b9d087a15d992a911e200249f456473481037e5ad5c62defca1`.
- New lab: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729`.
- Do not modify, add or delete `uv.lock`.
- Never construct or start provider-write, payment, handoff-delivery or ManyChat-delivery workers.
- Network-capable provider credentials remain fake and every provider base URL remains under `invalid.invalid`.
- The Hermes OAuth mount is the only live integration and is used only for real model inference.
- Archive each failed attempt before any retry; never overwrite prior evidence.
- No provider write, payment link, channel delivery, deploy, push or rollout is authorized.

---

### Task 1: Pure harness contracts and clock/read scripts

**Files:**
- Create: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/extended_harness.py`
- Create: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/test_extended_harness.py`

**Interfaces:**
- Produces: `ScenarioClock.now() -> datetime`, `ScenarioClock.advance(seconds: int) -> None`, `ReadScript.next(kind: ReadKind) -> ReadOutcome`, `Step`, `Scenario`, `public_leak(text: str) -> bool`.
- Consumes: `v2_contracts.providers.ReadKind` from the pinned candidate image.

- [ ] **Step 1: Write failing unit tests for time, scripted outcomes and leak detection**

```python
from datetime import datetime, timezone
import unittest
from extended_harness import ReadScript, ScenarioClock, public_leak
from v2_contracts.providers import ReadKind

class HarnessContractsTest(unittest.TestCase):
    def test_clock_advances_exact_seconds(self):
        clock = ScenarioClock(datetime(2026, 7, 29, 12, tzinfo=timezone.utc))
        clock.advance(1801)
        self.assertEqual(clock.now().isoformat(), "2026-07-29T12:30:01+00:00")

    def test_read_script_consumes_kind_specific_outcomes(self):
        script = ReadScript({ReadKind.ACTIVITY: ("available", "price_changed")})
        self.assertEqual(script.next(ReadKind.ACTIVITY), "available")
        self.assertEqual(script.next(ReadKind.ACTIVITY), "price_changed")
        self.assertEqual(script.next(ReadKind.ACTIVITY), "available")

    def test_public_leak_blocks_internal_and_provider_vocabulary(self):
        for text in ("offer:abc", "product:tour-4ps", "Bókun", "Stripe", "system prompt"):
            self.assertTrue(public_leak(text), text)
        self.assertFalse(public_leak("Vou verificar a disponibilidade para você."))
```

- [ ] **Step 2: Run the tests inside the candidate image and verify RED**

Run:

```bash
docker run --rm --entrypoint python \
  -v /home/ubuntu/workspace/maya-v2-extended-shadow-20260729:/lab \
  -w /lab \
  sha256:4d9d30fc49da8b9d087a15d992a911e200249f456473481037e5ad5c62defca1 \
  -m unittest -v test_extended_harness.py
```

Expected: import failure for `extended_harness` or missing contract names.

- [ ] **Step 3: Implement the minimal pure contracts**

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from v2_contracts.providers import ReadKind

class ScenarioClock:
    def __init__(self, value: datetime) -> None:
        self._value = value
    def now(self) -> datetime:
        return self._value
    def advance(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 0:
            raise ValueError("seconds must be a non-negative exact integer")
        self._value += timedelta(seconds=seconds)

class ReadScript:
    def __init__(self, outcomes: dict[ReadKind, tuple[str, ...]]) -> None:
        self._outcomes = outcomes
        self._index = {kind: 0 for kind in outcomes}
    def next(self, kind: ReadKind) -> str:
        values = self._outcomes.get(kind, ())
        index = self._index.get(kind, 0)
        if index >= len(values):
            return "available"
        self._index[kind] = index + 1
        return values[index]

@dataclass(frozen=True, slots=True)
class Step:
    messages: tuple[str, ...]
    event_key: str
    advance_seconds: int = 0
    expect_replayed: bool = False
    expect_error: bool = False

@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    subscribers: tuple[str, ...]
    steps: tuple[Step, ...]
    expected_commands: int
    expected_relays: int
    expected_handoffs: int

_LEAKS = tuple(re.compile(value, re.I) for value in (
    r"offer:[0-9a-z]", r"product:tour", r"request_id", r"source_event_id",
    r"private_binding", r"capability_policy", r"system prompt", r"\bbókun\b", r"\bstripe\b",
))

def public_leak(text: str) -> bool:
    return any(pattern.search(text) for pattern in _LEAKS)
```

- [ ] **Step 4: Run the unit tests and verify GREEN**

Run the same Docker `unittest` command. Expected: all three tests pass.

---

### Task 2: Isolated runner and side-effect invariants

**Files:**
- Create: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_extended_shadow.py`
- Create: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh`
- Modify: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/test_extended_harness.py`

**Interfaces:**
- Consumes: `Scenario`, `Step`, `ScenarioClock`, `ReadScript` from Task 1; `base_real5.py`; `V2Container`, `V2TurnExecutor`, `InboundBatch` and typed read/profile contracts from the candidate.
- Produces: `run(name: str) -> None`, `build_batch(...) -> InboundBatch`, `ControlledReadPort.read(request) -> ReadObservation`, `SequenceProfile.read(...) -> PrivateCustomerBinding`, and per-scenario `summary.json`.

- [ ] **Step 1: Add RED tests for exact event replay and multi-event batch assembly**

```python
from run_extended_shadow import build_batch

class BatchAssemblyTest(unittest.TestCase):
    def test_batch_preserves_two_events_and_combined_order(self):
        batch = build_batch(
            subscriber="992900012",
            event_key="conflict",
            messages=("Sim", "Não, mude para 19/11/2026"),
            received_at=ScenarioClock.START,
        )
        self.assertEqual(len(batch.events), 2)
        self.assertEqual(batch.combined_text, "Sim\nNão, mude para 19/11/2026")
        self.assertEqual(batch.batch_id, "batch:maya-extended:992900012:conflict")
```

- [ ] **Step 2: Run this selector and verify it fails because the runner API is absent**

Run the Docker `unittest` command from Task 1. Expected: import/name failure for `build_batch`.

- [ ] **Step 3: Implement runner composition**

The runner must:

```python
STATE = Path(os.environ["REAL5_STATE_DIR"])
TRANSCRIPT = STATE / "transcript.jsonl"
READS = STATE / "controlled-read-observations.jsonl"
FAILURES = STATE / "contained-failures.jsonl"
SUMMARY = STATE / "summary.json"

# Before inference:
assert settings.candidate_git_sha == EXPECTED_GIT_SHA
assert settings.candidate_image_digest == EXPECTED_IMAGE
assert settings.manychat_delivery_enabled is False
assert settings.manychat_handoff_enabled is False

# After each accepted turn:
current = base.counts(container)
assert current["commands"] == current["relays"]
assert not public_leak(reply)
assert not base.CAPTURES.exists()
```

`ControlledReadPort` returns deterministic public DTOs for `ACTIVITY`, `ACTIVITY_DESCRIPTION`, `KNOWLEDGE`, `LODGING` and `ROOM_DESCRIPTION`. `unavailable` sets `available=False`; `price_changed` changes BRL 334.95 to BRL 349.95 with a new authenticated binding; `raise_once` raises one categorical `RuntimeError` before returning the default observation on the next identical request.

`run_container.sh` must pin the exact image and revision, use `--read-only`, `--cap-drop ALL`, `no-new-privileges`, fake provider keys, `invalid.invalid` provider/channel URLs, a read/write state mount only for the scenario and the real Hermes home only for child-model OAuth.

- [ ] **Step 4: Run harness tests and shell syntax validation**

```bash
docker run --rm --entrypoint python \
  -v /home/ubuntu/workspace/maya-v2-extended-shadow-20260729:/lab \
  -v /home/ubuntu/workspace/maya-v2-real5-20260728/run_real5.py:/lab/base_real5.py:ro \
  -w /lab \
  sha256:4d9d30fc49da8b9d087a15d992a911e200249f456473481037e5ad5c62defca1 \
  -m unittest -v test_extended_harness.py
bash -n /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh
python -m py_compile /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/*.py
```

Expected: all unit tests pass and syntax commands exit 0.

---

### Task 3: Twelve closed scenario definitions

**Files:**
- Create: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/scenarios.py`
- Modify: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/test_extended_harness.py`

**Interfaces:**
- Produces: `SCENARIOS: dict[str, Scenario]` with keys `10-expired-after-pause` through `21-hostel-blockage-to-tour-continuity`.
- Consumes: `Scenario` and `Step` from Task 1.

- [ ] **Step 1: Add a RED matrix-contract test**

```python
from scenarios import SCENARIOS

class MatrixContractTest(unittest.TestCase):
    def test_matrix_has_exact_nonredundant_scenarios(self):
        self.assertEqual(tuple(SCENARIOS), (
            "10-expired-after-pause",
            "11-duplicate-event-replay",
            "12-conflicting-debounce-batch",
            "13-stale-confirmation-after-date-correction",
            "14-price-change-on-reread",
            "15-unavailable-on-reread",
            "16-transient-read-failure",
            "17-incomplete-private-profile",
            "18-late-safety-constraint",
            "19-mixed-language-and-noisy-transcript",
            "20-interleaved-lead-isolation",
            "21-hostel-blockage-to-tour-continuity",
        ))
        self.assertTrue(all(5 <= len(item.steps) <= 15 for item in SCENARIOS.values()))
```

- [ ] **Step 2: Run the selector and verify RED**

Expected: `scenarios` import failure.

- [ ] **Step 3: Define natural ManyChat-style turns and exact final expectations**

Each scenario must use synthetic contact data and include an explicit expected command/relay/handoff count. Zero-effect cases expect `0/0`; replay and valid post-resummary confirmations may expect `1/1`; two-lead isolation may expect `2/2` only if each lead independently receives and confirms its own authenticated summary. `11-duplicate-event-replay` reuses the same `event_key` for the confirmation replay. `12-conflicting-debounce-batch` contains two messages in one `Step`. `10-expired-after-pause` advances `1801` seconds before confirmation. `16-transient-read-failure` marks exactly one step `expect_error=True` and immediately repeats the same message/event content with a new retry key.

- [ ] **Step 4: Run matrix-contract and complete harness tests**

Expected: all tests pass; every scenario has 5–15 steps and unique synthetic subscriber scopes.

---

### Task 4: Execute zero-command and containment scenarios

**Files:**
- Generated: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/state/$SCENARIO/`
- Generated: `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/attempts/$ATTEMPT/`

**Interfaces:**
- Consumes: `run_container.sh "$SCENARIO"`.
- Produces: transcript, model debug frames, controlled read records, contained failures and scenario summary.

- [ ] **Step 1: Run expiry, conflicting batch and stale-confirmation cases**

```bash
for scenario in \
  10-expired-after-pause \
  12-conflicting-debounce-batch \
  13-stale-confirmation-after-date-correction; do
  /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh "$scenario"
done
```

Expected: zero command for stale/old authority; any later fresh confirmation may create only the explicitly expected command.

- [ ] **Step 2: Run unavailable, transient failure, incomplete profile and late safety cases**

```bash
for scenario in \
  15-unavailable-on-reread \
  16-transient-read-failure \
  17-incomplete-private-profile \
  18-late-safety-constraint; do
  /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh "$scenario"
done
```

Expected: no external effects; transient recovery is labeled `pass_with_warning` and retains the first categorical failure.

- [ ] **Step 3: Inspect runtime-authoritative SQLite/outbox counts**

Use Python `sqlite3` against each scenario DB and write `effect-audit.json` containing counts for commands, relays, handoffs, provider captures, payment links, delivery rows and effect workers. Expected external counters: all zero.

---

### Task 5: Execute replay, drift, language, isolation and multi-service scenarios

**Files:**
- Generated: the remaining scenario state/evidence directories.

**Interfaces:**
- Consumes: the same immutable image and runner.
- Produces: five additional final summaries plus an interleaved lead-isolation audit.

- [ ] **Step 1: Run replay and price-drift cases**

```bash
for scenario in 11-duplicate-event-replay 14-price-change-on-reread; do
  /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh "$scenario"
done
```

Expected: replay reuses one receipt with no second command/read; price drift invalidates old consent and requires a new summary.

- [ ] **Step 2: Run language, isolation and multi-service cases**

```bash
for scenario in \
  19-mixed-language-and-noisy-transcript \
  20-interleaved-lead-isolation \
  21-hostel-blockage-to-tour-continuity; do
  /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh "$scenario"
done
```

Expected: no “ok” authorization outside a current proposal, no cross-lead scope contamination, and no lodging route stickiness that blocks a later tour read.

- [ ] **Step 3: If a runtime defect appears, follow focused RED/GREEN**

Set `ATTEMPT="$(git rev-parse --short=7 HEAD)-${SCENARIO}-${CAUSE}"` and preserve the scenario directory under `attempts/$ATTEMPT/`. Add one regression to the owning existing test file (`tests/test_v2_turn_executor.py`, `tests/test_v2_conversation_reducer.py` or `tests/test_v2_critical_actions.py`), run it to observe the exact failure, apply the smallest runtime change, run the focused selector to green, commit, build one new image, update the immutable runner pin and rerun only affected scenarios. Do not change prompt or runtime on model style preference alone.

---

### Task 6: Clean full matrix, regression gates and evidence report

**Files:**
- Create: `$FINAL_DIR/matrix-summary.json`, where `FINAL_DIR="/home/ubuntu/workspace/maya-v2-extended-shadow-20260729/evidence/final-$(git rev-parse --short=7 HEAD)"`
- Create: `$FINAL_DIR/validation.md`
- Create: `$FINAL_DIR/sanitized-excerpts.md`
- Create: `$FINAL_DIR/SHA256SUMS`

**Interfaces:**
- Consumes: twelve final state directories tied to one image digest.
- Produces: machine-readable aggregate, human report, excerpts and checksum manifest.

- [ ] **Step 1: Run one clean full matrix after the candidate is frozen**

Archive all preliminary state first, create empty per-scenario directories and run all twelve scenarios against one image digest. Expected: all mechanically accepted or explicitly `pass_with_warning`; no mixed candidate SHAs/digests.

- [ ] **Step 2: Run fresh code gates**

```bash
env -i HOME=/home/ubuntu \
  PATH=/home/ubuntu/chapada-leads-hermes/venv/bin:/usr/local/bin:/usr/bin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q
python -m compileall -q reservation_boundary reservation_confirmation reservation_domain reservation_execution reservation_followup reservation_lookup v2_adapters v2_application v2_contracts v2_host
git diff --check
git status --short
FINAL_IMAGE=$(python -c 'import re,sys; text=open(sys.argv[1], encoding="utf-8").read(); print(re.search(r"^IMAGE=(sha256:[0-9a-f]{64})$", text, re.M).group(1))' /home/ubuntu/workspace/maya-v2-extended-shadow-20260729/run_container.sh)
docker image inspect "$FINAL_IMAGE" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}} {{.Architecture}}/{{.Os}}'
```

Expected: pytest exits 0 with only explicitly named historical deselections, compile/diff checks exit 0, `uv.lock` remains untracked and unchanged, and image label equals final Git SHA.

- [ ] **Step 3: Aggregate and checksum evidence**

The aggregate must report turn count, read attempts/commits, commands, relays, internal handoffs, external deliveries, provider captures, payment links, worker starts, replays, contained errors, visible fallbacks, leaks, duplicate replies and per-scenario verdict. Run `sha256sum` over every final summary/transcript and the three report files, then verify with `sha256sum -c SHA256SUMS`.

- [ ] **Step 4: Present a two-layer conclusion**

Report operational/effect safety separately from conversational quality. Include representative sanitized lead/Maya excerpts and clearly state that the matrix does not authorize deployment, public V2, ManyChat sends, provider writes or rollout.
