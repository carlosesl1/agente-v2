# V2 handoff: existing ManyChat tag only

## Operator correction and scope

Carlos confirmed in the current chat that applying the existing handoff tag stops all automatic replies for that number in ManyChat. The earlier request for a screenshot, a separate human-assignment flow or an additional pause mechanism was unnecessary and is withdrawn. `111-handoff-live-preflight.json` remains historical; `115-tag-only-correction.json` supersedes its blocker.

Parent: `70359401c3551c9841b6ccdc93e6bf0c378c8803`. This correction changes only the isolated source worktree. No active database, runtime, ManyChat automation, GA, Ops or V3 was changed.

## Minimal correction

- The host handoff adapter applies **only addTag**. It no longer invokes sendFlow or reads/writes a reply field.
- The existing handoff outbox, lead ownership resolution, receipt persistence and effect identity remain unchanged. No new database, queue, pause layer, AI or customer-facing text.
- The confirmed receipt covers tag acceptance, with adapter version 2 and a tag-only digest domain; it does not claim a human answered.
- A pre-dispatch connection failure remains retryable. An uncertain tag result is not blindly replayed.
- Enabled handoff requires the existing API key and tag, not a separate flow. The old flow setting is still parsed for deployment compatibility but is unused by this adapter.

## Validation

- RED before functional edit: `112-tag-only-red.txt`, exit 1 — six causal failures (extra sendFlow and mandatory flow configuration), fourteen passing.
- GREEN: `113-tag-only-green.txt`, exit 0 — **76 passed**. Includes terminal failure after restart, one tag per lead/handoff, transient failure without handoff, invalid recovery without repeated model actions, unknown tag result without retry, independent gates, and composition/settings.
- First full run: `114-tag-only-full.txt`, exit 1 — 2281 passed, 2958 subtests passed, three tests still asserting the old tag-plus-flow behavior. They were updated to tag-only request counts/receipt time and a tag read-timeout, preserving no-retry protection rather than skipping tests.
- Expanded focal run: `117-tag-only-green.txt`, exit 0 — **88 passed**.
- Final full regression: `118-tag-only-full.txt`, exit 0 — **2284 passed, 2958 subtests passed**, two dependency deprecation warnings, no skips/deselections.
- Immutable predecessor receipt → reopened tag-only adapter: `119-tag-only-receipt-compatibility.json`, exit 0 — stored receipt unchanged, adapter idle, zero additional requests. Temporary SQLite and mocked HTTP only.
- Ruff 0.15.10: all checks passed. Fast-track boundaries and `git diff --check`: passed.
- Sources bound in `116-tag-only-tested-source.json` under the local evidence directory.

## Decision and limits

The separate-flow blocker is withdrawn. Local tag-only behavior is validated using a simulated ManyChat transport. This is not evidence of applying the tag on the real contact. The previous `a7864e2` image does not contain this correction; a successor image is required before isolated deployment. Earlier isolated-test authorization is retained; GA promotion and real reservation/payment operations are not authorized by this correction.
