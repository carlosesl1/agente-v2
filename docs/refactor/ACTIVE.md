# Active E2E — new agency booking/link awaiting human TEST checkout

- User explicitly authorized one new 4Ps booking for 2026-11-25 08:30, two adults, BRL 730.80 total / BRL 146.16 agency TEST deposit, conditional on unchanged fresh terms. No automatic checkout or historical lodging/payment changes.
- After zero-effect expired-summary attempts, one fresh summary and immediate natural confirmation in a bounded window produced one booking command/dispatch and one completed payment initiation/dispatch. Native Bókun booking `104856042` is **RESERVED / NOT_PAID**, paid zero, fee-inclusive due BRL 730.80. Stripe agency TEST link is active, amount BRL 146.16, zero paid sessions observed.
- Native WhatsApp CTA received; ManyChat 302 reaches the same authenticated new Payment Link with `mcp_token` tracking, Stripe landing 200. Verbatim URL equality is false; account, price and new reservation-anchor metadata were independently verified by authenticated GET.
- Same functional runtime `5fccceedc318510e4b3e443a9f1a98742417ebf8`; current issuing state retained and effect/send gates closed, contact paused. Old commercial rows byte-for-byte preserved; zero new settlement commands/evidence/ledger rows; GA/Ops unchanged. Never roll back to an empty/pre-booking state while this checkout is payable.
- NEXT: **AWAITING_HUMAN_TEST_PAYMENT**. Only the new agency button is eligible; revalidate a delayed/expired booking before payment. After human TEST checkout, prove genuine Stripe receipt/correlation, one provider settlement, native read-back and final WhatsApp response under revalidated authority. Not financial E2E PASS.
- Evidence: `/home/ubuntu/workspace/v2-renewal-e2e-727d3625/journey-20260924T220219Z/booking-fresh/RESULTADO.md`, `handoff-proof.json`, `provider-final-audit.private.json`, `cta-verification.json`, `historical-row-preservation.json`, and executed closure receipt.

## Previous stage — context correction and summary gate

# Active maintenance — persisted commercial context published TEST CLOSED

- Carlos requested continuation of the real WhatsApp E2E. Closed payment capability initially blocked summary materialization; after correcting the scoped test window, real-model delta frames exposed a runtime guard that ignored committed commercial facts.
- Published functional revision `5fccceedc318510e4b3e443a9f1a98742417ebf8`, authenticated local/remote ref `refs/heads/production/test-persisted-context-5fcccee`. Read and verify the canonical runtime authority for component identities; this documentary HEAD is not another runtime release.
- Qualification: 2510 tests / 2958 subtests; 155 tests against image modules. Real Maya + executor + isolated SQLite with controlled provider/profile/status facts reached `awaiting_confirmation` without commands. The earlier archive-only full-suite failure lacked Git history and is preserved; the complete same-code rerun in the clean checkout passed.
- Real WhatsApp after publication: fresh 4Ps quote for 25/11/2026 08:30, two adults, BRL 730.80 total / BRL 146.16 Stripe TEST deposit; runtime-owned summary reached `awaiting_confirmation` and its confirmation question was received on the client. No booking confirmation was sent.
- Final safety: runtime authority OK, TEST gates closed, contact paused and client sends disabled. Same state/mounts and rollback; 22 commercial/financial tables unchanged over the journey; only message dispatch/outbox changed. GA/Ops unchanged. No new reservation, checkout, settlement or refund.
- Evidence: `/home/ubuntu/workspace/v2-renewal-e2e-727d3625/journey-20260924T220219Z/RESULTADO.md`, `verification-final.json`, `qualification/qualification-final.json`, and `publication/`.
- NEXT: obtain specific itinerary/amount authorization before a new real Bókun reservation and agency-bound Stripe TEST checkout. Preserve cancelled lodging and historical payments. Checkout remains human; after it, verify genuine webhook, correlation/idempotency, provider settlement and WhatsApp response. Revalidate expired commercial evidence; never replay old POSTs, edit live SQLite, reset state or promote GA implicitly.

## Previous maintenance — history/clock publication

# Active maintenance — history/clock candidate published TEST CLOSED

- Completed the exact scope Carlos selected: clock correction, joint qualification and TEST-only CLOSED publication, without bookings, charges or customer messages. Read the canonical authority and run its verifier for active component identities; this documentary HEAD is not a new runtime release.
- Published functional revision `a8d2e913c7790bf9421a26cb5dfabe111800c324`, authenticated ref `refs/heads/production/test-history-clock-a8d2e91`. Full qualification: 2499 tests / 2958 subtests; 210 tests against image modules; three controlled real-model scenarios, zero executable tools (not financial/channel/handoff E2E).
- Live verification: authority OK, API/router readyz passed, fresh healthy worker, history configured while payment methods and all effect gates remain closed. Same state mounts, 24 financial/delivery tables unchanged, 10 SQLite backups and closed predecessor rollback retained; GA/Ops unchanged. WhatsApp WORKING; contact paused and client sends disabled.
- Evidence: `/home/ubuntu/workspace/v2-renewal-e2e-727d3625/resume-20260924T200802Z/publication-joint/PUBLICACAO.md` and `verification-final.json`.
- NEXT: only a separately scoped conversational/transactional window; confirm current component/amount before new effects. Do not repeat technical publication, reopen gates, replay old POSTs, reuse historical checkout authority, edit active SQLite, reset state or promote GA implicitly.

## Previous maintenance — authorized joint qualification and publication

# Active maintenance — history visibility and stable protocol-repair snapshot

- Carlos explicitly selected: “Corrigir o relógio, requalificar e publicar somente no TEST fechado, sem reservas ou cobranças”. Authorized scope is the existing history fix plus stable request context during a bounded protocol repair, complete qualification, then TEST-only CLOSED publication with the same retained state. No channel messages, booking/payment/settlement/refund effects, GA/Ops/V3 changes or active SQLite edits.
- The repair now reuses the original serialized request/messages/clock, changing only the existing protocol-repair instructions. A new model turn still samples a fresh clock. Existing schema, authorship, transcript and effect assertions remain intact.
- Causal RED: advancing seconds and Bahia midnight failed for all six legacy-schema repair paths; separate-turn clock refresh passed. Focused GREEN: 107 tests. Final immutable source/image qualification must succeed before publication; preserve all earlier failed/partial runs as evidence.
- Owners: `v2_host.settings`, `v2_host.composition`, `v2_adapters.hermes_model`; tests add causal witnesses without weakening existing assertions. No new controller, semantic rules or text-based routing.
- Evidence root: `/home/ubuntu/workspace/v2-renewal-e2e-727d3625/resume-20260924T200802Z/`.
- NEXT: freeze and qualify the joint candidate, authenticate its exact source/image, publish only CLOSED TEST with rollback and before/after state proofs, then report the verified result. Financial/conversational resumption remains a separate gate after this publication scope.

## Previous maintenance — history correction before clock authorization

# Active maintenance — payment history must survive closed effect gates

- Current instruction: Carlos reported WhatsApp connected and asked to continue. The connection/identity and prior message were reconciled without replay. Two new summary-only requests reached Maya and received replies, but both were refused before a new provider read. No new booking, checkout, settlement or refund was produced; TEST was closed after each bounded attempt.
- Proven cause: `V2Settings.from_env` erased the payment-result key when emission gates were closed, and `V2Container.open` omitted the history owner. `component_renewal_allowed` correctly rejected an unavailable payment history. Do not open emission gates or weaken that predicate to work around missing history.
- Correction scope: existing settings and composition only; preserve a configured worker history key/store independently of effect workers. API isolation, absent-key fail-closed behavior, component guards, model authorship and payment capabilities remain unchanged. No schema migration, new semantic rule or direct edits to active databases.
- Evidence: `/home/ubuntu/workspace/v2-renewal-e2e-727d3625/resume-20260924T200802Z/`. Causal RED and 112 focused/regression passes; an isolated SQLite-backup proof with real provider GET observations preserves 2 payment initiations, 6 step receipts and 2 reconciliations, permits agency renewal, and still rejects lodging renewal. This is not a model/channel/financial E2E pass.
- TEST remains on its declared predecessor `a4ff977`; read and verify the authority before any subsequent action. This source change is NOT deployed. GA/Ops and retained state are unchanged.
- NEXT: finish candidate qualification and separately authorize its TEST-only CLOSED publication, then resume the summary-only external conversation. New financial effects still require current scenario/component/amount confirmation; prior paid/cancelled resources are not reusable authorization.

