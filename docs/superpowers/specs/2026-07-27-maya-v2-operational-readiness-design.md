# Maya V2 Operational Readiness Design

## Objective

Make Maya V2 safe and operational for real ManyChat WhatsApp service, initially for an explicit subscriber allowlist, while preserving one-owner ingress, durable idempotency, fail-closed effects, and independent provider gates.

## Current failures and root causes

1. **Conversation blocked by incomplete profile.** `conversation.reduce_turn()` applies `_profile_ready()` after handoff handling and before every other intent. This global gate replaces greetings, FAQ, information, and read-only requests with a profile-completion message.
2. **Fresh provider observations rejected as future data.** `V2TurnExecutor` captures the reference instant before the provider call. Providers stamp `observed_at` after completing the call, so `ReadService` compares a later observation to an earlier reference.
3. **Malformed model output can suppress the customer reply.** The adapter validates strictly, but the surrounding execution path has no deterministic, audited, no-effect fallback after bounded protocol repair fails.
4. **Commercial knowledge is not reachable by the live model.** The V2 knowledge adapter exists, but the live prompt does not teach the model to request `knowledge` reads and only describes lodging plus one tour. The V1 HERMES manual, catalog, and FAQ are not present as a controlled V2 artifact.
5. **Operational expiry is not effective readiness.** Configured gates are reported even when signed public authority has expired. The relay routes by subscriber alone, and cleanup depends on a cron process with Docker access.

## Selected architecture

### Conversation gates

Information and read-only work remain available with an incomplete profile. A complete customer binding is required only when a proposal carries a reservation command, payment initiation, or another identity-dependent write. Handoff remains possible without a complete booking profile.

### Read clock

Capture `observed_now` after each provider invocation and bind the observation against that instant. Keep all existing maximum-age, correlation, and future-observation checks. Tests must continue to reject observations that are actually later than the post-call reference.

### Guaranteed reply

The model receives one strict protocol attempt and one bounded repair attempt. If both are invalid, the parent emits an explicit deterministic fallback proposal with no reads, no command, and no handoff promise. The fallback is localized from the normalized lead language and is recorded as a deterministic fallback, not represented as model output. The turn remains persistable and deliverable under the same public authority allocation.

### Commercial knowledge

Create a versioned, build-time V2 knowledge bundle derived from reviewed facts in:

- V1 `HERMES.md`;
- V1 `config/leads_agent.yaml`;
- V1 `config/cerebro_faq.yaml`.

The bundle contains public commercial guidance, FAQ entries, hostel facts, payment instructions, and the closed tour catalog. It excludes tools, credentials, environment values, raw provider payloads, and legacy write instructions. Product selection uses V2 canonical internal IDs; provider IDs remain in deployment mapping only and are never requested from or shown to leads.

The live prompt teaches the model when and how to request `knowledge`, Cloudbeds, and Bókun reads. Availability, prices, bookings, links, and payment confirmations always require current provider evidence.

### Effects and rollout

Compose every supported capability but gate it independently:

- ManyChat public delivery;
- ManyChat handoff;
- Cloudbeds reservation writes;
- Bókun reservation writes;
- Stripe test payment links;
- Pix instructions and evidence intake;
- Wise instructions and signed settlement intake;
- payment confirmation and post-payment actions.

All gates remain closed in qualification. Limited canaries open one capability at a time for one allowlisted subscriber. Every write requires durable idempotency, fencing, and read-back. Unknown outcomes enter manual review and are never blindly retried.

### Operational authority

Readiness reports effective delivery authority, including signed-manifest validity, unexpired allocation, worker heartbeat, and effective gate state. The relay performs a V2 readiness preflight before choosing V2; if not eligible it sends the request once to the legacy endpoint. It never retries a POST across owners.

The relay also enforces its own cutover deadline and therefore stops routing to V2 without requiring Docker cleanup. Auto-close remains a cleanup layer and must run under an identity that can execute the deploy scripts.

## Acceptance criteria

- Greetings, FAQ, lodging/tour discovery, payment-policy questions, and provider reads work with incomplete profiles.
- Identity-dependent commands are still blocked until required customer data are complete.
- Fresh Cloudbeds/Bókun observations are accepted; truly future or stale observations remain rejected.
- Every valid ingress produces either a valid reply row or an explicit durable manual-review state; malformed model protocol never silently drops a turn.
- The live model can retrieve V1 commercial facts and the complete closed catalog without exposure of provider IDs or internals.
- Effective readiness becomes non-ready on expired authority, missing heartbeat, or unavailable delivery allocation.
- Relay expiry returns traffic to legacy without double ingress.
- Focused tests, full regression, lint, boundaries, live-prompt evaluation, immutable image build, and limited canary all pass before traffic expansion.
