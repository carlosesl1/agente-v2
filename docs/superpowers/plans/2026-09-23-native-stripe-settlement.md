# Native Stripe receipt and settlement implementation plan

**Goal:** close the verified Stripe TEST → V2 ingress → durable evidence → provider settlement boundary without redirecting GA payments or replaying the cancelled booking's payment.

**Architecture:** authenticate account-specific native Stripe signatures; use GET-only Stripe correlation against issued PaymentLink/Price receipts and confirmed execution owners. Materialize the existing followup workflow from authenticated paid checkout acceptance, using the actual canonical payment_intent.succeeded event as the global claim. Reuse the existing permanent settlement fence and completion context. No new ledger or conversational classifier.

**Execution:** inline, no subagents, in the current authenticated worktree at base e13452c609ed1889387564c2ccb568e2625a3b8e. Preserve GA/Ops/V3/legacy and active databases. Existing paid cancelled reservation is observational only.

## 1. Native ingress
- Causal RED in `tests/test_v2_native_stripe.py`: signed native checkout POST currently unavailable. Add real temporary stores, issued receipts, synthetic Stripe HTTP; test wrong signature/account/mode/amount, exact link ownership, paid vs unpaid, canonical PI event and reopen/replay.
- Implement `v2_application/native_stripe.py` for native verification/correlation and short-lived store acceptance; account-specific routes in `v2_host/api_main.py`, independently opt-in role-scoped settings. Existing normalized Pix/Wise paths unchanged.
- Financial summary/confirmation comes from actual paid hosted Checkout, not invented conversation consent. Preserve provider amount BRL and validate paid currency against the authenticated Price option.

## 2. Settlement
- Causal tests in `tests/test_v2_stripe_settlement.py`: default worker currently closed. Exercise fresh provider status, cancelled/no-effect, accepted one POST, timeout/reopen no second POST, exact payment method and amount.
- Implement a Stripe-only settlement port using existing `PaymentSettlementWorker` and current provider contracts; bind command, anchor and active lease at physical write. Cloudbeds Cartãodecrédito; Bókun no Pix discount. No uncertain retry, no reactivation.
- Route terminal failures through existing handoff, and settlement facts to existing Maya completion context, preserving authored replies.

## 3. Qualification and isolated deployment
- Run focal tests after each causal round, then full clean-environment suite and `python3 scripts/check_fasttrack_boundaries.py`; inspect diff and exact image source.
- Immutable candidate/source release and separate Stripe TEST endpoint, never redirect the old GA paths. Restrict ingress to the isolated state's issued charges. Preserve rollback and `/readyz`; verify runtime authority before and after.
- Real inbound signature/delivery and full new active-booking payment are separate gates. Never count synthetic HTTP tests or the old cancelled payment as completed live settlement. If a fresh payer action is needed, give Carlos the new verified TEST link only once the lane is ready.