## Previous maintenance — CLOSED renewal publication completed

# Active maintenance — CLOSED renewal publication completed

- Authorization: Carlos said “Siga para concluir” after the qualified-candidate closeout; scope executed was TEST CLOSED publication only, with the same retained state. No external transaction/channel or GA authority was inferred.
- Promotion and verification completed. Read the canonical `ACTIVE_RUNTIME.json` and run its verifier for current component refs/digests; never infer active source from this documentary HEAD. Source and exact image were already qualified; registry and remote source identity were authenticated during publication.
- Internal/public readiness, fresh healthy heartbeat, closed worker gates, unchanged state mounts, consistent SQLite backups and unchanged financial/delivery table hashes were verified. GA/Ops container identities and start times did not change; rollback remains the exact closed predecessor bundle.
- Evidence: `/home/ubuntu/workspace/v2-renewal-corrections-727d3625/publication-20260924/PUBLICACAO.md` and `verification-final.json`.
- NEXT: obtain explicit scenario authorization for a new external reservation/payment/WhatsApp journey. Preserve old cancellations, captures and uncertain effects. Do not open gates, repeat old POSTs, reset state, create bookings/checkouts, issue refunds or promote GA implicitly. Historical Cloudbeds response cause remains unproved.

## Previous maintenance — historical qualification before publication

# Active maintenance — component renewal and Cloudbeds receipt qualification

- Authorized by Carlos in this chat: “Então siga com o que falta” — remaining technical work only. The implementation uses the existing flow, component-scoped renewal, fresh provider status and new customer confirmation; no extra effect/deploy authorization is inferred.
- Base `ab58c2ff3823eb7963840294b4eeb77777f5289d`; branch `fix/v2-manychat-account-routing-727d3625`; worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
- Technical qualification complete for immutable source `a4ff97782cf4f6025268179b6c570bc4b802693d`, local ref `qualification/component-renewal-a4ff977`, image ID `sha256:62b3d190d335126c05feed8e7c7f27bf6ab2e58635b69fdd335bb9ff87df1d5a`. Full suite: 2477 tests / 2958 subtests; exact image: 251 tests / 6 subtests; four causal mutations killed; three zero-tool real-model context probes (not complete booking/channel E2E). Spec/plan are recorded in `docs/superpowers/`.
- NEXT — separate authorization: publish the qualified candidate to TEST CLOSED with retained state/rollback, then a specifically authorized new financial/channel journey. No GA promotion, old-payment replay, refund or state reset. The authority manifest and seven runtime container identities remained unchanged. Runtime remains on its previous declared source; a later documentation-only HEAD is not the candidate identity.
- Runtime authority OK; scope is isolated source/test work only. No reopening, external booking/payment/refund/channel effects, active SQLite edits, GA/Ops/V3 changes, new agent/queue/ledger or subagents.
- Owners: existing active execution context, turn executor, conversation reducer and Stripe settlement adapter. Preserve semantic authorship, component histories and durable fences. Historical incident response remains unavailable; do not infer root cause or success from aggregate balance.
- Evidence `/home/ubuntu/workspace/v2-renewal-corrections-727d3625/`.

## Previous maintenance

# Active maintenance — Stripe settlement incident corrections

- Authorized by Carlos in this chat: correct settlement and post-payment failures; operator already cancelled the Cloudbeds payment/reservation. Branch `fix/v2-manychat-account-routing-727d3625`, worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`, source base `97db2b0b2d73dc497881896c612c2fa16c8eb9e8` (only documentation differs from deployed `1341cd8`).
- Runtime recovered using the exact declared closed rollback; same image and state. Authority OK; GA/Ops unchanged. No reopening, new reservation, charge, refund, active SQLite edit, or payment replay.
- NEXT: causal tests for delayed read-only payment-effect observations and receipt chronology; separate verified Stripe capture facts from provider settlement in Maya context; preserve response evidence on ambiguous settlement. Then clean regression, offline incident-copy replay and immutable-image qualification. Real writes and E2E payment retest remain closed.
- Owners: existing followup outbox/worker, Stripe settlement adapter and execution-context projection. Maya remains the sole semantic author. No text rewrite/gates, extra ledger or subagents.
- Evidence `/home/ubuntu/workspace/v2-payment-corrections-727d3625/`. The historical Cloudbeds POST response was not retained; do not claim a proven response-shape root cause from a current zero balance. Stripe TEST capture remains succeeded/no refund as observed after cancellation.

## Previous maintenance

# Active maintenance — native Stripe receipt and settlement

- Authorized by Carlos: fix the actual paid TEST webhook ignored by the old endpoint and close native ingress/provider settlement. Base e13452c609ed1889387564c2ccb568e2625a3b8e; same worktree and branch below.
- Functional candidate `1341cd8b5a0b615752a8b78f2f9a0cf75ed2b057` qualified: native account-scoped Stripe TEST ingress, existing ledger/fence, Cloudbeds/Bókun settlement, Maya completion/handoff and actual ManyChat acceptance observation. Final isolated full suite **2446 tests / 2958 subtests passed**; immutable image **130 tests / 6 subtests passed**, network disabled. Image ID `sha256:eadaea6b86a3c70678c6dd84fcf9921a0944b5b0f685e3fc734a07cebac03f00`; source bytes verified. The predecessor opens copied financial state. These are not live payment E2E or deployment claims.
- NEXT — BLOCKED: recheck Accounts Read on the Agency TEST key after the operator enables it, then qualify/activate the exact isolated endpoints and an authorized active-reservation journey. All other required Stripe read probes passed. No endpoint provisioning, live payment replay, contact reopening or GA/Ops change; authority bytes and all seven watched container identities remain unchanged. Evidence `/home/ubuntu/workspace/v2-stripe-settlement-727d3625/`; runbook `docs/operations/native-stripe-settlement.md`. Do not repeat the cancelled historical payment to manufacture settlement success.
- No GA cutover, cancelled-reservation payment replay, active database edits, legacy/V3 changes, new financial ledger or subagents. Existing ManyChat acceptance preserved.

## Previous maintenance

# Active maintenance — exact reservation lifecycle and consolidated V2 journey

- Authorized by Carlos in session 727d36251e40: reservation confirmation is distinct from payment; nonpayment leads to automatic cancellation; Maya needs the exact provider status. Continue remaining consolidation, isolated deployment and real WhatsApp/Stripe TEST journey. ManyChat links retain latest fields per lead/unit as accepted; no immutable-link redesign.
- Base `5167668c1d554c31e89b534a12ce95f6faff31cf`; branch `fix/v2-manychat-account-routing-727d3625`; worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
- NEXT: causal tests for native reference persistence and fresh read-only lifecycle projection; minimal implementation in current execution context and provider readers; preserve execution certainty and immutable ledgers. No semantic rewriting, lexical intent triggers, subagents, new state machine or active database edits.
- Plan: `docs/superpowers/plans/2026-09-23-exact-reservation-status.md`. Evidence `/home/ubuntu/workspace/v2-exact-reservation-status-727d3625/`.
- Promotion gate: exact candidate tests/image, isolated `/readyz`, natural WhatsApp authorization, provider readback, payment TEST and postpayment. No GA cutover if any gate fails. V3, legacy and Ops remain outside scope.

## Previous maintenance

# Active maintenance — V2 account-bound ManyChat Stripe routing

- Authorized by Carlos: map each Stripe button to its account/business unit and language using the four exact ManyChat namespaces supplied in this chat. Branch `fix/v2-manychat-account-routing-727d3625`, worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`, base `11b918cbb20872bbefd038241c537a6e2573a67a`.
- Local verification complete: seven causal REDs, 113 focused tests, and full isolated suite **2393 tests / 2958 subtests passed**. Exact-image qualification follows the local commit; no deployment is authorized by these checks. Evidence `/home/ubuntu/workspace/v2-manychat-account-routing-727d3625/`.
- NEXT: when authorized, qualify all four native WhatsApp CTAs and final redirects in a fresh isolated contact test; no general promotion. Independent Bókun `RESERVED` presentation remains outside this patch.
- Contract/runbook: `docs/operations/manychat-payment-routing.md`; GET inventory verified exact namespaces and separate unit fields. No external writes during this maintenance.
- Owner: channel delivery binding to the existing authenticated Stripe offer/selection and durable lead, not Maya prose, URL guessing or lexical service/locale detection. Preserve authorship, message/release identities and uncertain-send fences.
- Scope: four flows and independent Hostel/Agency fields, default composition and configuration. No Bókun status changes, new bookings/charges, GA/Ops deployment, active-state edits, V3 or legacy. Live ManyChat discovery is GET-only; actual sends and rollout are separate gates.
- Runtime authority verified OK. ManyChat inventory independently confirms supplied namespaces and separate account link fields.

