# Phone-Derived Customer Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select PT-BR for Brazilian phone numbers and English for every other international phone, then use that single language consistently in Stripe checkout copy and V2 automatic reservation/payment-link messages.

**Architecture:** Normalize private ManyChat phone data to E.164 at the profile adapter, derive a closed `CustomerLanguage` from the canonical phone, and persist only that enum in payment display/results. Render all checkout and completion copy from the immutable language; never carry the phone into Stripe or public outbox data.

**Tech Stack:** Python 3.12, frozen dataclasses/enums, SQLite/AES-GCM payment store, pytest, httpx fake transports, Docker.

## Global Constraints

- `+55...` and canonicalized `55...` select `CustomerLanguage.PT_BR`; every other valid international DDI selects `CustomerLanguage.EN`.
- Digits-only local Brazilian phone is prefixed with `+55` only when the private ManyChat country is `BR`.
- Malformed or ambiguous phone data fails before reservation/payment/message effects.
- Free-form Maya replies remain outside deterministic DDI routing.
- No raw phone in Stripe Product data, metadata, result objects, public outbox text, logs, or reports.
- Existing materialized Stripe links remain unchanged.
- Legacy payment selections/results remain decodable but cannot create a new Stripe effect/message without a known language.
- Product names remain at most 120 characters; descriptions remain at most 500.
- No live provider, Stripe, ManyChat, deploy, promotion, or push actions.

---

### Task 1: Canonical phone normalization and closed language selector

**Files:**
- Create: `v2_contracts/localization.py`
- Modify: `v2_adapters/manychat_profile.py`
- Modify: `v2_contracts/__init__.py`
- Test: `tests/test_v2_profile_and_model_grammar.py`
- Create: `tests/test_v2_customer_language.py`

**Interfaces:**
- Produces: `CustomerLanguage(str, Enum)` with `PT_BR="pt-BR"`, `EN="en"`.
- Produces: `customer_language_from_phone(phone_e164: str) -> CustomerLanguage`.
- Produces internally: `_canonical_manychat_phone(value: object, country_code: str | None) -> str | None`.

- [ ] **Step 1: Write RED selector tests**

```python
@pytest.mark.parametrize(
    ("phone", "expected"),
    (
        ("+5575999999999", CustomerLanguage.PT_BR),
        ("+12025550123", CustomerLanguage.EN),
        ("+447700900123", CustomerLanguage.EN),
        ("+34612345678", CustomerLanguage.EN),
    ),
)
def test_customer_language_comes_only_from_canonical_phone(phone, expected):
    assert customer_language_from_phone(phone) is expected

@pytest.mark.parametrize("phone", ("5575999999999", "", "+05512345678", "+55 7599999999"))
def test_customer_language_rejects_non_e164(phone):
    with pytest.raises(ValueError, match="E.164"):
        customer_language_from_phone(phone)
```

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_customer_language.py
```

Expected: collection/import failure because `v2_contracts.localization` does not exist.

- [ ] **Step 2: Implement the minimal selector**

```python
class CustomerLanguage(str, Enum):
    PT_BR = "pt-BR"
    EN = "en"

_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")

def customer_language_from_phone(phone_e164: str) -> CustomerLanguage:
    if type(phone_e164) is not str or _E164_RE.fullmatch(phone_e164) is None:
        raise ValueError("phone_e164 must be canonical E.164")
    return CustomerLanguage.PT_BR if phone_e164.startswith("+55") else CustomerLanguage.EN
```

Export both names through `v2_contracts.localization.__all__` and `v2_contracts.__init__`.

- [ ] **Step 3: Verify selector GREEN**

Run the selector file. Expected: all pass.

- [ ] **Step 4: Write RED profile-normalization tests**

Extend `test_v2_profile_and_model_grammar.py` with:

```python
@pytest.mark.parametrize(
    ("raw_phone", "country", "expected"),
    (
        ("5575999999999", "BR", "+5575999999999"),
        ("75999999999", "BR", "+5575999999999"),
        ("447700900123", "GB", "+447700900123"),
        ("34612345678", "ES", "+34612345678"),
        ("12025550123", "US", "+12025550123"),
    ),
)
def test_profile_adapter_canonicalizes_manychat_phone_without_plus(
    raw_phone, country, expected
):
    payload = {**_complete_payload(), "phone_e164": raw_phone, "country_code": country}
    binding = ManyChatProfileAdapter(
        transport=ProfileTransport(payload), ttl=timedelta(minutes=5)
    ).read("manychat:subscriber-001", now=NOW)
    assert binding.phone_e164 == expected
