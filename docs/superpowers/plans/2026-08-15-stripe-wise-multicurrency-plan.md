# Stripe Wise Multi-Currency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each V2 Stripe Payment Link offer BRL, USD, and EUR using current Wise rates and the V1 `- R$0.20` adjustment.

**Architecture:** Keep the change inside the existing Stripe transport. Add one small read-only Wise rate client in `v2_adapters/stripe.py`, wire two worker settings through the existing production factory, and add USD/EUR `currency_options` plus audit metadata to the existing Stripe calls. Do not add persistence, cache, workflows, contracts, or new packages.

**Tech Stack:** Python 3.12, `httpx`, `Decimal`, pytest, Docker Compose.

## Global Constraints

- BRL remains the canonical provider amount and default Stripe currency.
- Formula: `BRL per foreign unit = (1 / Wise BRL→foreign rate) - 0.20`.
- Round Stripe minor units with `ROUND_HALF_UP`.
- Fetch fresh Wise rates for each link creation and fail closed when unavailable.
- Never log or return the Wise token or raw provider payload.
- Keep Stripe quantity fixed at one and Stripe environment in test mode.

---

### Task 1: Wise rates and Stripe multi-currency form

**Files:**
- Modify: `tests/test_v2_stripe_test_transport.py`
- Modify: `v2_adapters/stripe.py`

**Interfaces:**
- Consumes: `StripeLinkRequest` with canonical BRL `amount_minor`.
- Produces: `WiseBRLRates` and a Price form containing BRL base plus USD/EUR currency options.

- [ ] Add a failing transport test with one mock client handling Wise GETs and Stripe POSTs; assert exact routes, formula, rounded values, metadata, and no Stripe POST before both rates validate.
- [ ] Run `PYTHONPATH=$PWD uv run --extra runtime --extra dev pytest -q tests/test_v2_stripe_test_transport.py` and confirm failure because the Wise dependency and currency options are absent.
- [ ] Implement `WiseExchangeRateReader` in `v2_adapters/stripe.py` using the existing `httpx.Client`, canonical `https://api.wise.com`, bearer auth, exact BRL→USD/EUR routes, positive `Decimal` validation, and the fixed `Decimal("-0.20")` adjustment.
- [ ] Inject the reader into `StripeTestHTTPTransport`; obtain the quote before the Product POST; add `currency_options[usd][unit_amount]` and `currency_options[eur][unit_amount]`; add canonical amount/rate metadata to the Payment Link and journal bindings.
- [ ] Run the focal test and confirm it passes.

### Task 2: Worker configuration and composition

**Files:**
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_production_composition.py`
- Modify: `v2_host/settings.py`
- Modify: `v2_host/production.py`
- Modify: `compose.v2.yaml`

**Interfaces:**
- Consumes: `V2_WISE_API_TOKEN`, `V2_WISE_BASE_URL`.
- Produces: configured `WiseExchangeRateReader` passed to `StripeTestHTTPTransport`.

- [ ] Add failing tests proving worker Stripe enablement requires a Wise token, API role cannot retain it, and production composition wires the reader.
- [ ] Run the focal settings/composition tests and confirm the expected failures.
- [ ] Add `wise_api_token` and `wise_base_url` to `V2Settings`, role scoping, canonical URL validation, and `from_env`.
- [ ] Construct `WiseExchangeRateReader` in `build_payment_worker` and pass it to `StripeTestHTTPTransport`.
- [ ] Add the two variables only to the worker environment in `compose.v2.yaml`.
- [ ] Run the focal tests and confirm they pass.

### Task 3: Verification and deployment

**Files:**
- Modify deployment artifacts under `/home/ubuntu/workspace/agente-v2-canary-deploy` by copying the existing active compose/env and changing only immutable image identity plus the Wise variable.

- [ ] Run Stripe, settings, role-scope, production-composition, payment-initiation, webhook, and reconciliation tests.
- [ ] Run full applicable pytest, Ruff 0.15.10, compileall, `git diff --check`, and fast-track boundaries.
- [ ] Commit implementation, push branch, build immutable image, publish to local registry, and capture digest.
- [ ] Copy the operational Wise token from the V1 container directly into the new deployment env without printing it.
- [ ] Promote with the current digest as rollback; verify all containers healthy, public readiness, immutable identity, and zero recent worker errors.
- [ ] Run a no-network mocked-provider smoke inside the image proving BRL/USD/EUR payload generation without creating a real Stripe link.
