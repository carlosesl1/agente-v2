# Task 9 runtime checkpoint — atomic v8 turn executor

Date: 2026-07-23
Status: COMPLETE for Runtime Task 3; Task 9 remains `NEXT`
Functional commit: `ba19de547f57d568575d31f90c386b840915a815`

## Delivered

- `AuditedModelTurn` commits exact child stdin/full stdout hashes and HMAC-derived frame evidence.
- `HermesModelAdapter.complete_audited` retains exact exchange evidence without exposing raw bytes in repr.
- `V2TurnExecutor`:
  - detects exact receipt replay before profile/model/read calls;
  - loads/acquires the v8 boundary fence in short transactions;
  - runs private profile, model and provider reads with no SQLite transaction open;
  - permits one closed read round and rejects duplicate/multi-round proposals;
  - validates model `source_event_id` against the leased batch;
  - revalidates state, profile TTL, read TTL, public authority deadline and total turn deadline before commit;
  - retries the whole turn after stale CAS/fence and discards the obsolete decision;
  - writes state, receipt, transcript frames, Phase 8 read artifacts, accumulated projection facts, Maya closure/proposal, kernel decision, commands, command relays, handoff jobs and public rows through one `commit_turn_v8` call.
- V2 availability reads are converted into canonical `Phase8ToolReadRequest`, `SanitizedLookupResult`, evidence receipt and `ReadObservation` artifacts without provider raw IDs.
- Commercial `query_hash` excludes per-call `request_id`, so fresh re-reads preserve offer/binding identity while request correlation remains unique.
- Conversation projection is reconstructed from accumulated Maya facts + route and verified against `behavior_state_snapshot_digest`.
- The SQLite v8 DDL/schema fingerprint is unchanged; no new table or artifact enum was added.
- Handoff requests create a durable active `HandoffWorkflow`, one internal job and an effect guard.

## RED evidence

The first focused run failed because the executor module did not exist:

```text
ModuleNotFoundError: No module named 'v2_application.turn_executor'
```

Subsequent REDs failed at the intended boundaries:

```text
AttributeError: 'V2TurnExecutor' object has no attribute 'execute'
TurnExecutionError: audited read loop is not implemented
TurnExecutionError: command relays are not implemented
```

The atomic fault test injects failure after the public outbox insert and verifies rollback of state version, event, artifacts, public row and authority allocation CAS.

## Final verification

Focused runtime/domain gate:

```text
25 passed, 6 subtests passed
```

Proportional V2 + Phase 8 + domain gate:

```text
254 passed, 1 deselected, 1 warning, 486 subtests passed
```

The single deselected test is the pre-existing documentation baseline:

```text
tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```

It expects the old phrase `8. Shadow, canary e rollout | **design aprovado`; the unchanged `docs/refactor/README.md` already records the active fast-track wording. All other tests in that file remained included.

Static gates:

```text
All checks passed!
fasttrack-boundaries: OK
verification-static:OK
schema-v8-unchanged
secret_hits=[]
unfinished_flags=[]
```

## Safety and rollout

- No deploy, restart, provider write, charge or public message occurred.
- All public rows used preinstalled synthetic `conversation_test` authority.
- Real Cloudbeds/Bókun/payment/ManyChat gates remain closed.
- Rollout remains `NO-GO`.

## Next gate

Runtime Task 4: connect durable inbox claims to `V2TurnExecutor`, then relay command/internal/public owned rows through concrete workers with crash replay and no noop/fallback stages.
