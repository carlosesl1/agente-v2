# Detailed Stripe Checkout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace generic Stripe Product copy with deterministic, non-PII commercial details for lodging, activity, and package-component payment links.

**Architecture:** Derive immutable checkout details in `ReservationOutcomeProjector`, persist them inside the hashed `PaymentSelection`, carry them through `StripeLinkRequest`, and render a bounded Stripe Product name/description in a focused adapter helper. Preserve legacy decode, but fail closed before HTTP when a legacy obligation without details attempts a new Stripe effect.

**Tech Stack:** Python 3.12, frozen dataclasses/enums, SQLite STRICT tables, `httpx`, Stripe form API, `pytest`, Docker.

## Global Constraints

- Use only signed `OfferSnapshot` fields and the confirmed provider outcome; never inspect free-form conversation text.
- Never include customer name, email, phone, country, birth date, gender, or passenger names.
- Fixed labels are compact PT/EN; do not infer language.
- Existing completed links remain readable and are never recreated or modified.
- Product name maximum: 120 characters; Product description maximum: 500 characters.
- Package components remain separate links and receive a `Pacote / Package —` prefix.
- Preserve one fenced Stripe dispatch slot and existing Product → Price → Payment Link idempotency keys.
- No network/provider calls in tests; use `httpx.MockTransport` and local SQLite only.

---

### Task 1: Immutable display contract and legacy-safe persistence

**Files:**
- Modify: `v2_contracts/payments.py`
- Modify: `v2_application/payments.py`
- Test: `tests/test_v2_payment_initiation.py`

**Interfaces:**
- Produces: `CheckoutService(str, Enum)` with `LODGING` and `ACTIVITY`.
- Produces: `PaymentDisplayDetails(service, public_label, start_date, end_date, start_time, adults, children, reservation_total_minor, package_component)`.
- Extends: `PaymentObligation.display_details: PaymentDisplayDetails | None = None`.
- Extends: `ReservationPaymentContext.display_details: PaymentDisplayDetails | None = None`.
- Extends: `StripeLinkRequest.display_details: PaymentDisplayDetails | None = None`.

- [ ] **Step 1: Write failing validation and round-trip tests**

Add fixtures for lodging and activity details and tests that assert:

```python
ACTIVITY_DETAILS = PaymentDisplayDetails(
    service=CheckoutService.ACTIVITY,
    public_label="Roteiro dos 4Ps",
    start_date=date(2026, 12, 3),
    end_date=None,
    start_time="08:30",
    adults=1,
    children=0,
    reservation_total_minor=33495,
    package_component=True,
)
```

- activity forbids `end_date` and accepts optional `start_time`;
- lodging requires `end_date > start_date` and forbids `start_time`;
- public labels are normalized, non-empty, NUL-free, and bounded;
- adults are at least one; children are non-negative; total minor units are positive;
- `_selection_bytes()` writes the exact closed `display_details` object;
- `_selection_from_bytes()` round-trips the current shape;
- `_selection_from_bytes()` decodes a legacy selection without `display_details` as `None`;
- an extra display field is rejected as corrupt.

- [ ] **Step 2: Run RED**

Run:

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_payment_initiation.py::test_payment_display_details_validate_closed_service_shapes \
  tests/test_v2_payment_initiation.py::test_payment_selection_round_trips_display_details_and_decodes_legacy_rows \
  tests/test_v2_payment_initiation.py::test_payment_selection_rejects_unknown_display_fields