## Previous maintenance

# Active maintenance — V2 Maya text and button contract (local)

- Authorized by Carlos: resolve the two blockers found by real-model completion tests. Base `c1ca886a3950f81f355c5ccf2f34bc0138e46608`; branch `fix/v2-maya-text-button-contract-727d3625`; worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
- Verified before candidate freeze: five causal RED failures; 183 focal tests GREEN (including byte-exact Unicode to ManyChat, URL repair, restart/idempotency and tag-only terminal-failure handoff), architectural boundaries and Ruff OK. Read evidence keeps the exact prior policy/hash; only authored replies permit natural Unicode.
- NEXT: full regression and immutable-image real-model matrix. Consult the external evidence directory below for the post-commit verdict; do not infer production approval or a deploy from this maintenance note.
- Owners: authored-reply validation, model execution-context projection and primary Maya prompt. Preserve strict provider/read normalization, exact authored text, financial URLs in channel delivery, uncertain-send fences and existing handoff on terminal inbox failures. No semantic reviewer, rewriting, intent regex, new queue/database or business effects.
- No deploy, live provider/channel effects, active-state edits, V3 or legacy work. Runtime authority verified OK before work.
- Evidence: `/home/ubuntu/workspace/v2-text-button-fix-727d3625-20260923/`; prior failing matrix `/home/ubuntu/workspace/v2-button-tests-c1ca886-20260922/` remains unchanged.

## Previous maintenance

# V2 Stripe buttons via ManyChat (local)

- Authorized by Carlos in the current chat: fix the diagnosed loss of the ManyChat payment-button dispatch. Base `2482c8ae7845b4277b95529a225067f831adc913`; branch `fix/v2-manychat-payment-buttons-727d3625`; worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
- Owner: completion projection into the existing public outbox; preserve Maya-authored boundary replies and the existing ManyChat field/flow adapter. Stripe URL plus factual service label is an authenticated channel artifact, not replacement Maya prose. No new queue/database/schema, semantic classifier or model.
- DONE local: causal producer-to-HTTP RED (10 failed/4 passed), projection repair, restart/replay and uncertain-delivery gates. Final focused gate **98 passed**; full clean regression **2351 passed, 2958 subtests passed**, two upstream deprecation warnings, no exclusions. Boundary guard, changed-file Ruff and diff checks passed.
- Container qualification: **71 passed** with the exact candidate source mounted read-only over the prior immutable runtime image, `--network none`, no operational credentials. This is not a rebuilt release image or live WhatsApp validation.
- Inline review (no subagents): source-coverage recovery, pre-dispatch completion consolidation, historical release compatibility, and durable pre-I/O outbox fence including process loss/acceptance-persistence interruption. Preserve prompts, reservation/payment creation and independent unresolved incidents.
- NEXT outside this local change: publication/rebuild and separately authorized real-channel qualification; no automatic rollout. Before installing on existing state, reconcile undelivered historical Stripe offers: initiation receipts do not reveal external deactivation. Never resume the closed test mount to force delivery.
- Scope: no production deployment, real channel/provider effects, replay of archived live-test state, or edits to active state. GA/test/Ops authority, V3 and legacy remain untouched. Account for legacy payment receipts and native completion consolidation.
- Diagnostic: `/home/ubuntu/workspace/v2-manychat-button-727d3625/DIAGNOSTICO.md`. New evidence: `/home/ubuntu/workspace/v2-manychat-button-fix-727d3625/`.

## Prior local maintenance

# V2 model-owned service scope (local)

