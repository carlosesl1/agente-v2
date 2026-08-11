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
- Static and canonical gate on `bc5bc05fede180effd86e3e073544e8da249bb0a` / tree `afa4a7a2e233ac2cb5a8f0423b8c1133ed13cc02`: `compileall` and committed-diff checks passed; repository-wide Ruff has 497 historical findings while the same changed-file selection has 17 on base and successor (delta zero); canonical clean-environment pytest passed with `1765 passed, 7 deselected, 2953 subtests passed`.
- Real-model fake-read smoke on that candidate: the historical fake `+1` profile correctly selected English and was rejected as invalid harness evidence; the corrected `+55` smoke answered the shared-room question in Portuguese with one knowledge read, zero commands/relays and zero external effects, but manual review marked a WARN for adding an unsupported social benefit.
- Post-smoke causal RED/GREEN: the existing process test now also requires that Maya not add unsupported praise, popularity, suitability, or commercial benefits; the directly affected gate remains `121 passed`.
- First five-scenario attempt on `c5e79b9c8a509fc72b8434c2e575874738a7cc14` was stopped after `agency-solo-4ps` failed manual semantic review: it still converted the canonical "more accessible" classification into unsupported popularity/quality language and asked permission for a read whose observation was already present. The completed scenario recorded one local command and one pending relay, but zero provider/Stripe/ManyChat/external-effect calls; the controller process was intentionally killed while the second scenario was in progress.
- Second causal RED/GREEN: the prompt now states that the accessibility classification is the only pre-description justification and that a post-read frame must answer from the observation instead of asking whether to perform an already completed read; the directly affected gate remains `121 passed`.
- Hotspot rerun on `5c0c2ab255a5ae43989e9bcd6934fd32fc4806c4` / tree `90540c28d260fe68b733cf0acde4f74e7620ac85`: `agency-solo-4ps` no longer invented popularity/quality or asked permission to run an already completed read. Manual semantic review passed with a minor progression WARN because it asked the lead to choose between 4Ps and 2Ms after reading 4Ps. No controller rule is justified for that inefficiency. Static checks and canonical clean-environment pytest passed with `1765 passed, 7 deselected, 2953 subtests passed`.
- Remaining real-model fake-read qualification on the same candidate: `agency-couple-sossego` and `hostel-solo-shared` passed semantic review; `hostel-couple-private` was invalid evidence because the fake knowledge matcher selected the shared-dorm answer on the generic word "hostel" and will be rerun with the specific private entry prioritized; `package-solo-4ps-late` correctly selected the mixed dorm later but initially listed a female-only room to a lead who had already identified himself in the masculine. The package harness also incorrectly expected one command although a valid package creates two local component commands. All runs recorded zero external-effect calls.
- Third causal RED/GREEN: the model-owned prompt now distinguishes an explicitly stated female/male/mixed audience in `room_public_name` from inferred amenities and requires semantic comparison with the lead's original self-identification; the directly affected gate remains `121 passed`. No parser, regex, controller gate, or runtime audience classifier was added.
- Final functional candidate: `fe0e5c58d7dc6e72c72f859a2ee859be159efc49` / tree `4af5f363ff38f288656371a2ee331cfe1e53b3b3`; worktree clean. Technical gates: affected `121 passed`; canonical clean-environment `1765 passed, 7 deselected, 2953 subtests passed`; `compileall`/diff PASS; changed-file Ruff base 10 / candidate 10 / delta zero.
- Final immutable real-model qualification on that same candidate: five historical scenarios, 25 turns, 47 model calls and 24 fake reads; manual semantic review **5 PASS / 0 FAIL**, with nonblocking progression WARNs documented for `agency-solo-4ps` and `package-solo-4ps-late`. Runner mechanics: 3 PASS / 2 WARN / 0 FAIL; both mechanical WARNs are the legacy synthetic-profile expectation `private_fact_names_mismatch:birth_date,gender_expected_`.
- Effect audit: six local commands and six relays, all pending; zero claims, owners, leases, target receipts, ACKs or effect outbox rows; zero Cloudbeds/Bókun HTTP, Stripe, Pix/Wise, ManyChat delivery/handoff, provider refs, reservations, bookings, checkouts, links or charges. Evidence: `/home/ubuntu/maya-v2-agent-process-refinement-fe0e5c5-20260811T231549Z/SEMANTIC_AUDIT.md`; `EFFECTS_BLOCKED` remains present and no approval-for-effects marker exists.
- Exact next gate: user review only. This phase is complete locally. Promotion remains NO-GO until a separate explicit authorization for push/deploy/canary/effects; no such authorization exists in this chat.

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
