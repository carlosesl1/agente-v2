# Cloudbeds party inventory — acceptance record

## Status

Implemented and qualified in branch `fix/v2-cloudbeds-party-727d3625`, based on the verified GA/TEST source `d60e546d092c80678d5017a2db6706161ed49632`. **Not deployed.** No runtime manifest, live prompt/config, GA, TEST or Ops state changed.

## Behavior and ownership

- Removed the unused `getRatePlans` dependency from the lodging read. A successful empty availability response is now a normal empty exact-party quote, not a technical failure.
- Group reads additionally obtain same-stay single-occupant inventory with shared rooms included, classified by the native catalogue. Original adults/children remain unchanged.
- Single-item priced/selectable `options` and informational `unit_inventory` are separate. Inventory carries quantities, category identity, private/shared classification, provider capacity, query dates and probe/original parties. It has no price, offer ID or choice reference.
- Native rate-plan duplicates do not add stock. Conflicting duplicate facts and malformed data fail explicitly. Dorm capacity is not multiplied by the number of beds.
- Maya interprets preferences and combinations; no phrase matching, reply rewrite, allocation engine or new business gate was introduced. Errors in authentication, transport or native schema are not recast as sold out.
- Existing single-person queries, provider binding and effect-time private quote resolution remain separate from the informational lookup.

## Evidence

Artifacts: `/home/ubuntu/workspace/v2-cloudbeds-party-727d3625/`.

1. Red/green: `red.log`, `inventory-red.log`, `malformed-red.log` preserve failures before their corresponding changes. The focused regression covers empty/sold-out, split category data, positive control, child-party preservation, duplicate stock, malformed responses, HTTP errors and rejection of inventory as an offer.
2. `v2-final.log`: **1,855 passed, 44 subtests passed**, including the new integrated turn-executor test and updated transport expectations. One pre-existing Starlette deprecation warning.
3. Earlier broad run `full-1.log`: 2,774 passed / 2,958 subtests passed, with one failed *old request-shape expectation*. That expectation was updated and passed in the subsequent complete V2/phase3 run. Do not describe the earlier broad invocation as all-green or add counts from overlapping runs.
4. `provider.json`: candidate source against actual Cloudbeds, GET-only endpoint allowlist, disposable read-only container with no operational state. November 8–11, 2026: three adults → zero direct offers and nine inventory categories; two adults → two direct offers plus inventory; one adult → normal nine direct offers. No provider writes.
5. `model.json`: eight successful real Maya subprocess calls covering pre-read/post-read in four scenarios. One uses captured real Cloudbeds data; three use **explicitly controlled fixtures**, not claimed actual stock. Child assertions confirm zero tools/capabilities. No operational store/effect executor/channel is composed.
6. Integrated executor test uses an in-memory boundary store, controlled provider HTTP and controlled model proposals. It verifies that empty direct offers plus positive inventory reach the second frame, complete the committed turn, preserve adults=3, and create zero command/relay rows. This is not a live-channel E2E.
7. `check_fasttrack_boundaries.py`, `git diff --check` and new-module lint passed; provider_http lint has no added diagnostics relative to its baseline.

## Manual review of the real-model replies

- **Original private-room request:** explains no single private for three was quoted; offers investigating two privates without substituting shared rooms or saying the hostel is full.
- **Private split accepted:** identifies a two-person private plus a one-person private, preserving the total group and not inventing a quote.
- **Shared split accepted:** identifies two beds in one mixed dorm and one in another, without treating dorm maxGuests as bed capacity.
- **Insufficient private stock:** one available double is not represented as housing all three.

Review was performed directly, without delegated reviewers or lexical acceptance gates.

## Deliberate limits and release prerequisites

- Inventory is an accommodation alternative, not an allocation/price guarantee. This change does not implement automatic multi-item lodging reservation. If the customer wishes to book such a split, the existing single-item execution contract cannot be misrepresented as group coverage; team handling remains necessary.
- Consultation history stores exact-party quotes, not the new inventory view; prompt semantics clarify that its negative status is not whole-property unavailability. New availability claims need a new lookup.
- Live WhatsApp delivery, ManyChat, reservations, payment collection and settlement were not exercised or altered by this task.
- Publishing must use the verified runtime-authority release workflow, including the candidate prompt in the generated effective model configuration. A new image alone does not prove that an externally mounted old prompt changed.