- Authorized by Carlos: correct the service-scope defect found in the isolated real conversation. Branch `fix/v2-service-scope-727d3625`, worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`, base `377648731664dc2a2acf1b286b79168f2ea94663`.
- Evidence shows the committed facts never contained `service`; recorded model summaries also omit it. Passenger collection was admitted by the parser, then failed in the executor because the activity party could not be resolved.
- Invariant: Maya supplies semantic scope; the adapter validates the effective typed scope before passenger collection/selection admission. Existing state is reusable. Reads alone, product IDs and customer/prose keywords never imply purchase scope or authorization.
- DONE local: causal RED (11 failed, 9 passed), minimal adapter/prompt correction, final focused suite (224 passed), complete suite (2334 passed, 2958 subtests), boundary/diff checks and zero new Ruff findings versus base. Three real-model retrospective scope/collection probes passed on the candidate source, with zero tools/provider dispatch/effects; this is not a complete business journey or a rebuilt-image qualification.
- NEXT outside this local change: publish/rebuild/CI, authorize and repeat the isolated channel journey, then evaluate promotion separately. Production is unchanged.
- Preserve the existing one structural-repair attempt, authored prose, handoff exhaustion, provider freshness and effect guards. No new agent, queue, database, regex, schema field or semantic reviewer. No deploy or external commercial/channel effects in this maintenance step; GA/Ops/V3 and active state untouched.
- Evidence directory: `/home/ubuntu/workspace/v2-service-scope-727d3625/`.

## Prior maintenance (historical)

# V2 child history and error contract (local)

- Current maintenance scope explicitly authorized by Carlos: align child history without count-based truncation, and distinguish invalid child input from invalid model output. No Bókun, commercial-policy, deployment or external-effect changes.
- DONE local: branch `fix/v2-child-history-727d3625`, based on authenticated `874b8f2`, in `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`. Parent context and wire byte bounds preserved; categorical input/output/execution failures cross the existing process and inbox boundaries.
- Evidence: `/home/ubuntu/workspace/v2-child-history-727d3625/RELATORIO.md`. Final clean-env suite: 2317 tests + 2958 subtests passed, no exclusions; 128 integrated tests passed. Preserved incident history crossed the real subprocess once with exact content; SDK/model synthetic, candidate mounted read-only over the authenticated base image with no network.
- NEXT: no automatic rollout. Real-model/WhatsApp validation, candidate image release and deployment remain separate from this local fix. GA, isolated test, Ops, authority and live state are unchanged.

## Preserved release preparation (historical)

- Preparação local: contratos históricos corrigidos sem skips/deselections; 2283 testes + 2958 subtestes passaram. Evidência: `docs/refactor/evidence/2026-09-19-v2-release-preparation.md`.
- DONE local: candidata `a7864e2`, imagem local `sha256:7815b4880763…`, 68 testes na imagem, `/readyz` API-only 200 e compatibilidade de inbox sintética antiga→nova→antiga. Smoke Maya real: 3 turnos/4 chamadas, consulta simulada e recuperação sem replay, zero comandos comerciais/envios. Evidência e limites no relatório acima.
- Correção operacional confirmada por Carlos no chat: a tag de handoff pausa completamente as respostas automáticas no ManyChat; nenhum fluxo adicional é necessário. O pedido anterior de captura/fluxo deixa de ser bloqueio.
- DONE local: handoff somente addTag, sem sendFlow/texto fabricado; outbox, recibos e proteção de resultado incerto preservados. 88 focais e integral 2284 testes + 2958 subtestes passaram; recibo da versão anterior não é reenviado. Evidência: `docs/refactor/evidence/2026-09-20-v2-handoff-tag-only.md`.
- NEXT: construir/qualificar imagem sucessora com handoff por tag e continuar promoção/teste isolado previamente autorizado; não exigir captura ou fluxo adicional. A imagem `a7864e2` ainda não contém esta correção. GA/Ops/V3 e bancos ativos inalterados; reservas/pagamentos reais fora do escopo.

- DONE local: falhas impeditivas da inbox agora chegam ao handoff durável existente (coordenador/outbox/tag/flow), sem depender de resposta da Maya. Reinício, admissão/vínculo interrompidos e identidade anterior ao primeiro commit cobertos. Base `b8e974a`; 65 focais passaram; integral 2273 testes + 2958 subtestes passaram, mesmos sete testes históricos falhando. Evidência: `docs/refactor/evidence/2026-09-19-v2-blocking-error-handoff.md`; plano: `docs/superpowers/plans/2026-09-18-v2-blocking-error-handoff.md`. Sem deploy, entrega real, V3 ou subagentes.

- **Correção de continuidade concluída localmente:** rejeição factual retorna à mesma Maya uma vez, resposta informativa segue recibo/outbox; recuperação inválida ou três falhas de execução chegam a `manual_review`, liberando entradas seguintes. ACK comprometido usa replay. Sem deploy.
- Evidência: `docs/refactor/evidence/2026-09-18-v2-rejection-continuity.md`; **192 focais**, integral **2261 passaram + 2958 subtestes**, as mesmas **sete falhas da base 8f92904 reexecutada**. Fonte testada conferida por hash; não é autorização de promoção.

- Authorized by Carlos on 2026-09-18 in the current chat: incremental simplification with complete lead context, per-component outcomes and removal of redundant conversational protocols. Production and real operations are explicitly outside this authorization.
- Branch: `refactor/v2-atendimento-simples-727d3625`; required worktree: `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
- Verified isolated-test source base: `8980646d5615db9ecb32c97f4579ba09080dc02a`. GA remains independently declared by the verified live authority; this document is not runtime authority.
- Design: `docs/superpowers/specs/2026-09-18-v2-atendimento-simples-design.md`.
- Carlos approved the written specification with “Siga” in the current chat; Graphify is authorized as navigation aid.
- Local increment **4 complete** under `docs/superpowers/plans/2026-09-18-v2-atendimento-simples-04-protocolos.md` (inline, sem subagentes), authorized by “Siga”. Normal turns no longer have confirmation/selection/progress/recap reviews or public correction. Explicit intents, full post-tool context and effect contracts remain; no deploy or real effects.
- Ownership: Maya interprets and authors; tools return facts; controller validates effects. Lead-provided values are reusable in the conversation without PII masking/presence-only substitution.
- Local increment 3A complete: component outcomes, initiation/settlement records and authenticated asynchronous outbox history reach Maya; factual collection and independent reads preserve the immutable commanded workflow. No deploy, real provider/channel call, active-state edit, V3, legacy or Ops change.
- Increment 3A evidence: `docs/refactor/evidence/2026-09-18-v2-atendimento-simples-03a.md`; focal **271 passed**; whole diagnostic **2234 passed, 7 failed, 2958 subtests passed**, same historical IDs. Eighteen new tests fail causally on the previous source. Phase 6 package manifest/checksums regenerated and verified, not ignored.
- Local increment **3B**: explicit internal completion events call the same Maya; receipt/source identities atomically bind authored replies; customer turns consolidate only before dispatch. No schema/database/new queue; historical messages preserved; recovery cannot repeat reservation/payment effects.
- Increment 3B evidence: **380 focal passed**; whole **2261 passed, 7 historical failed, 2958 subtests passed**, no new failure IDs. Twenty-two causal scenarios fail on the preceding source and pass here. Model/transports simulated; runtime authority OK. No real-model E2E, publication or deploy.
- Increment 4 evidence: `docs/refactor/evidence/2026-09-18-v2-atendimento-simples-04.md`; **363 focal passed**; whole **2247 passed, 7 historical failed, 2958 subtests passed**, same exact failure IDs. Fifteen new scenarios fail on the immutable predecessor and pass on the candidate; source/log hashes sealed.
- DONE locally: narrow rejection-continuity correction authorized by Carlos in this chat, per `docs/superpowers/plans/2026-09-18-v2-rejection-continuity.md`. Same Maya, one factual communication-only continuation and bounded inbox failures; no new agent/queue/table or real effects. Local base `8f92904`. Production remains unchanged and GA NO-GO. Real-model qualification and historical suite debt remain separate.
- Clean baseline: Bókun transport and active-execution suites passed `60 passed in 1.33s` in a clean environment. These are baseline contract tests, not proof of corrected Bókun or E2E conversation.
- Runner note: the first dependency command was blocked by the tool's long-lived-process detector; the subsequent minimal runner executed successfully. No product failure was hidden.
- Required pre-commit boundary command: `python3 scripts/check_fasttrack_boundaries.py`; documentation diff must pass `git diff --check`.
- Historical NEXT entries below are retained for provenance only and confer no current implementation/rollout authority.
- Local increment 1: nested checkout and precise no-submit cause implemented; no schema/database or retry layer added.
- Evidence: `docs/refactor/evidence/2026-09-18-v2-atendimento-simples-01.md`; actual logs and hashes under `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`.
- Supported runner: Python 3.12.14; final affected suites `152 passed`; whole diagnostic `2205 passed, 7 failed, 2958 subtests passed`. The same seven failure IDs reproduce on functional base `8980646d...`; do not call the whole suite green. Ruff 0.15.10, compileall and boundaries passed.
- Graphify: code-only pre-change map bound to `ee4dfaa`, with documented extraction gaps; navigation aid, not runtime authority.
- Local increment 2: values and passenger roles reach model requests; shared effective-value resolver; explicit holder binding; reusable contact phone; byte-budgeted dialogue without four-turn deletion. Presence-only fields and name-based role inference removed; confirmation shares normal context serialization.
- Increment 2 evidence: `docs/refactor/evidence/2026-09-18-v2-atendimento-simples-02.md`; focal `272 passed`, final whole diagnostic `2216 passed, 7 failed, 2958 subtests passed`. Same seven historical IDs; twelve new causal cases fail on the preceding source and pass here. Eight simulated turns preserve data and original dialogue across reopening, with no business commands.
- Decision: increments 1 and 2 validated locally; no rollout authorization, real-model E2E or remote CI. Outcome/history/protocol simplification is still pending; whole suite is not green.
- NEXT: increment 3 — component-level outcomes/payments and asynchronous completion in conversation history, retaining scoped idempotency/unknown-effect protection. Then remove redundant review/correction protocols. No deploy or external effects.

## Preserved historical correction — Maya V2 current-observation completion

- Authorized by Carlos in the current chat on 2026-09-15: free disk space, correct the authenticated-price omission found in the real WhatsApp conversation, retest the isolated contact and proceed through the established immutable rollout gates.
- Runtime authority verified `OK` before work; authenticated GA/test base `2ba6735d063e2232d8539d5e1ef85841b31f0525`, tree `53e19d67a6d48cc6b902a09290bc7c57f3d118ec`, image ID `sha256:13d07f45390110e505def42d098fa33ea1119ff55a887dd624200f62875d093a`.
- Branch `fix/v2-observation-completeness-727d3625`; required worktree `/home/ubuntu/agente-v2/.worktrees/observation-completeness-727d3625`.
- Design: `docs/superpowers/specs/2026-09-15-v2-current-observation-completion.md`.
- Active plan: `docs/superpowers/plans/2026-09-15-v2-current-observation-completion.md`.
- Owner: `v2_adapters/hermes_model.py`; causal tests: `tests/test_v2_hermes_model_adapter.py`.
- Root cause: the accepted second frame contained a positive public-safe Cloudbeds observation with exact `600.00 BRL`, but current completion guidance was generic and not conditionally salient at the post-read boundary; the model omitted the total while the controller correctly preserved Maya-owned prose and created no effect.
- Fixed architecture: append one concise current-observation completion suffix only when `ModelRequest.observations` is non-empty. Maya remains the sole semantic/prose owner; no text parser, regex, keyword gate, deterministic rewrite or secondary semantic model is allowed.
- Safety: no reservation, payment, provider POST, handoff, V3, legacy or Ops mutation. The isolated WhatsApp retest starts with `>>>`, uses one fresh operation ID once, reconciles all outbox chunks and forbids blind retry after ambiguity.
- Rollout: immutable image; isolated authorized contact first; exact Terra/high identity plus causal price-answer evidence before GA; preserve the current Terra/high runtime as immediate rollback.
- Disk precondition closed: 38 clean branch-backed worktrees and recreate-only caches were removed; dirty/detached worktrees, active images/state, rollback and final evidence were preserved. Root free space increased from 569 MiB to 4,658 MiB.
- Baseline: exact focused adapter suite passed `72` tests in a clean environment before functional edits.
- Causal RED: `test_current_observation_completion_authority_is_post_read_only` failed after proving the exact `600.00 BRL` payload was present; the only missing witness was the conditional post-read authority marker.
- Focused GREEN: causal witness `1` passed; complete model-adapter suite `73` passed; turn-executor suite `86` passed.
- Static gate: the first `.venv/bin/python -m ruff` runner attempt lacked the Ruff module and did not test product code; the required pinned `ruff==0.15.10` focal run superseded it and passed, followed by compileall, diff check and `fasttrack-boundaries: OK`. A diagnostic whole-tree Ruff run reproduced `96` pre-existing historical findings outside this diff; no unrelated cleanup was made.
- Directly affected gate: `203` passed across model adapter, turn executor, conversation context, Terra prompt and Hermes child.
- Canonical diagnostic without exclusions: `2,175` passed plus `2,958` subtests and exactly the known `7` Phase 7/Phase 8 historical incompatibilities failed. Canonical gate with those seven explicit historical deselections passed: `2,175 passed, 7 deselected, 2,958 subtests`.
- NEXT: commit this evidence binding, rerun focused/static/canonical gates on the exact docs descendant, then build a new immutable image.