```

Move the old digits-only `11999999999` rejection into the BR normalization matrix. Add invalid cases with spaces, punctuation, letters, leading-zero DDI, and overlength.

Run the new selectors. Expected: FAIL because digits-only phone is rejected by `PrivateCustomerBinding`.

- [ ] **Step 5: Implement private adapter normalization**

In `v2_adapters/manychat_profile.py`, normalize country first and phone second:

```python
_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_DIGITS_RE = re.compile(r"^[0-9]{8,15}$")

def _canonical_manychat_phone(value: object, country_code: str | None) -> str | None:
    phone = _private_value(value, "phone_e164")
    if phone is None:
        return None
    if phone.startswith("+"):
        canonical = phone
    elif _DIGITS_RE.fullmatch(phone) is not None:
        digits = phone
        if country_code == "BR" and not digits.startswith("55"):
            digits = "55" + digits
        canonical = "+" + digits
    else:
        raise ManyChatProfilePayloadError("phone_e164 is not canonical")
    if _E164_RE.fullmatch(canonical) is None:
        raise ManyChatProfilePayloadError("phone_e164 is not canonical E.164")
    return canonical
```

Ensure `content_hash` binds the canonical E.164 value, not the raw provider string.

- [ ] **Step 6: Verify profile normalization GREEN and commit**

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_customer_language.py tests/test_v2_profile_and_model_grammar.py tests/test_v2_effective_customer_profile.py
```

Expected: all pass.

Commit:

```bash
git add v2_contracts/localization.py v2_contracts/__init__.py v2_adapters/manychat_profile.py tests/test_v2_customer_language.py tests/test_v2_profile_and_model_grammar.py
git commit -m "feat(v2): derive customer language from phone"
```

---

### Task 2: Persist language through payment selection and Stripe result

**Files:**
- Modify: `v2_contracts/payments.py`
- Modify: `v2_application/payments.py`
- Modify: `v2_application/outcome_projector.py`
- Modify: `v2_adapters/stripe.py`
- Modify: `tests/test_v2_payment_initiation.py`
- Modify: `tests/test_v2_outcome_projector.py`

**Interfaces:**
- Extends: `PaymentDisplayDetails.customer_language: CustomerLanguage | None = None`.
- Extends: `StripePaymentLink.customer_language: CustomerLanguage | None = None`.
- New projection always provides exact `CustomerLanguage`; `None` exists only for legacy decode.

- [ ] **Step 1: Write RED contract/serialization tests**

Update canonical fixtures to include `customer_language=CustomerLanguage.PT_BR`. Require serialized display details to contain `"customer_language": "pt-BR"`; require round-trip equality and different `_initiation_id` for PT-BR vs EN. Construct old display JSON without the field and assert decode returns `customer_language is None`.

Add encrypted Stripe-result round-trip assertions through the existing initiation worker: completed offer preserves `CustomerLanguage.PT_BR`. Add a direct legacy `_offer_from_bytes` case without `customer_language` returning `None`.

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_payment_initiation.py
```

Expected: FAIL because payment contracts have no language.

- [ ] **Step 2: Add legacy-compatible fields and serializers**

In `payments.py`, exact-validate `CustomerLanguage | None` on display details and Stripe links. In `v2_application/payments.py`:

- include `customer_language` in new display-detail bytes;
- accept exactly the new field set or the historical field set;
- decode historical fields as `None`;
- include `customer_language` in new Stripe result bytes;
- accept exactly the new Stripe-result set or historical set;
- decode historical Stripe result as `None`.

Never accept unknown extra fields.

- [ ] **Step 3: Verify payment persistence GREEN**

Run the payment initiation file. Expected: all pass.

- [ ] **Step 4: Write RED projection tests**

Make `_package_command(phone_e164=...)` explicit. Assert BR commands project `PT_BR`, US/UK commands project `EN`, package sibling obligations use the same language, and the persisted selection contains no phone text.

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_outcome_projector.py
```

Expected: FAIL because projector does not set language.

- [ ] **Step 5: Derive language once in the projector**

Use:

```python
customer_language=customer_language_from_phone(
    command.payload.customer.phone_e164
)
```

when creating `PaymentDisplayDetails`. No country fallback is allowed here.

