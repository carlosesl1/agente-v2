# V2 local release preparation — Implementation Plan

**Goal:** qualify the simplified candidate as an offline executable image before any isolated-contact cutover.
**Authority:** Carlos's current “Siga” after the preparation/isolated-validation proposal. Inline execution; no subagents. Local base `d63985830b7aa2254b07dda428494dcfbc7778cc`.
**Architecture:** keep Dockerfile.v2 as the current runtime packaging owner. The Phase 7 stdlib wheel is historical, not a second V2 distribution. Test its original immutable source rather than rewriting old evidence to match a later runtime.
**Scope:** existing atendimento-simples branch/worktree. No GA/test/Ops restart, live channel, provider writes, payment, active-state edit, V3, or legacy work. Promotion and real handoff remain separately authorized.

## 1. Historical/current contract separation
- [ ] Reproduce `tests/test_phase7_closeout.py tests/test_phase7_package.py tests/test_phase8_entry.py` with clean-env Python 3.12 pytest.
- [ ] Authenticate Phase 7 closeout `93682024b4867d3e313324339a7060d5351dcd3d`, tree `b779e35c671f3050d056c6ef3c8c0700f5b13f35` (pinned by Phase 8 entry).
- [ ] Scope six historical package/manifest/validator tests to that immutable archive via `tests/phase7_snapshot.py`, rerunning original unittest selectors in a stdlib-only temporary environment. Missing history or assertion failure fails; no skip/xfail.
- [ ] Update only the superseded Phase 8 index assertion and truthful README status: ACTIVE owns current work; verified ACTIVE_RUNTIME owns deployed components. Preserve quarantine evidence.
- [ ] Remove the seven obsolete deselections from `.github/workflows/phase8.yml` only after all seven execute successfully.

## 2. Current candidate closure
- [ ] Add `tests/test_v2_release_preparation.py` to enforce no CI exclusions and current Docker package/config coverage, independent of historical wheel success.
- [ ] Run focal, static/boundary and one full suite without deselections. Keep logs in the existing external evidence directory.
- [ ] Review inline and commit only changed tests/docs/workflow; no behavior refactor.

## 3. Image and isolation proof
- [ ] Build once from a clean Git archive of the verified commit using Dockerfile.v2 and explicit revision labels; record actual image identity, not an invented registry digest.
- [ ] Exercise current module/config bytes, ordinary API entrypoint, worker startup, inbox migration/reopen and existing handoff integration in a network-denied container with disposable state and no credentials.
- [ ] Preserve rollback artifacts and rerun runtime authority. Record image/startup evidence and exact limits.
- [ ] Stop before replacing the active isolated runtime or enabling a real channel. No claim of Maya live-model qualification or human pickup from offline receipts.
