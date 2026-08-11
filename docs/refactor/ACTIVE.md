# Refactor control — Maya V2 agent process and read enrichment

## Authority

- Authorized by Carlos Eduardo on 2026-08-11: preserve the stable Maya V2 context work, but remove every privacy/PII mechanism that inspects, changes, corrects, blocks, retries, or fails a Maya reply. Voluntarily supplied customer data may be repeated.
- Explicit prohibition: no deterministic natural-language extractor, no regex/substring/alias logic for customer intent, facts, progression, confirmation, selection, or handoff decisions; no keyword triggers.
- Model remains the semantic and textual owner. Parent code may validate closed schemas, typed facts, structural progress, authenticated provider observations, receipts, idempotency, and transaction authority, but never PII in Maya text.
- Privacy/PII work is forbidden unless Carlos requests it explicitly in the current chat. Do not recommend it. Open-ended privacy work is forbidden.
- No deploy, restart, canary promotion, broad rollout, production provider POST, or real external effect is authorized by this phase.
- Authorized by Carlos Eduardo on 2026-08-11 after the natural-conversation audit: continue with the prompt/skill/process and provider-information approach before adding controller complexity. The approved implementation reuses the existing prompt and read tools; it adds no new skill inventory or controller gate.

## Immutable base

- Base commit: `496799ed0d8c30f9d966fdea9e9c86b546ac992e`
- Base tree: `cde291f6f5b4df015e2609c0470fda2e2805b9fd`
- No new OCI has been built for this phase.
- The base candidate remains immutable. All changes belong to the successor branch below.

## Active successor

- Branch: `maya-v2-agent-process-refinement`
- Required worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-agent-process-refinement`
- Design: `docs/superpowers/specs/2026-08-11-maya-agent-process-and-read-enrichment-design.md`
- Plan: `docs/superpowers/plans/2026-08-11-maya-agent-process-and-read-enrichment.md`
- Starting candidate: `496799ed0d8c30f9d966fdea9e9c86b546ac992e`; the audited candidate and its historical conversation root remain immutable.
- Baseline affected gate: `114 passed` on the starting candidate. The global Python initially lacked pytest; the isolated worktree environment was created with `uv sync --extra runtime --extra dev` and no runtime service was changed.
- Focused RED evidence: the shared-dorm query returned `conexao_feira_vindo_sul`; the new prompt process assertion was absent; and the Cloudbeds room-description payload lacked `room_public_name`.
- Implemented affected gate: `121 passed` across prompt, Cérebro, Bókun reads, Cloudbeds/provider transports, and Hermes model adapter; `git diff --check` passed.
- Exact next gate: the bounded increment is frozen at `b6a2262b2c495fb458738bb7a449d497ef38b681` / tree `a55dc5a770eded4f226fc296ddf9624d2aaaa66d`. `compileall` and committed-diff checks passed. Repository-wide Ruff reports 497 historical findings; the same changed-file selection reports 17 findings on the base and 17 on the successor, delta zero. Freeze the test-only Ruff cleanup, then run the canonical full test suite once. No push, deploy, restart, canary, OCI build, provider write, outbound message, payment initiation, or promotion is authorized.

## Required invariants

1. The complete current customer message remains untouched and model-owned.
2. Recent dialogue is bounded, integrity-checked model context. The parent does not parse customer language or inspect Maya text for personal data.
3. Commercial facts, intent, reads, clarification, selection, confirmation, and handoff remain semantic model outputs under closed contracts.
4. A structurally empty frame may trigger at most one model-owned progress review. Parent code never derives customer semantics from text.
5. Provider results are grounded from accepted typed observations. Granular grounding preserves a separately typed clarification question without parsing prose.
6. Handoff language is derived from the exact durable lifecycle status. Pending relay is never represented as human acknowledgement or active human monitoring.
7. One read round, profile authority, confirmation binding, provider write gates, idempotency, outbox, writer/reconciler, and Stripe/Wise/Pix boundaries remain authoritative without changing Maya text for privacy/PII.
8. Tests and model evaluations use fake business providers and no workers that can create external effects.

## Execution gates

- RED tests must fail for the intended causal reason before implementation.
- For this bounded reversal, focused causal tests, the directly affected gate, canonical clean-environment pytest, `git diff --check`, and a zero-reference scan for the removed output mechanism must pass before the local commit.
- Real-model evaluation is allowed only in a controlled sandbox with tool-free Hermes and fake/no business providers.
- A new immutable commit/tree/OCI may be frozen only after repeated conversational qualification.
- Promotion remains blocked until Carlos explicitly authorizes it after reviewing evidence.