- [ ] **Step 6: Bind language through Stripe adapter/result**

Before any transport call, require non-`None` display language. Return `StripePaymentLink(customer_language=details.customer_language, ...)`. Include the language in the receipt hash payload so result identity binds it.

Add a test that `replace(HOSTEL_DETAILS, customer_language=None)` fails before fake transport, and tests that PT/EN language survives request/result.

- [ ] **Step 7: Run focused payment/projector tests and commit**

```bash
venv/bin/python -m pytest -q tests/test_v2_payment_initiation.py tests/test_v2_outcome_projector.py
```

Expected: all pass.

Commit explicit paths with:

```bash
git commit -m "feat(v2): persist phone-derived payment language"
```

---

### Task 3: Render single-language Stripe checkout copy

**Files:**
- Modify: `v2_adapters/stripe_checkout.py`
- Modify: `tests/test_v2_stripe_checkout.py`
- Modify: `tests/test_v2_stripe_test_transport.py`

**Interfaces:**
- Consumes: `request.display_details.customer_language`.
- Produces: exact PT-BR or English `StripeProductPresentation`.

- [ ] **Step 1: Rewrite checkout assertions first (RED)**

PT-BR expected activity:

```text
Pacote — Roteiro dos 4Ps — Sinal 20%
Passeio • 03/12/2026 às 08:30 • 1 adulto • Total R$ 334,95 • Pagar agora R$ 66,99 (20%) • Saldo restante R$ 267,96
```

English expected activity:

```text
Package — Roteiro dos 4Ps — Deposit 20%
Tour • 03 Dec 2026 at 08:30 • 1 adult • Total R$334.95 • Pay now R$66.99 (20%) • Remaining balance R$267.96
```

Add equivalent lodging assertions and parameterized singular/plural/children/no-time cases. Assert no mixed-language markers (`/`, `Pagar` in EN, `Pay now` in PT). Assert missing language fails before any Product POST.

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_stripe_checkout.py tests/test_v2_stripe_test_transport.py
```

Expected: copy assertions fail because renderer is bilingual.

- [ ] **Step 2: Implement language-owned copy tables**

Use closed dictionaries keyed by `CustomerLanguage` for service labels, package prefix, full/deposit suffixes, payment labels, balance labels, and party nouns. Implement fixed English month abbreviations without process locale:

```python
_EN_MONTH = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
```

PT currency: `R$ 1.234,56`; EN currency: `R$1,234.56`. Preserve existing calculation, bounds, service-unit validation, and fail-closed behavior.

- [ ] **Step 3: Bind language in presentation hash**

Add `"customer_language": details.customer_language.value` to canonical display details. The exact rendered Product name/description remain hash inputs. Update hash test accordingly.

- [ ] **Step 4: Verify Stripe tests GREEN and commit**

Run both Stripe files. Expected: all pass.

Commit:

```bash
git add v2_adapters/stripe_checkout.py tests/test_v2_stripe_checkout.py tests/test_v2_stripe_test_transport.py
git commit -m "feat(v2): localize Stripe checkout by phone"
```

---

### Task 4: Localize automatic confirmation and Stripe-link messages

**Files:**
- Modify: `v2_application/completion_projector.py`
- Modify: `tests/test_v2_completion_projector.py`
- Modify: `tests/v2_payment_display_fixtures.py`
- Modify: `tests/v2_signed_qualification.py`
- Modify: `tests/test_v2_e2e.py`

**Interfaces:**
- `_confirmation_text(commands) -> str` derives exactly one language from each command phone and rejects disagreement.
- `_payment_text(unit, url, customer_language) -> str` requires exact language.

- [ ] **Step 1: Write RED completion-copy tests**

Assert all PT and EN reservation group shapes and both payment-link units. Add an E2E BR customer yielding only PT completion chunks and a foreign customer yielding only English chunks. Add a legacy `StripePaymentLink(customer_language=None)` case that raises before link text is placed in the public outbox.

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_completion_projector.py tests/test_v2_e2e.py
```

Expected: English assertions fail and `_payment_text` lacks language.

- [ ] **Step 2: Implement confirmation localization**

Derive languages from every command phone. Require exactly one language for a group. Return closed copy per service set:

