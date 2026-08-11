# Refactor control — Maya V2 conversational context stability

## Authority

- Authorized by Carlos Eduardo on 2026-08-11: preserve the stable Maya V2 context work, but remove every privacy/PII mechanism that inspects, changes, corrects, blocks, retries, or fails a Maya reply. Voluntarily supplied customer data may be repeated.
- Explicit prohibition: no deterministic natural-language extractor, no regex/substring/alias logic for customer intent, facts, progression, confirmation, selection, or handoff decisions; no keyword triggers.
- Model remains the semantic and textual owner. Parent code may validate closed schemas, typed facts, structural progress, authenticated provider observations, receipts, idempotency, and transaction authority, but never PII in Maya text.
- Privacy/PII work is forbidden unless Carlos requests it explicitly in the current chat. Do not recommend it. Open-ended privacy work is forbidden.
- No deploy, restart, canary promotion, broad rollout, production provider POST, or real external effect is authorized by this phase.

## Immutable base

- Base commit: `9d3be1a45a5798a1982f906e1fdf192695e01973`
- Base tree: `9e6a6ac084d377052d8360d4393f2c3af95a83bb`
- Base OCI: `ghcr.io/carlosesl1/agente-v2@sha256:b74fc973c43260199d068921597a6d46e7af43aebb0b7ba7d2a74374e9a43b1b`
- The base candidate remains immutable. All changes belong to the successor branch below.

## Active successor

- Branch: `maya-v2-context-stability`
- Required worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-context-stability`
- Design: `docs/superpowers/specs/2026-08-10-maya-model-owned-public-text-design.md`
- Plan: `docs/superpowers/plans/2026-08-10-maya-model-owned-public-text.md`
- Reversal commit: `8b6c26b3d172c78f4dc304fe66a7d59801adfb0f`.
- Review successor: customer-provided data remains accepted exactly as Maya wrote it, while the independent active-content contract still rejects HTML, Markdown links, internal provider references, technical credential markers, URLs, and control characters.
- Qualification: affected gate `36 passed, 175 subtests passed`; canonical clean-environment gate `1731 passed, 7 deselected, 2953 subtests passed`. The seven exact deselections are unchanged stale Phase 7/index contracts outside this diff.
- Exact next gate: authenticate and independently review this successor, then freeze one local commit. No push, deploy, restart, canary, OCI build, real-model run, or promotion is authorized.

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