## Preserved deployed correction — Maya V2 GPT 5.6 Terra at high effort

- Authorized by Carlos in the current chat on 2026-09-15: change the agent that responds on WhatsApp to GPT 5.6 Terra with reasoning effort `high` and execute a real controlled test.
- Runtime authority verified `OK` before work; original authenticated GA/test base `63bf7b9e09609d8d04e45491d5ae07978c85986d`.
- Branch `fix/v2-terra-high-727d3625`; required worktree `/home/ubuntu/agente-v2/.worktrees/terra-high-727d3625`.
- Design: `docs/superpowers/specs/2026-09-15-v2-terra-high-runtime.md`.
- Plan: `docs/superpowers/plans/2026-09-15-v2-terra-high-runtime.md`.
- Deployed successor: `2ba6735d063e2232d8539d5e1ef85841b31f0525`, tree `53e19d67a6d48cc6b902a09290bc7c57f3d118ec`, provider `openai-codex`, model `gpt-5.6-terra`, reasoning effort `high`.
- Qualification: focused, affected, canonical, image-level, isolated WhatsApp and GA rollout gates passed; current correction above supersedes only its historical NEXT.

## Preserved closed correction — V2 rolling provider-read probe dates

- Authorized by Carlos in the current chat on 2026-09-14: permanently replace static provider-read probe dates with future dates derived from the Bahia business clock, qualify the exact candidate, deploy first to the isolated test runtime and then to GA only after green gates.
- Runtime authority verified `OK` before work at 2026-09-14T20:09:52Z; authenticated GA base `0d790e7c8ce842a37abd5baab1035c1b65f774fd`.
- Branch `fix/v2-service-reliability`; required worktree `/home/ubuntu/agente-v2/.worktrees/service-reliability`.
- Design: `docs/superpowers/specs/2026-09-14-v2-rolling-read-probe-dates-design.md`.
- Active plan: `docs/superpowers/plans/2026-09-14-v2-rolling-read-probe-dates.md`.
- Owner: `v2_host/production.py`; causal tests: `tests/test_v2_production_composition.py`. Existing date settings remain load-compatible but become non-authoritative.
- Fixed contract: convert explicit UTC worker `now` to `America/Bahia`; check-in is business date + 30 days; check-out is check-in + 3 days; activity date equals check-in. Use one calculated window; accept each returned observation against a fresh exact UTC sample taken after its GET.
- Safety: probe remains GET-only. No provider write, booking, reservation, payment, handoff, active SQLite edit, V3 work, legacy reuse or Ops mutation. Channel smoke is separately bounded and effects-closed.
- Baseline: 59 affected tests passed on the exact base with clean environment before edits.
- Causal RED: three cases failed on the exact expected mismatch, emitting static `2020-01-01` instead of the derived Bahia dates. The earlier test-shape KeyError was preserved separately and superseded.
- First functional candidate `50ff0642d70cb2f856d9fc995921464d1681bfcf`; tree `b852dbecd5cd6dd02af69e818f6e350451c25e39`. Its date regressions and canonical suite passed, but GET-only image qualification correctly rejected it: the adapter stamped an observation after the cycle clock and `accept(now=cycle_start)` raised `ReadBindingMismatch("observation is from the future")`. Local image `sha256:05e472c153e3e7b79520a59f9c3c36dc51aac8fe58988fc07058a63d606957ff` was never pushed or deployed and is superseded.
- Successor functional commit `203a41d256d920294db83b56dd85fc3fbc4dedb5`; tree `ec9c0581ea3a412c085842a3180207d7db2a9251`. Successor causal GREEN: 5 passed; affected gate: 63 passed with 1 historical dependency warning; pinned Ruff 0.15.10, compileall, diff check and fast-track boundary guard passed.
- Frozen descendant `50803ba58e00242a5f207073e9625c00538bbce1` passed 2,171 tests plus 2,958 subtests and dark provider reads, but independent review rejected it before rollout: an ordinary Bókun activity probe could transitively reach quote checkout when GA write capability was enabled. Local image `sha256:be456adca036f17193a5adeac3bf441cf166a0a5b7d6862ea5821b05a54e7d8c` was never pushed or deployed and is superseded.
- GET-only causal RED: three probe cases lacked an explicit safety contract and the matched-group/two-participant request could not express it. GREEN: the activity-only exact boolean is bound into probe identity; group enrichment routes it only through Bókun availability reads; the existing transport witness proves GET/GET with quote checkout enabled. Affected gate: 88 passed; pinned Ruff 0.15.10, compileall, diff check and fast-track boundary guard passed.
- GET-only functional successor `126476a9a091b7ec8ce13c22ae6a5d5714ddf77e`; tree `b05d7737a50e38765b366366c3e15ee23be4068a`.
- Frozen descendant `42140dbfb408bf4ad5f3a9853a17d5d0a6fd4894` passed 2,173 tests plus 2,958 subtests and a real write-enabled/GET-guarded provider probe, but re-review timed out after proving two test-adequacy survivors: a pre-GET shared acceptance sample and a `V2ReadService` availability-flag drop. Local image `sha256:b9b314d3fd04533dbb8d7440b88557e088feccfb985b7dc4e3e4e5e15d540463` was never pushed or deployed and is superseded.
- Strong witnesses now enforce exact `read → clock → accept` ordering twice and traverse `ReconciliationStage → V2ReadService → group enrichment → Bókun adapter → real HTTP transport`. Both in-memory mutants are killed. Strong focal gate: 2 passed; affected gate: 89 passed; pinned Ruff 0.15.10, compileall, diff check and fast-track guard passed.
- Strong-test successor `5470c2a6160c577b57f7f5998738e8335bf9bfd7`; tree `09607d709bf3ec1a6f3fbc06108bc994fd532ceb`.
- The prior date RED, acceptance-clock RED, invalid test/runner-shape attempts and first rejected image are preserved separately in evidence.
- Evidence root: `/home/ubuntu/workspace/v2-rolling-probe-727d3625/`.
- Remaining gates: canonical/static suite; independent exact-SHA re-review; new immutable image identity; GET-only proof with write-enabled Bókun transport; isolated-test rollout; GA rollout; final READ → VERIFY.
- Rollback: preserve exact predecessor image/config/state bindings before each runtime mutation; restore and verify predecessor on any failed gate.
- NEXT: commit this evidence-only binding, authenticate the final docs descendant, rerun canonical/static gates and obtain independent exact-SHA re-review before rebuilding.

## Preserved closed correction — V2 service reliability

