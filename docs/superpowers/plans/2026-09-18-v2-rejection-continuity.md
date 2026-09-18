# V2 rejection continuity — Implementation Plan

> Execute inline with executing-plans and TDD; Carlos prohibits subagents.

**Goal:** A rejected action produces one factual continuation to Maya, not an unbounded reprocessing loop.
**Architecture:** Reuse ModelRequest, authored reply, existing turn receipt/outbox and inbox manual_review. No new agent, queue, table, semantic review or customer-text filtering.
**Tech stack:** Python 3.12, dataclasses, SQLite, pytest.

## Authority and boundaries
Carlos explicitly requested "implemente a correção sem complicar as coisas" after the offline rejection witness. Base 8f92904; same isolated worktree and branch in ACTIVE.md. Local implementation/tests only. No deploy, external model/channel/provider calls, active databases, GA/isolated runtime changes, Ops deployment, V3 or legacy edits.

## Task 1 — rejected action reaches a terminal conversational outcome
Owners: v2_contracts/model.py (optional factual rejection and terminal contract error); v2_adapters/hermes_model.py (wire); config/v2_terra_system_prompt.txt (same Maya authors); v2_application/turn_executor.py (one communication-only continuation).
Tests: tests/test_v2_rejection_continuity.py; update prior executor tests which expect silence while preserving no-effect assertions.
- [ ] RED: real inbox/executor and temporary SQLite, network denied; invalid selection followed by authored response must commit, preserve source identity/context, create zero commands and free following input. Post-read rejection must preserve observations without repeating reads.
- [ ] GREEN: add `action_rejection: str | None = None` to ModelRequest. Serialize as a factual rejection; no mode-specific output schema. Return exact inform-only Maya proposal through existing finalization, with all commands/relays absent. No recursive recovery; a repeated non-inform/effectful answer is terminal.
- [ ] Verify original reply is absent, recovery chunks exact, durable receipt replay and no further model/command; reject effect/read/source mutation on continuation. Keep CAS and uncertain commercial results distinct.

## Task 2 — bounded infrastructure failures through the existing inbox
Owners: v2_application/inbox.py and inbox_worker.py. Add failure_count and failure_reason columns with additive owner migration; reuse manual_review status. No new table.
- [ ] RED: after three execution failures and reopening SQLite each time, old batch is manual_review and newer input can proceed; errors during ACK retain commit replay.
- [ ] GREEN: increment failures only on execution failure. Invalid communication contract is immediately manual_review. Transient execution failures get at most three failed attempts. Persist categorical diagnostic; never synthesize a customer reply or claim a human was notified. Interruptions/ACK failures retain ordinary release/replay.
- [ ] Verify migration, stale-claim atomicity, repeated rejection, ordinary transient recovery, healthy lead and post-commit replay.

## Task 3 — local closure
- [ ] Focused pytest with env -i, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null; whole diagnostic against seven historical failure IDs.
- [ ] Inline diff review, Ruff, compile, `python3 scripts/check_fasttrack_boundaries.py`, package manifest refresh only if affected, authority verify.
- [ ] Evidence in docs/refactor/evidence; exact-path local commit and post-commit source/log hashes. No deployment.

Terminal limitation: manual_review records operational intervention needed; it does not invent channel delivery or dispatch a human notification. If Maya/infra cannot produce a valid answer, report that explicitly rather than claiming every customer is answered.
