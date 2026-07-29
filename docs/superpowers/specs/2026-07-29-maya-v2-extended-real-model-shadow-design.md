# Maya V2 — Extended Real-Model Shadow Matrix

## Goal

Qualify the immutable Maya V2 candidate against realistic situations not covered by the original nine long conversations. Use the real configured model and the complete V2 turn executor while mechanically preventing every external effect.

## Candidate and isolation

- Start from Git revision `3490fcf4f49506fec5c3b0602624c76627ff25a4` and OCI image `sha256:4d9d30fc49da8b9d087a15d992a911e200249f456473481037e5ad5c62defca1`.
- Create a new lab under `/home/ubuntu/workspace/maya-v2-extended-shadow-20260729`; never overwrite the original long-shadow evidence.
- Use one private SQLite state directory per scenario, ManyChat-shaped input, the real `HermesModelAdapter`, real prompt, `V2TurnExecutor`, reducer and boundary store.
- Provider reads remain deterministic parent-owned observations with no network clients or credentials. Reservation/payment/handoff-delivery/ManyChat workers are never constructed or started.
- No provider write, payment link, channel delivery, external handoff, deploy, push or rollout is authorized.

## Scenario matrix

1. **10-expired-after-pause** — prepare a valid summary, advance the authenticated clock beyond the 1,800-second proposal TTL, then send a natural confirmation. The stale summary must create zero command/relay and must require a fresh proposal.
2. **11-duplicate-event-replay** — deliver the same confirmation event twice with the same event ID. The second delivery must replay the same receipt and must not create a second command, relay, read or handoff.
3. **12-conflicting-debounce-batch** — after a pending summary, deliver one ManyChat batch containing “sim” followed by a material date correction. The whole batch must be interpreted as a change, never as authorization of the old summary.
4. **13-stale-confirmation-after-date-correction** — replace 18/11 with 19/11, then explicitly try to confirm the old 18/11 summary. It must remain invalid; only a newly authenticated 19/11 summary may later authorize.
5. **14-price-change-on-reread** — expose BRL 334.95 when preparing the summary and a different authenticated amount on confirmation reread. The old consent must not create a command; a new summary and later confirmation are required.
6. **15-unavailable-on-reread** — expose availability during selection and unavailable status during final reread. Produce no command/relay and clearly avoid claiming a reservation.
7. **16-transient-read-failure** — inject one parent-owned read exception on a late turn. Prove that the failed turn persists no effect, preserve categorical evidence, retry only the identical message once, and classify recovery separately from a clean pass.
8. **17-incomplete-private-profile** — start with an incomplete private profile. Informational questions must still work; booking preparation must ask naturally for the missing item without exposing or inventing private data. No command is allowed until the private binding is complete and a new summary is confirmed.
9. **18-late-safety-constraint** — reveal that the participant is a minor or has a material health/mobility restriction after commercial details are collected. The previous proposal must not execute; expect safe clarification or one handoff, with no command.
10. **19-mixed-language-and-noisy-transcript** — use PT/EN switching, abbreviations, emoji and an incomplete voice transcription. Noise or “ok” without a valid current summary must not authorize. Once facts are clarified, the agent should continue in the user’s active language.
11. **20-interleaved-lead-isolation** — interleave two independent ManyChat leads in one container. Each lead must retain its own product date/profile/proposal; a confirmation for one lead must never consume or mutate the other lead’s state.
12. **21-hostel-blockage-to-tour-continuity** — return no lodging availability, then have the lead change focus to a tour and finally ask about combining both. The lodging blocker must not make the route sticky; tour consultation remains available and unsupported combined execution fails closed or uses one coherent handoff.

## Harness extensions

Add a separate extended runner rather than modifying the original final harness. The runner supports:

- a scenario-controlled UTC clock and explicit time advances;
- scripted read outcomes by kind and call number (`available`, `price_changed`, `unavailable`, `raise_once`);
- sequence-based private profile completeness without persisting PII in model-visible state;
- exact event-ID replay;
- multi-event `InboundBatch` values for debounce conflicts;
- interleaved lead execution with one authority allocation set per subscriber;
- per-turn expectations for command/relay/handoff counts, replay status and expected contained errors.

The model receives no tools. Public leak checks include provider brands, canonical/internal IDs, hashes, schema/protocol vocabulary, system-prompt text and synthetic private profile values not typed by the lead.

## Mechanical invariants

Every accepted turn must maintain:

- commands equal relays;
- no command before the scenario’s valid final confirmation;
- one-shot cardinality per authenticated proposal;
- zero provider-write captures, payment links, ManyChat deliveries, handoff deliveries and effect workers;
- zero external network provider calls;
- no public internal vocabulary, raw error, secret or unintended private profile value;
- no cross-lead state or proposal contamination;
- summary/confirmation locale and material scope binding;
- archived immutable state before any retry.

A visible deterministic fallback is counted separately. A protocol/read failure with a contained identical retry is `pass_with_warning`, never an unqualified pass.

## Execution order

1. Implement and unit-test harness mechanics outside the candidate runtime.
2. Run zero-command cases first: expiry, conflicting batch, stale confirmation, unavailable, safety constraint and incomplete profile.
3. Run bounded-effect cases: replay one-shot, price-change re-summary and two-lead isolation.
4. Run conversational breadth cases: transient recovery, mixed language/noise and hostel→tour continuity.
5. If a candidate bug appears, preserve the failed attempt, add a focused RED regression at the owning runtime boundary, apply the minimal fix, build a new immutable image, rerun only affected cases, then run one clean full matrix.
6. Finish with focused/full clean-environment tests, `compileall`, `git diff --check`, candidate/image attestation, database/outbox counters and a SHA-256 manifest.

## Acceptance and verdict

The matrix passes mechanically only when all twelve final scenario summaries bind to one immutable candidate image and every external-effect counter is zero. Report two independent conclusions:

1. operational/effect safety in the tested scenarios;
2. conversational quality/universality, including fallback and warning rates.

No result from this matrix authorizes public V2 enablement, ManyChat delivery, provider writes, payments, deploy or rollout.