- Authorized by Carlos in the prior current-chat batch: correct audited loose ends with no regex, keywords, substring intent detection or fragile case catalogs. Pix/Wise validation is distinct from Stripe.
- Prior immutable GA base `b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3`; prior plan `docs/superpowers/plans/2026-09-05-v2-service-reliability.md`.
- Tasks A–D implemented in `97bb095d3231f055fbb0022c77f818feb1dd9850`. Final compatibility correction switches only the host-test reopen from V1 to V2; legacy V1 setup and authentication/replay/evidence assertions are preserved.
- Corrections closed locally on tested runtime commit `42bfdfa9f2f6d72faf53abfd4de9c8a0224b70ee`: canonical exit 0, 2167 passed, 7 historical deselections, 2958 subtests passed. Evidence: `/home/ubuntu/workspace/v2-service-fixes-43d84d54/FECHAMENTO.md`.
- The prior batch's `NEXT: none` and no-deploy boundary remain historical and are superseded only by the new rolling-probe authority above.

## Historical context (not the active implementation authority)

# Refactor control — Maya V2 agent process and read enrichment

## Authority

- Authorized by Carlos Eduardo on 2026-08-11: preserve the stable Maya V2 context work, but remove every privacy/PII mechanism that inspects, changes, corrects, blocks, retries, or fails a Maya reply. Voluntarily supplied customer data may be repeated.
- Explicit prohibition: no deterministic natural-language extractor, no regex/substring/alias logic for customer intent, facts, progression, confirmation, selection, or handoff decisions; no keyword triggers.
- Model remains the semantic and textual owner. Parent code may validate closed schemas, typed facts, structural progress, authenticated provider observations, receipts, idempotency, and transaction authority, but never PII in Maya text.
- Privacy/PII work is forbidden unless Carlos requests it explicitly in the current chat. Do not recommend it. Open-ended privacy work is forbidden.
- No deploy, restart, canary promotion, broad rollout, production provider POST, or real external effect is authorized by this phase.
- Authorized by Carlos Eduardo on 2026-08-11 after the natural-conversation audit: continue with the prompt/skill/process and provider-information approach before adding controller complexity. The approved implementation reuses the existing prompt and read tools; it adds no new skill inventory or controller gate.

## Immutable base

- Base commit: `496799ed0d8c30f9d966fdea9e9c86b546ac992e`
- Base tree: `cde291f6f5b4df015e2609c0470fda2e2805b9fd`
- No new OCI has been built for this phase.
- The base candidate remains immutable. All changes belong to the successor branch below.

## Active successor — Maya V2 group-enriched activity availability

