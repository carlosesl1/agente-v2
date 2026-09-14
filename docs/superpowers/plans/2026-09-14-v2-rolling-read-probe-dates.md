# V2 Rolling Provider-Read Probe Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each task in order. Every functional change starts with a causal failing test.

**Goal:** Make Cloudbeds and Bókun health probes use deterministic future dates derived from the Bahia business day so static date expiry can never block Maya V2 ingress again.

**Architecture:** `v2_host.production` owns a pure rolling-window calculation and `ReconciliationStage` uses it once per fresh probe. The typed activity request carries an explicit availability-only bit through group enrichment into Bókun's GET-only path. Existing date environment fields remain compatible but become non-authoritative; product identity, cache interval, provider validation, and all effect boundaries remain unchanged.

**Tech Stack:** Python 3.12, stdlib `zoneinfo`, typed V2 contracts, pytest, Docker/OCI deployment.

## Global constraints

- Base commit: `0d790e7c8ce842a37abd5baab1035c1b65f774fd`.
- Branch: `fix/v2-service-reliability`.
- Worktree: `/home/ubuntu/agente-v2/.worktrees/service-reliability`.
- V2 only; no V3 or legacy checkout reads/writes.
- No provider POST, reservation, payment, ManyChat delivery, handoff, or active SQLite edit.
- Preserve old date settings only for configuration compatibility; runtime requests must ignore their values.
- Use `America/Bahia`, a 30-day lead, and a three-night lodging window.
- Run pytest with clean environment, no plugin autoload, and explicit `HERMES_LEADS_AGENT_CONFIG_PATH`.

---

### Task 1: Freeze task authority and design

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create: `docs/superpowers/specs/2026-09-14-v2-rolling-read-probe-dates-design.md`
- Create: `docs/superpowers/plans/2026-09-14-v2-rolling-read-probe-dates.md`

- [ ] Record Carlos's current-chat authorization, exact base SHA, branch/worktree, owner files, gates, and first `NEXT`.
- [ ] Run `git diff --check` and verify only governance documents changed.
- [ ] Commit with `docs(v2): authorize rolling read-probe dates`.

### Task 2: Add causal calendar regressions

**Files:**
- Modify: `tests/test_v2_production_composition.py`

**Interfaces:**
- Consumes: existing `ReconciliationStage.run_once(now=...)` and `_ProbeReads` recording fake.
- Produces: explicit required `ReadRequest` dates and acceptance timestamps.

- [ ] Extend `_ProbeReads` to record `accept(..., now=...)` timestamps without changing its existing behavior.
- [ ] Add a parametrized regression using stale legacy dates and these exact UTC clocks:

```python
@pytest.mark.parametrize(
    ("now", "expected_check_in"),
    (
        (datetime(2026, 12, 31, 2, 59, tzinfo=timezone.utc), date(2027, 1, 29)),
        (datetime(2026, 12, 31, 3, 0, tzinfo=timezone.utc), date(2027, 1, 30)),
        (datetime(2028, 1, 31, 12, 0, tzinfo=timezone.utc), date(2028, 3, 1)),
    ),
)
def test_read_probe_uses_rolling_bahia_dates_not_legacy_static_dates(...):
    ...
```

- [ ] Assert the first request is lodging, its check-out is `check_in + timedelta(days=3)`, the second is activity on the same `check_in`, and both request IDs contain only the derived dates.
- [ ] Add a separate acceptance-clock witness requiring a fresh exact UTC sample after each provider read; an observation stamped during HTTP dispatch must never be compared with the earlier cycle-start time.
- [ ] Run the new tests with the canonical clean-environment command and preserve the outputs as RED. Expected failures: the base requests contain stale configured dates; the first candidate used the earlier cycle clock and caused a productive `ReadBindingMismatch("observation is from the future")`.

### Task 3: Implement the rolling window

**Files:**
- Modify: `v2_host/production.py`

**Interfaces:**
- Produces: `_read_probe_dates(now: datetime) -> tuple[date, date, date]`.
- Consumed by: `ReconciliationStage._probe_reads()`.

- [ ] Add `ZoneInfo` and closed module constants for `America/Bahia`, 30 lead days, and three lodging nights.
- [ ] Implement:

```python
def _read_probe_dates(*, now: datetime) -> tuple[date, date, date]:
    business_today = now.astimezone(_READ_PROBE_TIME_ZONE).date()
    check_in = business_today + timedelta(days=_READ_PROBE_LEAD_DAYS)
    check_out = check_in + timedelta(days=_READ_PROBE_STAY_NIGHTS)
    return check_in, check_out, check_in
```