```

Expected: collection/import failure because the display contract does not exist.

- [ ] **Step 3: Implement the closed contract**

In `v2_contracts/payments.py`, add exact validation and copy `display_details` in `PaymentService.plan()` when creating `PaymentObligation`.

In `v2_application/payments.py`, serialize display details using ISO dates and exact primitive values. Decode only these keys:

```python
{
    "service", "public_label", "start_date", "end_date", "start_time",
    "adults", "children",
    "reservation_total_minor", "package_component",
}
```

Treat a missing top-level `display_details` as legacy `None`; reject present values that are not exact dicts with the exact key set.

- [ ] **Step 4: Run GREEN and focused payment tests**

```bash
venv/bin/python -m pytest -q tests/test_v2_payment_initiation.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add v2_contracts/payments.py v2_application/payments.py tests/test_v2_payment_initiation.py
git commit -m "feat(v2): persist Stripe checkout details"
```

---

### Task 2: Derive display details from confirmed reservation evidence

**Files:**
- Modify: `v2_application/outcome_projector.py`
- Modify: `tests/test_v2_outcome_projector.py`

**Interfaces:**
- Consumes: `PaymentDisplayDetails` and `CheckoutService` from Task 1.
- Produces: every newly projected `PaymentObligation` with non-null display details.

- [ ] **Step 1: Write failing projection tests**

Extend the single and package tests to inspect `selection_json` and require:

```python
assert lodging["display_details"] == {
    "service": "lodging",
    "public_label": expected.public_label,
    "start_date": expected.start_date.isoformat(),
    "end_date": expected.end_date.isoformat(),
    "start_time": None,
    "adults": expected.party.adults,
    "children": expected.party.children,
    "reservation_total_minor": expected_amount,
    "package_component": True,
}
```

Add the activity counterpart and assert the package flag is false for a standalone command and true for both package children. Assert no customer PII keys or values occur in serialized display details.

- [ ] **Step 2: Run RED**

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_outcome_projector.py::test_single_reservation_projects_one_obligation \
  tests/test_v2_outcome_projector.py::test_package_projects_two_unit_specific_obligations_once
```

Expected: failure because `display_details` is absent.

- [ ] **Step 3: Implement deterministic derivation**

Pass `package_component=len(members) == 2` into `_selection()`. Derive display fields only from `command.payload.components[0]`; map domain `ServiceKind` to payment `CheckoutService`. Keep `outcome.provider_reference` only in the private confirmed anchor. Bind `reservation_total_minor` to the same `_minor_units(component.total.amount)` used by the obligation.

Raise before enqueue if the private confirmed anchor lacks its provider reference or the component shape cannot form valid display details.

- [ ] **Step 4: Run GREEN and projector regressions**

```bash
venv/bin/python -m pytest -q tests/test_v2_outcome_projector.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add v2_application/outcome_projector.py tests/test_v2_outcome_projector.py
git commit -m "feat(v2): project payment display evidence"
```

---

### Task 3: Format and submit rich Stripe Product copy

**Files:**
- Create: `v2_adapters/stripe_checkout.py`
- Modify: `v2_adapters/stripe.py`
- Modify: `tests/test_v2_stripe_test_transport.py`
- Create: `tests/test_v2_stripe_checkout.py`

**Interfaces:**
- Consumes: `StripeLinkRequest.display_details`, `amount_minor`, `currency`, and `payment_percentage`.
- Produces: `StripeProductPresentation(name: str, description: str, details_sha256: str)`.
- Produces: `stripe_product_presentation(request: StripeLinkRequest) -> StripeProductPresentation`.

- [ ] **Step 1: Write failing formatter tests**

Add table-driven tests with exact expected strings for:

```python
StripeProductPresentation(
    name="Pacote / Package — Roteiro dos 4Ps — Sinal / Deposit 20%",
    description=(
        "Passeio / Tour • 03/12/2026 às / at 08:30 • 1 adulto / adult • "
        "Total R$ 334,95 • "
        "Pagar agora / Pay now R$ 66,99 (20%) • "
        "Saldo restante / Remaining balance R$ 267,96"
    ),
)
```

and lodging:

```text
Suíte Casal — Pagamento integral / Full payment
Hospedagem / Accommodation • Check-in 02/12/2026 • Check-out 04/12/2026 • 1 hóspede / guest • Total R$ 300,00 • Pagar agora / Pay now R$ 300,00 (100%)
```

Also test:

- multiple adults and children;
- missing activity time;
- deterministic name truncation to 120 characters;
- description longer than 500 fails closed;
- legacy request without details fails before formatting.

- [ ] **Step 2: Run formatter RED**

```bash
venv/bin/python -m pytest -q tests/test_v2_stripe_checkout.py
```

Expected: collection/import failure because `v2_adapters.stripe_checkout` does not exist.

- [ ] **Step 3: Implement the pure formatter**

Use only `datetime.date`, integer minor units, deterministic `Decimal`/string rendering, and closed mappings:

```python
_SERVICE_LABEL = {
    CheckoutService.LODGING: "Hospedagem / Accommodation",
    CheckoutService.ACTIVITY: "Passeio / Tour",
}
```

Compute `details_sha256` over canonical UTF-8 JSON containing all display fields, payable amount, currency, and percentage. Do not include `payment_id`, subscriber ID, or customer data.

- [ ] **Step 4: Write failing transport wire tests**

Update the closed-form test to require Product form fields:

```python
{
    "name": [presentation.name],
    "description": [presentation.description],
    "metadata[payment_id_sha256]": [...],
    "metadata[economic_version]": ["2"],
    "metadata[display_details_sha256]": [presentation.details_sha256],
}
```

Require the Product mock response to echo exact `name`, `description`, and metadata. Require Payment Link POST and GET metadata to carry the display hash.

Add mismatch parameterization for Product name, Product description, and Product display hash. Each case must stop after `/v1/products` and raise `RuntimeError`.

- [ ] **Step 5: Run transport RED**

```bash
venv/bin/python -m pytest -q tests/test_v2_stripe_test_transport.py
```

Expected: failures because the Product form and verification are still generic.

- [ ] **Step 6: Implement transport and adapter propagation**

`StripeLinkAdapter.create_link()` must reject `obligation.display_details is None` before invoking the transport and copy details into `StripeLinkRequest`.

`StripeTestHTTPTransport.__call__()` must create the presentation once, send it to Product, verify test mode plus exact Product fields/hash, include the hash in Payment Link metadata, and preserve current Product/Price/Link idempotency keys.

- [ ] **Step 7: Run GREEN**

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_stripe_checkout.py \
  tests/test_v2_stripe_test_transport.py \
  tests/test_v2_payment_initiation.py
```

Expected: all tests pass and all HTTP calls use `httpx.MockTransport`.

- [ ] **Step 8: Commit**

```bash
git add v2_adapters/stripe_checkout.py v2_adapters/stripe.py \
  tests/test_v2_stripe_checkout.py tests/test_v2_stripe_test_transport.py \
  tests/test_v2_payment_initiation.py
git commit -m "feat(v2): detail Stripe checkout products"
```

---

### Task 4: Cross-flow verification and build artifact

**Files:**
- Modify only if a causal regression requires it: focused files from Tasks 1–3.
- No deployment or environment files.

**Interfaces:**
- Verifies the complete command → payment selection → Stripe form path.

- [ ] **Step 1: Run focused workflow suite**

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_outcome_projector.py \
  tests/test_v2_payment_initiation.py \
  tests/test_v2_stripe_checkout.py \
  tests/test_v2_stripe_test_transport.py \
  tests/test_phase6_payment_worker.py \
  tests/test_v2_completion_projector.py
```

Expected: all pass.

- [ ] **Step 2: Run static checks**

```bash
git diff --check
python -m py_compile \
  v2_contracts/payments.py \
  v2_application/payments.py \
  v2_application/outcome_projector.py \
  v2_adapters/stripe_checkout.py \
  v2_adapters/stripe.py
```

Expected: exit 0.

- [ ] **Step 3: Run global suite in the clean environment used by this branch**

```bash
PY="$(pwd)/venv/bin/python"
env -i HOME="$HOME" PATH="$PATH" PYTHONPATH="$(pwd)" "$PY" -m pytest -q
```

Expected classification: feature tests pass; compare any failures against the known inherited Phase 7/8 baseline instead of masking them.

- [ ] **Step 4: Commit any verified causal regression fix explicitly**

```bash
git add v2_contracts/payments.py v2_application/payments.py \
  v2_application/outcome_projector.py v2_adapters/stripe_checkout.py \
  v2_adapters/stripe.py tests/test_v2_payment_initiation.py \
  tests/test_v2_outcome_projector.py tests/test_v2_stripe_checkout.py \
  tests/test_v2_stripe_test_transport.py
git commit -m "fix(v2): preserve detailed checkout contracts"
```

Skip this commit when no additional changes exist.

- [ ] **Step 5: Build and attest the image**

```bash
SHA="$(git rev-parse HEAD)"
TAG="${SHA:0:7}"
docker build -f Dockerfile.v2 -t "local/agente-v2-semantic:$TAG" \
  --build-arg VCS_REF="$SHA" \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --build-arg VERSION=0.8.0 .
docker image inspect "local/agente-v2-semantic:$TAG" \
  --format '{{.Id}}|{{index .Config.Labels "org.opencontainers.image.revision"}}|{{.Config.User}}'
```

Expected: build exit 0; revision label equals `SHA`; runtime user is non-root.

- [ ] **Step 6: Final scope and safety check**

```bash
git status --short --branch
docker ps --filter 'name=maya-v2-live' --format '{{.Names}}|{{.Status}}'
```

Expected: clean worktree and no new live-effect container. Do not create a real Stripe link, reservation, deployment, or push.