- Authorized by Carlos Eduardo on 2026-08-13: every Maya V2 activity lookup must compose the group sheet and Bókun inside the runtime and return one result to Maya; a one-person option for a minimum-two activity may exist only when the same activity/date has a matching group, otherwise it must not be offered.
- Branch: `maya-v2-group-enriched-availability`.
- Required worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard`.
- Immutable base: `acfd5d6c1f7ecfef2bebf875a8e5a4dac37da265`.
- Design: `docs/superpowers/specs/2026-08-13-maya-v2-group-enriched-availability.md`.
- Plan: `docs/superpowers/plans/2026-08-13-maya-v2-group-enriched-availability.md`.
- Runtime decision: Maya receives no new tool. `ReadKind.ACTIVITY` remains one model-facing operation and a composite runtime adapter owns group lookup plus Bókun lookup/resolution.
- Failure decision: for 2+ participants, sheet failure degrades to a Bókun result with group status unavailable; for exactly one participant on the six closed minimum-two products, missing/unavailable group evidence fails closed and hides the option.
- Safety decision: Bókun remains authoritative for availability, price, exact rate/category and executable private binding. Solo eligibility is revalidated during private binding resolution before any provider write.
- Scope decision: start only with the six recovered V1 product/rate mappings; no dynamic generalization to all minimum-two products.
- No-effect rule: implementation/tests use fakes and perform no provider write, ManyChat delivery, payment, handoff, or V3 change. Production promotion follows the plan only after focused/canonical gates, immutable image binding, rollback metadata and read-only smoke.
- Environment evidence: global `python -m pytest` failed because global Python has no pytest. A first `uv pip install -e '.[runtime,dev]'` attempt failed because this flat monorepo is intentionally not an editable setuptools package. A manually created `.venv` was superseded and removed after confirming the canonical existing `venv` selected by `uv run`. These are runner-selection failures, not product test failures.
- Focused immutable baseline: `62 passed` across Bókun party reads, authenticated read bridge, reads, settings, and productive composition using `uv run --extra runtime --extra dev python -m pytest ...`.
- Exact NEXT: execute Task 1 RED tests for the closed group policy and read-only CSV source, prove causal failures, then implement only that task and run its focal gate.

## Preserved release — Maya V2 operational execution dashboard

- Authorized by Carlos Eduardo on 2026-08-13 after approving the dashboard specification: implement, verify, and publish the read-only operational dashboard at `https://hermes.chapadabackpackers.com/ops`.
- Branch: `maya-v2-ops-dashboard`.
- Required worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard` (the current group-availability successor reuses this published worktree path).
- Immutable base: `9226d1b91cdf6007c8f5ce72d0a572e35c4f8a5b`; tree `8c64c013671474b521a2bd9228672477fa7211dc`.
- Design: `docs/superpowers/specs/2026-08-13-maya-v2-ops-execution-dashboard-design.md`.
- Plan: `docs/superpowers/plans/2026-08-13-maya-v2-ops-execution-dashboard.md`.
- Product decision: one execution per inbound message/event, grouped by `lead_id`; recent executions plus exact Lead ID search; n8n-style canvas; Input and Output visible simultaneously; summaries by default and complete permitted content on demand.
- Provider decision: show Maya query, provider request, provider response, and Maya observation as separate typed nodes. Cloudbeds, Bókun, Stripe, Pix/Wise, ManyChat, handoff, reconciliation, claims, leases, fences, and receipts appear only when present in trace or durable ledgers.
- Security boundary: dashboard is read-only, has its own username/password session, receives no provider credentials, mounts operational state read-only, exposes no replay/retry/effect endpoint, and never sends raw authentication headers or secret provider payloads to the browser.
- Instrumentation rule: capture only at typed runtime boundaries; do not inspect or infer customer intent from prose, do not change Maya output, do not add a runtime gate, and do not let trace failure trigger provider retry or alter commercial decisions.
- Test rule: tests use fake model/provider transports and temporary SQLite stores; no real reservation, booking, Payment Link, charge, ManyChat delivery, or handoff is permitted during implementation or dashboard smoke tests.
- Isolation rule: `/home/ubuntu/chapada-leads-v3` is outside scope and must not be edited, cleaned, reset, committed, or used as a source of V2 behavior.
- Completed Task 1: initial contract `8b89a21beb79fd62ca9cb8e5e253c2b0cb9444f9`, followed by causal review fixes `3153fd35ff93b6958cb8f02175de5ef6ec4c3f52`, `b0b517b7b2485348ad913914f4e4ef03d60cd71b`, and `df59e7012b8bb6e5d3bac9f9fdc16cae0bdbea7d`. Closed trace contracts, recursive sanitized JSON validation, strict UTC, deterministic IDs, exact enum types, terminal/timestamp coherence and payload bounds are GREEN with `30 passed`; Ruff, compileall and diff checks passed. REDs closed frozen-JSON serialization, short HTTP credentials, percent-encoded signed URL keys, bounded/NUL-free IDs, raw string statuses, impossible terminal snapshots, and query-value over-splitting.
- Baseline gate for this successor: `1778 passed, 7 deselected, 2953 subtests passed` in 199.05s. The seven exact exclusions match `.github/workflows/phase8.yml` and reproduce unchanged on immutable base `9226d1b9`; no broader deselection is allowed.
- WAL/deploy preflight: seven of eight active operational SQLite stores use WAL with live `-wal`/`-shm` sidecars. The writer/projector therefore owns a dedicated `/data/ops/v2-ops-trace.sqlite3`; the web dashboard mounts only the sanitized `/data/ops` directory read-only. It never mounts `ga-state`, business ledgers or `v2-private-customer.sqlite3`. A host/worker projector copies only typed sanitized milestones for `ledger_only` history.
- Completed Task 2: initial store `34d91c3d613b24c032601ded0ea3eb90fe9dc1bc`; immutable-review successor `0a8cb4d15cbe45833452508e2cab828d6c31867b`. The dedicated STRICT SQLite trace store, AES-256-GCM full content, monotonic/idempotent writer, read-only URI/authorizer, schema drift detection, bounded pagination/stale views, foreign-DB no-mutation guard and real two-writer contention are GREEN.
- Completed Task 3: initial recorder/typed serializer `f6f2f8840a3273d007578637dc11b8d65039e0a1`; recovered timed-out audit probes produced causal REDs for trace-local `BaseException` leakage and open status/channel labels. Successor `c2cf47708ccd9657af52f92885333e2ec18f92e0` closes both; governance commit `82122addbb677298c4374eeeebe5e8a0765d9bdd` brings `v2_ops` under the dependency scanner. Combined gate: `93 passed`; scanner, Ruff, compileall and diff checks passed.
- Completed online effect instrumentation: Cloudbeds/Bókun provider writes, Stripe/Pix/Wise initiation and causally owned boundary-public ManyChat delivery are observed only after their existing durable fences. Recorder failures, including `BaseException`, preserve the exact business return/exception and never repeat the external call. The legacy completion-public queue remains intentionally uncorrelated because its durable row has `release_id/source_message_id` but no authenticated inbound `event_id/source_turn_receipt_hash`; no temporal or name-based join is permitted.
- Completed ledger-only projection: boundary V8 roots require the authenticated primary source row and exact turn-receipt backlink; execution enrichment requires the identical canonical `command_id/command_hash`; payment enrichment requires the deterministic payment identity and exact selection hash. Source readers use `mode=ro`, `query_only=ON`, official semantic/schema authentication, pre-materialized WAL/SHM sidecars and physical source/output separation. The browser opens only the dedicated sanitized Ops store.
- Candidate `fea9a9efdc331502549fc030cbff141ecb6d0ab3` passed the clean canonical gate with `1944 passed, 7 deselected, 2953 subtests passed`; its runtime and Ops images passed hardened dark smokes. During final exact-SHA audit, an Important causal witness proved that a post-terminal effect node still `running` made the reader expose a completed execution as `running` and clear its terminal timestamp. The Ops cutover was rolled back to `d362f0ac`; V2 writer instrumentation remained active because the defect is confined to read projection and does not alter business execution.
- TDD successor closes that finding: `_execution_view()` derives status/timestamps from nodes only while the stored execution itself is `running`. Post-terminal nodes remain separately visible and may advance `current_node_id`, but cannot change terminal `status` or `completed_at`; the `running_stale` filter excludes terminal executions. RED reproduced `stored=completed` / `visible=running`; GREEN evidence is `55 passed` across store plus all causal effect tests and `161 passed` for `tests/test_v2_ops_*.py`; Ruff, boundary guard and `git diff --check` passed.
- Independent projection audit on `fea9a9e` returned `APPROVE`; the effect audit produced the Important finding above and is not an approval. Exact NEXT: freeze the TDD successor, repeat canonical/static/no-effect gates and exact-SHA audits, rebuild both images with matching OCI revision labels, repeat hardened dark smokes, publish Ops and V2 reversibly, and prove rollback/restore while preserving `9226d1b9` V2 and `d362f0ac` Ops rollback artifacts.
- Corrected Task-4 scope: include `v2_host/app.py` and ManyChat ingress tests because authenticated webhook validation and `inbox.accept(event)` live there. This is required for truthful `manychat_webhook → router_validation → inbox_accept` nodes and authorizes no unrelated API/deploy work.
- Task-4 execution rules: every seam defaults to `NullOpsRecorder`; an enabled trace writer is worker-owned, process-lifetime and closed with the runtime lifecycle; API ingress receives only a recorder seam, never browser/SQLite access. Multi-event batches create one execution per event; expensive batch-level nodes belong only to the deterministic primary `(occurred_at,event_id)`, while siblings receive batch-reference nodes and never duplicate model/provider calls. Emit only at typed boundaries after durable claim/validation; preserve retries, idempotency, effect counts and business result/exception identity. Follow `.superpowers/sdd/task-4-execution-brief.md` and TDD.

## Preserved release branch — Bókun commercial query binding

- Authorized by Carlos Eduardo on 2026-08-12: investigate and permanently correct `private_binding_mismatch`, simplifying the conversation-to-worker path without relaxing confirmation or effect safety.
- Branch: `maya-v2-bokun-commercial-binding`
- Required worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-bokun-commercial-binding`
- Immutable base: `89f50d3b038f1f2f9c61704c43f4a118665e870a`; tree `7c96c2192424258d3396afd54fb5229433cd5446`.
- Design: `docs/superpowers/specs/2026-08-12-bokun-commercial-query-binding-design.md`.
- Plan: `docs/superpowers/plans/2026-08-12-bokun-commercial-query-binding.md`.
- Root cause: localized conversation reads include `locale` in `ReadRequest.query_hash()`, while `_offer_and_query()` persists a reconstructed no-locale hash in `lookup_id`; a fresh worker therefore hashes identical Bókun private fields under different query identities.
- Owner: `v2_contracts.providers.ReadRequest.query_hash()`; exact request identity remains `canonical_hash()`.
- Baseline focused gate: `75 passed` across reads, Bókun party/provider transports, and reservation preparation.
- RED evidence: both causal tests failed on the immutable base — localized/non-localized activity `query_hash()` values differed and a fresh resolver raised `PrivateBindingMismatch`.
- Minimum implementation: `ReadRequest.query_hash()` excludes `locale` only for `ReadKind.ACTIVITY`; exact request hashes and knowledge query hashes remain locale-sensitive. `_query_from_component()` reconstructs the Bókun activity query identity from the component and rejects product/date/party disagreement with `lookup_id` before provider I/O, while preserving both current `adults`/`children` and no-children legacy `participants` shapes.
- Focused GREEN evidence: `4 passed` on locale identity, fresh-composition survival, executable-rate mutation rejection, and knowledge locale behavior; final affected gate `119 passed`. Product/date/party mutation regression confirms zero provider calls on incoherence. `compileall`, `git diff --check`, boundary-import guard, and secret guard passed.
- Retrospective compatibility audit: applying the new activity identity to the seven persisted Bókun commands in the blocked refreshed corpus produced `7/7` exact localized-vs-lookup query-hash matches. Historical commands and blocked roots were not modified, promoted, reauthorized, or executed.
- Superseded evidence: one broadened pytest invocation used nonexistent `tests/test_v2_knowledge_contract.py`, exited 4, and ran zero tests; it was replaced by the valid 119-test gate. A repository-wide run without the canonical deselections produced `1769 passed` plus the same 7 historical closeout/package failures reproduced on the immutable base; a later canonical run was intentionally killed at 17% after a test-only mutation made that collection stale.
- Real-provider read-only proof: `/home/ubuntu/maya-v2-bokun-binding-fix-proof-20260812/REAL_READ_ONLY_PROOF.json`; schema v2; `pt-BR` and `en` each survived a fresh transport/adapter composition; 8 signed GETs, 0 non-GET calls, 0 reservation/payment workers, 0 bookings, and 0 payment links. Report SHA-256 `1c580feca0b637645d48ee55d810ce84259ff07eac9f0b17ff4a7954e030c845`; harness SHA-256 `ca1f145fdaa8c18d85867129b0037e29e2e80af6047322fe98fdc224611d65e9`.
- Canonical gate before bounded review: frozen full diff SHA-256 `ee4d9a3c8bc7756cde037e0549a9ba49390eef7a35775c3ea66c7fc4254b703c` / functional diff SHA-256 `a408e04a681a773795d313b847e63b935ce977bb72fc8dce077d10ca68bcad0f`; clean-environment pytest passed with `1769 passed, 7 deselected, 1 warning, 2953 subtests passed` in 201.96s.
- Bounded-review finding and resolution: a malformed canonical product embedded in `lookup_id` failed before provider I/O but escaped as `InvalidReadRequest`, outside the reservation adapter's normal `PrivateBindingMismatch` classification. A causal RED reproduced it; `_query_from_component()` now catches only `InvalidReadRequest` from activity-query reconstruction and raises `PrivateBindingMismatch("activity lookup identity is invalid")`. The four causal tests, final affected gate (`119 passed`), and GET-only proof all passed afterward.
- Canonical post-review gate on staged tree `025cb9a591c76fd72309d26494f400f4b910d697`, full staged diff SHA-256 `9e647a29b1ad5769c96360574a39b81105e022bb480fabfcdcc6182abf8318ee`, and functional staged diff SHA-256 `21285a9d253fbc66f827a1fff030bf084d6ebc3e0d4da43be88b20a9f1ba8b7c`: clean-environment pytest passed with `1769 passed, 7 deselected, 1 warning, 2953 subtests passed` in 200.61s.
- Local completion decision: **GO for a local commit and preserving this branch/worktree; NO-GO for merge, push, deploy, restart, canary, provider write, booking, payment link, ManyChat, or handoff without a new explicit authorization**.
- Exact NEXT after the local commit: keep `maya-v2-bokun-commercial-binding` and its worktree intact for review/integration. Any real control booking still requires separate authorization and must use one claim, one durable fence, one POST, provider GET reconciliation, and no Stripe link before the reconciled Bókun reference exists.