- [ ] In `_probe_reads`, calculate the tuple once per fresh probe and use it in request IDs and typed fields.
- [ ] Preserve post-read `datetime.now(timezone.utc)` sampling for both `self._reads.accept()` calls. The cycle `now` owns only deterministic date selection.
- [ ] Run the new regression and existing degraded-cache test; expected GREEN.
- [ ] Run the complete directly affected files:

```sh
env -i HOME=/tmp PATH=/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
 PYTHONPATH=/home/ubuntu/agente-v2/.worktrees/service-reliability:/home/ubuntu/agente-v2/.worktrees/service-reliability/tests \
 HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/agente-v2/.worktrees/service-reliability/config/leads_agent.yaml \
 /home/ubuntu/agente-v2/.worktrees/production-ga/venv/bin/python -m pytest -q -p no:cacheprovider \
 tests/test_v2_production_composition.py tests/test_v2_settings.py tests/test_v2_role_scoped_settings.py
```

### Task 4: Freeze and qualify the exact code candidate

Before freezing, close the independent review's GET-only finding:

- [ ] Add `ReadRequest.availability_only` as an exact activity-only boolean. Omit the default `False` from canonical bytes; bind `True` explicitly.
- [ ] Make direct and group-enriched Bókun reads honor the flag before any selection path.
- [ ] Set the flag only on the reconciliation activity probe.
- [ ] Preserve causal RED evidence for the absent contract and matched-group/two-participant path.
- [ ] Require the existing transport witness to record GET/GET with quote checkout enabled.
- [ ] Run the affected production, group, Bókun transport, and read-contract suites.

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create outside Git: `/home/ubuntu/workspace/v2-rolling-probe-727d3625/`

- [ ] Run `git diff --check`, compile the changed Python files, run the fast-track boundary guard, and compare changed-file lint against the base.
- [ ] Run the canonical clean-environment suite with only the seven historical CI deselections.
- [ ] Commit the code/tests and record exact SHA/tree plus commands/results in evidence.
- [ ] Dispatch an independent read-only exact-SHA review focused on timezone boundaries, stale config influence, physical GET-only behavior, cache semantics, and clock consistency.
- [ ] Resolve any material finding with a fresh causal RED→GREEN, then rerun affected and canonical gates.

### Task 5: Build and qualify an immutable image

**Files:**
- Use: `Dockerfile.v2`
- Evidence outside Git: `/home/ubuntu/workspace/v2-rolling-probe-727d3625/qualification/`

- [ ] Build one image from the final committed tree with `org.opencontainers.image.revision` equal to the exact candidate SHA.
- [ ] Record image ID and immutable digest; inspect that the image contains the exact changed source and tests.
- [ ] Start a dark/read-only disposable runtime with all provider writes and channel delivery closed.
- [ ] Inject a lowest-boundary HTTP guard that rejects every non-GET request before dispatch.
- [ ] Run the productive reconciliation probe at a controlled clock and require two accepted provider reads, zero non-GET attempts, zero business effects, and a healthy reconciliation result.

### Task 6: Reversible isolated-test then GA rollout

**Files:**
- Authority: `/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json`
- Test/GA deploy implementation: `/home/ubuntu/workspace/agente-v2-canary-deploy`

- [ ] Re-run READ → VERIFY immediately before mutation.
- [ ] Capture exact predecessor image IDs/digests, rendered Compose, health, state mounts, and rollback pointer; do not touch Ops.
- [ ] Deploy the immutable candidate to the isolated test API/worker/router only, preserving its existing state mounts.
- [ ] Require container health, fresh healthy heartbeat, all ten queues healthy, `/readyz` 200, and a real GET-only Cloudbeds+Bókun probe.
- [ ] Re-run READ → VERIFY and update the authority only through the canonical control-plane mechanism.
- [ ] If isolated test fails, restore the predecessor image/config and verify it before stopping.
- [ ] Promote the same digest to GA only after isolated evidence is green; preserve GA state and rollback evidence.
- [ ] Require GA container health, fresh healthy heartbeat, all ten queues healthy, public `/readyz` 200, and final `runtime authority: OK`.
- [ ] Leave the legacy static date values present only as ignored compatibility inputs until a separately authorized configuration cleanup removes them.

### Task 7: Non-effect channel smoke

- [ ] With provider writes/payments/handoff closed for the test target, send one authorized `>>>Teste` through WAHA.
- [ ] Correlate WAHA → ManyChat → router → inbox → Maya → outbox → ManyChat → WAHA using timestamps and durable IDs.
- [ ] Require exactly one response and zero reservations, bookings, payments, links, handoffs, or duplicate deliveries.
- [ ] Disable WAHA executor sending again immediately after the smoke and preserve evidence.
