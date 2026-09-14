# V2 rolling provider-read probe dates — design

## Authority and scope

Carlos authorized this correction in the current chat on 2026-09-14 after the verified production incident where static probe dates expired, degraded `reconciliation`, made `/readyz` return 503, and prevented a ManyChat event from entering Maya V2.

The authenticated base is GA commit `0d790e7c8ce842a37abd5baab1035c1b65f774fd`. Work is limited to V2 in branch `fix/v2-service-reliability` and worktree `/home/ubuntu/agente-v2/.worktrees/service-reliability`. V3 and the legacy checkout are outside scope.

## Goal

Make mandatory Cloudbeds and Bókun read probes derive valid future dates from the worker's explicit clock on every fresh probe, so calendar passage cannot make an otherwise healthy provider integration block V2 ingress.

## Considered approaches

1. **Runtime-owned rolling calendar window — selected.** Convert the worker's explicit UTC `now` to `America/Bahia`, then derive the probe dates. This is deterministic, testable across calendar boundaries, and requires no scheduler.
2. **Periodically rewrite environment dates — rejected.** This leaves correctness dependent on an external scheduler, restart timing, and mutable deployment configuration.
3. **Move the static dates farther into the future — rejected.** This only postpones the same incident.

## Runtime contract

A fresh probe computes exactly one business-local base date:

- `business_today = now converted to America/Bahia, then .date()`;
- lodging check-in = `business_today + 30 days`;
- lodging check-out = `check-in + 3 days`;
- activity date = lodging check-in.

The same computed values are used in the request IDs and typed `ReadRequest` fields. Dates use the worker cycle's explicit UTC `now`. Each returned observation is accepted against a fresh UTC sample taken after its corresponding GET, because productive adapters stamp observations during the call and an earlier cycle time would falsely classify them as coming from the future.

The existing `V2_READ_PROBE_CHECK_IN`, `V2_READ_PROBE_CHECK_OUT`, and `V2_READ_PROBE_ACTIVITY_DATE` settings remain temporarily loadable and validated for deployment compatibility, but they no longer select provider query dates. This makes the currently deployed environment forward-compatible while ensuring stale configured dates cannot reintroduce the failure. Product ID and probe interval remain configuration-owned.

### Explicit GET-only activity probe

The activity probe sets `ReadRequest.availability_only=True`. This is an exact boolean contract accepted only for `ReadKind.ACTIVITY`.

- The default remains `False` and is omitted from canonical bytes, preserving existing commercial read identities.
- `True` is included in canonical bytes, so a health probe cannot share identity with a quote-bearing commercial read.
- `BokunReadAdapter.read()` routes the explicit flag to `read_availability_only()`.
- `GroupEnrichedActivityReadAdapter` may still perform its read-only group lookup, but it short-circuits all selection paths and delegates to the Bókun availability-only route.
- The Bókun HTTP transport contract proves that `availability_only=True` performs only GET requests even when quote checkout is enabled.

## Boundaries and failure behavior

- The probe remains physically read-only: Cloudbeds and Bókun GET paths only, including when GA write capabilities are enabled.
- No reservation, cart, payment, ManyChat delivery, handoff, or provider write is added or enabled.
- A real provider or contract failure still degrades `reconciliation`; this change removes only calendar-expiry as a false failure source.
- The existing cache interval and fail-until-next-real-probe semantics are unchanged.
- No active SQLite, WAL, or SHM file is edited.

## Tests

The causal regression exercises the real `ReconciliationStage` with recording fake read ports and stale legacy dates. It must fail on the immutable base because the old code emits those stale dates.

Required witnesses:

1. Before Bahia midnight, the business date is still the prior local day even when UTC has advanced.
2. At Bahia midnight, the derived window advances by one day.
3. Month/year and leap-calendar arithmetic remain valid through `timedelta` date arithmetic.
4. Check-out is always three days after check-in, and all selected dates are future relative to the Bahia business date.
5. Stale legacy environment dates never appear in request IDs or typed requests.
6. Each `accept()` receives its own post-read exact UTC sample; the event order is `read → clock → accept` for lodging and then activity.
7. Existing degraded-cache behavior remains unchanged.
8. A complete `ReconciliationStage → V2ReadService → group enrichment → BokunReadAdapter → BokunHTTPTransport` witness proves that a matched-group/two-participant probe performs only the two expected GETs and exposes no offer/selection.
9. Default commercial activity canonical bytes omit the new flag, while probe bytes bind `true`.
10. Mutation witnesses must kill both a pre-GET shared acceptance sample and a `V2ReadService` availability-flag drop.

## Qualification and rollout

After focal RED→GREEN, run changed-file static checks, the directly affected settings/composition tests, and the canonical clean-environment suite. An independent read-only review must approve the exact final SHA. Build a new immutable OCI image carrying that revision, prove identity and GET-only behavior in dark/read-only qualification, then deploy first to the isolated test runtime and only after it is green to GA. Preserve rollback image/config/state evidence and re-run the canonical runtime-authority verifier after each promotion. Ops remains untouched and read-only.
