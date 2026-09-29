# Cloudbeds group inventory — implementation and acceptance

**Authorization:** Carlos requested the diagnosed fix and clarified that no three-person room does not mean no three places across different items. This implements that approved direction inline, without subagents.

**Goal:** Return legitimate empty exact-party quotes normally and give Maya fresh, separate inventory facts for discussing distribution across rooms/shared units. No new transactional multi-room reservation contract is implied.

**Architecture:** Cloudbeds transport owns GETs and factual normalization; the read adapter owns public observations; Maya alone owns suitability, preference, distribution suggestions and prose. Existing offer IDs and per-party binding continue to authorize only their original single-item selection. Supplementary inventory has no offer/choice ID, no group total and no effect authority.

## Constraints
- Worktree `.worktrees/cloudbeds-party-727d3625`, branch `fix/v2-cloudbeds-party-727d3625`, from verified active GA source.
- No V3, legacy writes, subagents, customer-text parsing, extra semantic reviewers, new financial effects, active SQLite edits or production rollout without a separate explicit cutover.
- True provider/HTTP/schema errors remain errors; never recognize sold-out status by error-message text.
- Shared-unit `maxGuests` is not automatically capacity per bed: expose provider classification and the actual probe occupancy, not a guessed hostel capacity sum.
- No calendar-day summation, duplicate rate-plan inventory summation, stale availability reuse, or group total invented from a one-person price.

## Tasks (inline execution)

1. RED/GREEN: remove unused getRatePlans dependency in `_lodging`. Test empty availability + failing rates with HTTP MockTransport, and prove real failures still raise. Keep positive quote/offer binding unchanged.
2. RED/GREEN: introduce a bounded supplementary `unit_inventory` when an informational group read explicitly requests it through the adapter. Native query for one adult, no children, one unit, includeSharedRooms=true, with same stay dates; getRoomTypes supplies explicit isPrivate. Normalize unique room-type identity, live available-unit count, native capacity, current public name/description and inventory-only scope. No price, offer ID or group eligibility assertion in inventory. Deduplicate same-type rate variants without adding quantities; conflicting facts fail rather than publish false availability. Missing catalogue/classification/count/capacity is a provider contract error.
3. RED/GREEN: expose validated inventory publicly, with requested-party/date binding and explicit quote-vs-inventory semantics; private resolver never uses inventory as an offer. Keep single-person reads compatible. Test mixed private/shared units, capacity contradictions, zero stock, exhausted stock, duplicates and malformed shape. Verify full model wire preserves inventory without generating selectable choice refs.
4. Update Maya's existing system prompt: empty single-item quotes do not prove property sold out; discuss partition across observed units respecting capacity and preference; ask about division only if not established; shared vs private and gender compatibility remain distinct; price only the authenticated quoted occupancy, not an invented group total; preserve total party facts. For a multi-item booking, do not select one partial-party offer as if it covered everyone. Existing confirmation/effect contract remains unchanged; unsupported transaction execution is not claimed.
5. Run isolated focused/full regression, boundary guard, lint, real GET-only provider observation, real Maya pre/post-read consultation with no effect adapters, including an inventory split across different categories, and negative insufficient-stock control. Keep controlled fixtures distinct from real provider responses.
6. Commit clean reviewed candidate; report scope and publication honestly. Production identity remains the manifest until a separately authorized rollout.

## Verification commands

```bash
HERMES_LEADS_AGENT_CONFIG_PATH=/tmp/no-live-config python -m pytest -q tests/test_v2_cloudbeds_party_inventory.py tests/test_v2_provider_http_transports.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Evidence lives outside Git in `/home/ubuntu/workspace/v2-cloudbeds-party-727d3625/`. Baseline transport tests: 11 passed before modification. Full suite, model/provider results and exact commit are recorded only after execution.