## Historical successor — Maya V2 agent process and read enrichment

- Branch: `maya-v2-agent-process-refinement`
- Required worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-agent-process-refinement`
- Design: `docs/superpowers/specs/2026-08-11-maya-agent-process-and-read-enrichment-design.md`
- Plan: `docs/superpowers/plans/2026-08-11-maya-agent-process-and-read-enrichment.md`
- Starting candidate: `496799ed0d8c30f9d966fdea9e9c86b546ac992e`; the audited candidate and its historical conversation root remain immutable.
- Baseline affected gate: `114 passed` on the starting candidate. The global Python initially lacked pytest; the isolated worktree environment was created with `uv sync --extra runtime --extra dev` and no runtime service was changed.
- Focused RED evidence: the shared-dorm query returned `conexao_feira_vindo_sul`; the new prompt process assertion was absent; and the Cloudbeds room-description payload lacked `room_public_name`.
- Implemented affected gate: `121 passed` across prompt, Cérebro, Bókun reads, Cloudbeds/provider transports, and Hermes model adapter; `git diff --check` passed.
- Static and canonical gate on `bc5bc05fede180effd86e3e073544e8da249bb0a` / tree `afa4a7a2e233ac2cb5a8f0423b8c1133ed13cc02`: `compileall` and committed-diff checks passed; repository-wide Ruff has 497 historical findings while the same changed-file selection has 17 on base and successor (delta zero); canonical clean-environment pytest passed with `1765 passed, 7 deselected, 2953 subtests passed`.
- Real-model fake-read smoke on that candidate: the historical fake `+1` profile correctly selected English and was rejected as invalid harness evidence; the corrected `+55` smoke answered the shared-room question in Portuguese with one knowledge read, zero commands/relays and zero external effects, but manual review marked a WARN for adding an unsupported social benefit.
- Post-smoke causal RED/GREEN: the existing process test now also requires that Maya not add unsupported praise, popularity, suitability, or commercial benefits; the directly affected gate remains `121 passed`.
- First five-scenario attempt on `c5e79b9c8a509fc72b8434c2e575874738a7cc14` was stopped after `agency-solo-4ps` failed manual semantic review: it still converted the canonical "more accessible" classification into unsupported popularity/quality language and asked permission for a read whose observation was already present. The completed scenario recorded one local command and one pending relay, but zero provider/Stripe/ManyChat/external-effect calls; the controller process was intentionally killed while the second scenario was in progress.
- Second causal RED/GREEN: the prompt now states that the accessibility classification is the only pre-description justification and that a post-read frame must answer from the observation instead of asking whether to perform an already completed read; the directly affected gate remains `121 passed`.
- Hotspot rerun on `5c0c2ab255a5ae43989e9bcd6934fd32fc4806c4` / tree `90540c28d260fe68b733cf0acde4f74e7620ac85`: `agency-solo-4ps` no longer invented popularity/quality or asked permission to run an already completed read. Manual semantic review passed with a minor progression WARN because it asked the lead to choose between 4Ps and 2Ms after reading 4Ps. No controller rule is justified for that inefficiency. Static checks and canonical clean-environment pytest passed with `1765 passed, 7 deselected, 2953 subtests passed`.
- Remaining real-model fake-read qualification on the same candidate: `agency-couple-sossego` and `hostel-solo-shared` passed semantic review; `hostel-couple-private` was invalid evidence because the fake knowledge matcher selected the shared-dorm answer on the generic word "hostel" and will be rerun with the specific private entry prioritized; `package-solo-4ps-late` correctly selected the mixed dorm later but initially listed a female-only room to a lead who had already identified himself in the masculine. The package harness also incorrectly expected one command although a valid package creates two local component commands. All runs recorded zero external-effect calls.
- Third causal RED/GREEN: the model-owned prompt now distinguishes an explicitly stated female/male/mixed audience in `room_public_name` from inferred amenities and requires semantic comparison with the lead's original self-identification; the directly affected gate remains `121 passed`. No parser, regex, controller gate, or runtime audience classifier was added.
- Final functional candidate: `fe0e5c58d7dc6e72c72f859a2ee859be159efc49` / tree `4af5f363ff38f288656371a2ee331cfe1e53b3b3`; worktree clean. Technical gates: affected `121 passed`; canonical clean-environment `1765 passed, 7 deselected, 2953 subtests passed`; `compileall`/diff PASS; changed-file Ruff base 10 / candidate 10 / delta zero.
- Final immutable real-model qualification on that same candidate: five historical scenarios, 25 turns, 47 model calls and 24 fake reads; manual semantic review **5 PASS / 0 FAIL**, with nonblocking progression WARNs documented for `agency-solo-4ps` and `package-solo-4ps-late`. Runner mechanics: 3 PASS / 2 WARN / 0 FAIL; both mechanical WARNs are the legacy synthetic-profile expectation `private_fact_names_mismatch:birth_date,gender_expected_`.
- Effect audit: six local commands and six relays, all pending; zero claims, owners, leases, target receipts, ACKs or effect outbox rows; zero Cloudbeds/Bókun HTTP, Stripe, Pix/Wise, ManyChat delivery/handoff, provider refs, reservations, bookings, checkouts, links or charges. Evidence: `/home/ubuntu/maya-v2-agent-process-refinement-fe0e5c5-20260811T231549Z/SEMANTIC_AUDIT.md`; `EFFECTS_BLOCKED` remains present and no approval-for-effects marker exists.
- Independent exact-SHA review of the bounded diff found one Important issue and no Critical issue: `_first()` normalized `room_public_name` before the strict adapter boundary, accepting numeric names and stripping noncanonical whitespace. Two causal RED cases reproduced both defects; raw Cloudbeds name validation now rejects non-text, empty, or whitespace-altered values before projection, and the affected gate is `123 passed`.
- Exact next gate: freeze the strict transport fix, run canonical/static gates on the new SHA, and rerun the five fake-read real-model scenarios with the unchanged prompt under that exact candidate binding. Promotion remains NO-GO until a separate explicit authorization for push/deploy/canary/effects; no such authorization exists in this chat.

## Required invariants

1. The complete current customer message remains untouched and model-owned.
2. Recent dialogue is bounded, integrity-checked model context. The parent does not parse customer language or inspect Maya text for personal data.
3. Commercial facts, intent, reads, clarification, selection, confirmation, and handoff remain semantic model outputs under closed contracts.
4. A structurally empty frame may trigger at most one model-owned progress review. Parent code never derives customer semantics from text.
5. Provider results are grounded from accepted typed observations. Granular grounding preserves a separately typed clarification question without parsing prose.
6. Handoff language is derived from the exact durable lifecycle status. Pending relay is never represented as human acknowledgement or active human monitoring.
7. One read round, profile authority, confirmation binding, provider write gates, idempotency, outbox, writer/reconciler, and Stripe/Wise/Pix boundaries remain authoritative without changing Maya text for privacy/PII.
8. Tests and model evaluations use fake business providers and no workers that can create external effects.

## Execution gates

- RED tests must fail for the intended causal reason before implementation.
- For this bounded reversal, focused causal tests, the directly affected gate, canonical clean-environment pytest, `git diff --check`, and a zero-reference scan for the removed output mechanism must pass before the local commit.
- Real-model evaluation is allowed only in a controlled sandbox with tool-free Hermes and fake/no business providers.
- A new immutable commit/tree/OCI may be frozen only after repeated conversational qualification.
- Promotion remains blocked until Carlos explicitly authorizes it after reviewing evidence.
