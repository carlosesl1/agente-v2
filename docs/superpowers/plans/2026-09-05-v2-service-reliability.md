# V2 service reliability — implementation plan

> Execute with bounded subagents and causal RED → GREEN. This is the successor authorized in the current chat, not a continuation of historical phases.

**Goal:** fix the twelve reproduced service failures without lexical heuristics or production effects.
**Architecture:** Maya owns semantic intent and public text. Runtime owns typed contracts, durable inbox batch identity/retry eligibility, ordered outboxes, confirmed command membership, clocks and schema compatibility. Independent file ownership permits parallel work; integration and final gates are controller-owned.
**Tech Stack:** Python, SQLite, pytest, existing V2 adapters.

## Global constraints
- Base is the verified GA commit b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3, branch fix/v2-service-reliability, worktree /home/ubuntu/agente-v2/.worktrees/service-reliability.
- No regex, keyword/substring triggers, natural-language parsers, output rewriting, or lists of customer scenarios as runtime logic. Exact typed enums/field names/provider schemas are not natural-language classifiers.
- No privacy/PII work, V3/legacy reuse, live database editing, production branch change, deploy/restart or external effects.
- Pix and Wise retain their own distinct evidence validation; do not make them Stripe-like APIs. Financial activation/new settlement integrations are outside this correction batch. Unimplemented post-booking actions must remain honestly unavailable and handled semantically by Maya/handoff.
- All new regressions must fail on the baseline before their fix. Tests use temporary stores/fake transports. No global credential env.
- Preserve confirmation/idempotency/fences and positive controls. Do not weaken tests just to turn green.

## Task A — inbox durability and lead-local progress (B2/B6)
Owner files: v2_application/inbox.py, v2_application/inbox_worker.py; tests/test_v2_inbox*.py plus tests/test_v2_inbox_reliability.py. No edits to worker_main, executor, schemas outside inbox.
- [ ] Reproduce poison lead starvation and commit-before-ACK rebatching from audit delivery/reproduce_delivery.py.
- [ ] Persist original batch membership across release/lease expiry/restart; process it before newer messages for that lead. Use existing executor receipt replay on same batch.
- [ ] Persist retry eligibility per lead/batch so an errored oldest lead does not monopolize unrelated work; preserve event ordering and do not discard pending work.
- [ ] Add restart, duplicate, positive-control and two-lead regressions; run focal tests with clean env.

## Task B — conversational contracts (B3/B10/B11/B12)
Owner files: v2_contracts/model.py, v2_adapters/hermes_model.py, v2_application/turn_plan.py, v2_application/conversation.py, v2_application/turn_executor.py, config/v2_luna_system_prompt.txt and their tests.
- [ ] Reproduce media-empty request, refresh suppression, cancelled handoff guard and confirmation locale failure from audit probes.
- [ ] Carry explicit structured attachment presence/unavailability to Maya so media-only turns can produce a model-owned limitation response, with no fabricated extraction or payment validation.
- [ ] Respect explicitly requested reads rather than overriding semantic intent by query equality; preserve efficient recap where Maya requests no read.
- [ ] Use lifecycle activity for handoff guard/reopening and allow locale-only confirmation updates without changing commercial consent.
- [ ] Test positive active handoff/confirmation guards, serialization compatibility and no lexical dependence.

## Task C — commercial completeness and provider facts (B7/B9)
Owner files: v2_application/relay_worker.py, v2_application/outcome_projector.py, v2_application/completion_projector.py, reservation_execution/sqlite_store.py if necessary, v2_adapters/provider_http.py, related tests. No edits to boundary/worker_store.py, conversation/executor, host/composition.py or host/worker_main.py.
- [ ] Reproduce staggered package relay vs both-relayed control and partial Cloudbeds daily prices.
- [ ] Derive expected component membership from authoritative persisted bundle/commands, never current row count; publish/pay only a complete resolved group. Keep economic identities stable.
- [ ] Validate full nightly coverage before using daily sums, preserve authoritative totals, and reject unevidenced availability/currency rather than inventing defaults.
- [ ] Test single reservations, package partial failure/replay/restart and provider incomplete/duplicate date shapes.

## Task D — runtime, handoff, public ordering (B1/B4/B5/B8), controller-owned
Files: v2_host/worker_main.py, v2_host/composition.py, v2_host/app.py if necessary, reservation_followup/workers.py and sqlite_store.py, reservation_boundary/worker_store.py; dedicated reliability tests.
- [ ] Prove advancing completion clock for handoff and partial/unknown terminal persistence; select compatible schema through controlled source-owned migration, never live DB edits.
- [ ] Order pending public chunks by durable lead turn sequence and block successors behind earlier leased/retryable chunks.
- [ ] Decouple durable inbound acceptance from a busy worker, while retaining honest readiness/health and admission authorization; fresh completion timestamps and non-spurious bounded liveness under long work.
- [ ] Test blocked work, new inbound admission, stale/dead worker, restart and expired leases. Do not introduce shared SQLite connections across threads.

## Execution / evidence
Audit inputs (read-only): /home/ubuntu/workspace/v2-service-audit-43d84d54/RELATORIO.md and its synthetic scripts. Original bug assertions must not be mistaken for corrected behavior.
Evidence outputs: /home/ubuntu/workspace/v2-service-fixes-43d84d54/<lane>-{red,green}.log and <lane>-report.md; checkpoint partial results there before timeout.
Runner:
```sh
env -i HOME=/tmp PATH=/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
 PYTHONPATH=/home/ubuntu/agente-v2/.worktrees/service-reliability:/home/ubuntu/agente-v2/.worktrees/service-reliability/tests \
 HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/agente-v2/.worktrees/service-reliability/config/leads_agent.yaml \
 /home/ubuntu/agente-v2/.worktrees/production-ga/venv/bin/python -m pytest -q -p no:cacheprovider <exact test paths>
```
- [ ] Integrate and review bounded lane diffs and concrete regressions.
- [ ] Freeze candidate; run full canonical pytest using only seven existing CI exclusions, fasttrack boundary guard, diff and changed-file lint checks.
- [ ] Independent read-only review of final candidate; resolve concrete material findings with new focal RED/GREEN.
- [ ] Preserve local commit, proof and honest remaining gates. No live promotion implied by green offline tests.