```python
_PT_CONFIRMATION = {
    frozenset((ServiceKind.LODGING,)): "Sua hospedagem foi confirmada.",
    frozenset((ServiceKind.ACTIVITY,)): "Seu passeio foi confirmado.",
    frozenset((ServiceKind.LODGING, ServiceKind.ACTIVITY)): "Sua hospedagem e seu passeio foram confirmados.",
}
_EN_CONFIRMATION = {
    frozenset((ServiceKind.LODGING,)): "Your accommodation has been confirmed.",
    frozenset((ServiceKind.ACTIVITY,)): "Your tour has been confirmed.",
    frozenset((ServiceKind.LODGING, ServiceKind.ACTIVITY)): "Your accommodation and tour have been confirmed.",
}
```

- [ ] **Step 3: Implement payment-link localization**

PT:

- hostel: `Link de pagamento da hospedagem: {url}`
- agency: `Link de pagamento do passeio: {url}`

EN:

- hostel: `Accommodation payment link: {url}`
- agency: `Tour payment link: {url}`

Reject `None` language before constructing/enqueuing link text.

- [ ] **Step 4: Update qualification fixtures explicitly**

Every `PaymentDisplayDetails` used for a new Stripe effect must declare the expected language. Do not use a global default. Update only affected assertions.

- [ ] **Step 5: Verify completion/E2E GREEN and commit**

```bash
venv/bin/python -m pytest -q tests/test_v2_completion_projector.py tests/test_v2_e2e.py tests/test_phase6_payment_worker.py
```

Expected: all pass.

Commit:

```bash
git commit -m "feat(v2): localize automatic payment messages"
```

---

### Task 5: Blast-radius verification and offline image

**Files:**
- Modify only if a causal regression requires it: affected test fixtures that create new `PaymentDisplayDetails`.
- Create outside Git: `/home/ubuntu/workspace/phone-language-<short-sha>/RESULTADO.md`.

- [ ] **Step 1: Find every constructor and eliminate accidental unknown-language effects**

Run a source scan for `PaymentDisplayDetails(` and classify each constructor as:

- new effect fixture → explicit `CustomerLanguage`;
- legacy compatibility fixture → explicit `None` or missing serialized field.

Do not introduce a PT default.

- [ ] **Step 2: Run focused regression set**

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_customer_language.py \
  tests/test_v2_profile_and_model_grammar.py \
  tests/test_v2_effective_customer_profile.py \
  tests/test_v2_outcome_projector.py \
  tests/test_v2_payment_initiation.py \
  tests/test_v2_stripe_checkout.py \
  tests/test_v2_stripe_test_transport.py \
  tests/test_v2_completion_projector.py \
  tests/test_phase6_payment_worker.py \
  tests/test_v2_e2e.py
```

Expected: all pass.

- [ ] **Step 3: Run static and full clean-env validation**

```bash
git diff --check
venv/bin/python -m py_compile \
  v2_contracts/localization.py \
  v2_contracts/payments.py \
  v2_adapters/manychat_profile.py \
  v2_adapters/stripe_checkout.py \
  v2_adapters/stripe.py \
  v2_application/outcome_projector.py \
  v2_application/payments.py \
  v2_application/completion_projector.py
PY="$(pwd)/venv/bin/python"
env -i HOME="$HOME" PATH="$PATH" PYTHONPATH="$(pwd)" \
  HERMES_LEADS_AGENT_CONFIG_PATH="/home/ubuntu/chapada-leads-hermes/config/leads_agent.yaml" \
  "$PY" -m pytest -q
```

Expected: all feature tests pass; only the seven already-known Phase 7/8 documentary/packaging failures may remain.

- [ ] **Step 4: Freeze, commit, and build once**

After all code is committed and the worktree is clean:

```bash
SHA="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short=7 HEAD)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker build --file Dockerfile.v2 \
  --build-arg VCS_REF="$SHA" \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  --build-arg VERSION="0.8.0-phone-language" \
  --tag "local/agente-v2-phone-language:$SHORT" .
```

Expected: build and compileall exit 0.

- [ ] **Step 5: Run network-disabled image previews**

Inside the image with `--network none`, render one PT-BR and one English Stripe Product plus automatic messages. Assert no bilingual slash framing and no raw phone in output.

- [ ] **Step 6: Verify operational non-effects and report**

Verify current canary container image/health is unchanged, Git has not been pushed, and no provider/ManyChat/Stripe command ran. Write sanitized `RESULTADO.md` outside Git with commit, image ID, exact PT/EN previews, focused/global test counts, inherited failure note, and unchanged canary evidence.
