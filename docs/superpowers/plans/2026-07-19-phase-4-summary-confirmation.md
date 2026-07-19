# Fase 4 — Resumo e confirmação únicos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar um boundary puro que renderiza exatamente um resumo por versão, classifica a resposta natural sem autoridade comercial, vincula-a ao resumo vigente e permite ao reducer emitir no máximo um `ReservationCommand`.

**Architecture:** `reservation_confirmation` conterá tipos, renderer, presentation builder, classifier Protocol/referência, trusted binder e properties. `reservation_domain` continuará como owner exclusivo da FSM/comando e ganhará apenas `AwaitingAdjustmentState` e a regra de ajuste semântico. Todos os fluxos serão in-memory, sem LLM, rede, provider, entrega ou persistência live.

**Tech Stack:** Python 3.12 stdlib, `dataclasses`, `Enum`, `Protocol`, `Decimal`, `hashlib`, `json`, `unicodedata`, `unittest`, scripts CLI e GitHub Actions.

## Global Constraints

- Repositório: `/home/ubuntu/agente-v2`, branch `main`.
- Spec canônica: `docs/superpowers/specs/2026-07-19-phase-4-summary-confirmation-design.md`.
- Legado `/home/ubuntu/chapada-leads-hermes` é estritamente somente leitura.
- Não executar Hermes/LLM, ManyChat, rede, provider, Docker, banco, fila, outbox live, deploy ou write comercial.
- Toda produção nova começa por teste RED observado e evidência JSON com exit code não zero.
- `ReservationCommand` continua com owner único em `reservation_domain/reducer.py`.
- `DecisionCandidate` nunca contém version, signature, offer/provider ref ou operation.
- Confirmação contextual exige `ClassificationContext` tipado vigente.
- Frescor temporal da confirmação é estrito: `received_at > presented_at`.
- `ADJUST` desarma o resumo antigo; no-op adjustment não incrementa versão.
- Renderer PT-BR/EN omite IDs privados e qualquer claim de efeito.
- Gate Fase 4: 50.000 casos, seed `20260719`; menor somente com `--smoke`.
- Regressão Fase 2: 100.000 sequências × 20 eventos, seed `20260718`.
- Mutantes executam somente em cópias temporárias do repositório.
- Fixtures são sintéticas e sanitizadas.
- Rollout comercial permanece `NO-GO`.

## File map

```text
reservation_confirmation/
  __init__.py       exports públicos fechados
  types.py          DTOs e invariantes locais
  renderer.py       projeção pública determinística PT/EN
  presentation.py   IDs e SummaryRecorded derivados
  classifier.py     Protocol + reference classifier fail-closed
  binding.py        contexto vigente + ConfirmationReceived
  properties.py     full-flow property oracle
  README.md          contrato público e limites

reservation_domain/
  types.py           AwaitingAdjustmentState/phase
  reducer.py         adjust disarm + no-op rejection
  properties.py      universo regressivo atualizado

scripts/
  run_phase4_properties.py
  run_phase4_mutations.py
  generate_phase4_manifest.py
  validate_phase4.py

tests/
  test_phase4_types.py
  test_phase4_renderer.py
  test_phase4_classifier.py
  test_phase4_adjustment_state.py
  test_phase4_replays.py
  test_phase4_properties.py
  test_phase4_mutation_runner.py
  fixtures/phase4/confirmation-corpus.json
```

---

### Task 1: Contratos tipados do boundary

**Files:**
- Create: `tests/test_phase4_types.py`
- Create: `reservation_confirmation/types.py`
- Create: `reservation_confirmation/__init__.py`
- Create: `reservation_confirmation/README.md`
- Create: `docs/refactor/evidence/phase-04/red-result-types.json`
- Modify: `docs/refactor/phases/phase-04-single-summary-and-confirmation.md`

**Interfaces:**
- Consumes: `CommercialDraft`, `SummaryRecorded`, `ConfirmationReceived`, `ConfirmationDecisionKind`.
- Produces: `SummaryLocale`, `RenderedSummary`, `PreparedSummary`, `ClassificationContext`, `ClassificationInput`, `DecisionCandidate`, `BoundConfirmation`.

- [ ] **Step 1: Write the failing type tests**

Create tests that exercise exact type closure and hostile inputs:

```python
class Phase4TypeTests(unittest.TestCase):
    def test_candidate_has_no_commercial_target_fields(self) -> None:
        self.assertEqual(
            {field.name for field in fields(DecisionCandidate)},
            {
                "decision", "classifier_id", "classifier_version",
                "confidence_basis_points", "evidence_codes",
            },
        )

    def test_rendered_summary_recomputes_content_hash(self) -> None:
        with self.assertRaisesRegex(ValueError, "content_hash"):
            RenderedSummary(
                renderer_id="summary-renderer",
                renderer_version=1,
                locale=SummaryLocale.PT_BR,
                draft_id="draft:alpha",
                draft_version=1,
                subject_signature="a" * 64,
                content="Resumo sintético",
                content_hash="b" * 64,
                claim_status="none",
                private_fields=(),
            )

    def test_classification_input_requires_canonical_text_and_utc(self) -> None:
        with self.assertRaises(ValueError):
            ClassificationInput(
                source_event_id="evt:alpha",
                received_at=datetime(2027, 1, 1),
                text="   ",
                context=None,
            )
```

Also assert exact enums, exact integer types (`bool` rejected), UTC timestamps,
canonical IDs/hashes, sorted unique evidence codes, empty private fields and
`claim_status == "none"`.

- [ ] **Step 2: Run RED and record it**

Run:

```bash
python3 -m unittest tests.test_phase4_types -v \
  > /tmp/phase4-red-types.out 2>&1
```

Expected: non-zero with `ModuleNotFoundError: reservation_confirmation`.
Write `red-result-types.json` with command, exit code, expected failure class,
UTC timestamp and SHA-256 of `/tmp/phase4-red-types.out`; do not version raw log.

- [ ] **Step 3: Implement exact DTO invariants**

Use strict helpers, not `str(value)` coercion for public IDs:

```python
class SummaryLocale(str, Enum):
    PT_BR = "pt_BR"
    EN = "en"

@dataclass(frozen=True, slots=True)
class DecisionCandidate:
    decision: ConfirmationDecisionKind
    classifier_id: str
    classifier_version: int
    confidence_basis_points: int
    evidence_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.decision) is not ConfirmationDecisionKind:
            raise ValueError("decision must use ConfirmationDecisionKind")
        _require_id(self.classifier_id, "classifier_id")
        if type(self.classifier_version) is not int or self.classifier_version < 1:
            raise ValueError("classifier_version must be an integer >= 1")
        if (
            type(self.confidence_basis_points) is not int
            or not 0 <= self.confidence_basis_points <= 10_000
        ):
            raise ValueError("confidence_basis_points must be 0..10000")
        ordered = tuple(sorted(self.evidence_codes))
        if len(set(ordered)) != len(ordered):
            raise ValueError("evidence_codes must be unique")
        object.__setattr__(self, "evidence_codes", ordered)
```

`RenderedSummary.__post_init__` must recompute SHA-256 from canonical JSON of
renderer ID/version, locale, draft binding and content. `PreparedSummary` must
verify that its event IDs/timestamp/draft binding equal `RenderedSummary` and
`SummaryRecorded`. `BoundConfirmation` must verify that a non-`None` event uses
the bound candidate decision and IDs.

- [ ] **Step 4: Export only the closed public API**

`reservation_confirmation/__init__.py` imports only the seven DTOs and locale at
this task. `README.md` states no I/O, no target authority, no raw message
persistence and no provider claim.

- [ ] **Step 5: Run GREEN and regressions**

```bash
python3 -m unittest tests.test_phase4_types -v
python3 -m unittest tests.test_phase3_lookup_types tests.test_phase2_serialization -q
python3 -m compileall -q reservation_confirmation tests
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add reservation_confirmation tests/test_phase4_types.py \
  docs/refactor/evidence/phase-04/red-result-types.json \
  docs/refactor/phases/phase-04-single-summary-and-confirmation.md
git commit -m "feat(phase-4): add confirmation boundary contracts"
```

---

### Task 2: Renderer e preparation bundle determinísticos

**Files:**
- Create: `tests/test_phase4_renderer.py`
- Create: `reservation_confirmation/renderer.py`
- Create: `reservation_confirmation/presentation.py`
- Create: `docs/refactor/evidence/phase-04/red-result-renderer.json`
- Modify: `reservation_confirmation/__init__.py`
- Modify: `reservation_confirmation/README.md`

**Interfaces:**
- Consumes: `CommercialDraft`, `ReadyToSummarizeState`, `SummaryLocale`.
- Produces:
  - `render_summary(draft, *, locale) -> RenderedSummary`;
  - `prepare_summary(state, *, locale, presented_at) -> PreparedSummary`.

- [ ] **Step 1: Write RED renderer tests**

Build synthetic lodging, activity and package drafts. Assert exact public output
for PT/EN and metamorphic safety:

```python
rendered = render_summary(package_draft, locale=SummaryLocale.PT_BR)
self.assertIn("Resumo do pedido", rendered.content)
self.assertIn("Nenhuma reserva foi criada", rendered.content)
self.assertEqual(rendered.claim_status, "none")
for private in (
    *[item.offer_id for item in package_draft.components],
    *[item.lookup_id for item in package_draft.components],
    *[item.provider_ref for item in package_draft.components],
    package_draft.subject_signature,
):
    self.assertNotIn(private, rendered.content)
self.assertNotIn("Total confirmado", rendered.content)
self.assertNotIn("reserva confirmada", rendered.content.casefold())
```

Assert same draft/locale produces byte-identical output/hash; locale changes
content/hash but not subject; component/add-on input order does not change text;
amount/date/time/party/customer/payment changes do; content controls/newlines are
collapsed; non-finite or mixed currency remains rejected by domain.

For `prepare_summary`, assert IDs are deterministic, caller supplies no IDs, and
the returned `SummaryRecorded` matches version/signature/time/outbox.

- [ ] **Step 2: Run RED and record it**

```bash
python3 -m unittest tests.test_phase4_renderer -v \
  > /tmp/phase4-red-renderer.out 2>&1
```

Expected: import failure for `render_summary`. Record sanitized JSON evidence.

- [ ] **Step 3: Implement canonical rendering**

Core projection must be explicit:

```python
def _component_lines(component: OfferSnapshot, locale: SummaryLocale) -> tuple[str, ...]:
    service = "Hospedagem" if component.service is ServiceKind.LODGING else "Passeio"
    if locale is SummaryLocale.EN:
        service = "Lodging" if component.service is ServiceKind.LODGING else "Activity"
    lines = [
        f"{service}: {_public_text(component.public_label)}",
        f"Data: {component.start_date.isoformat()}"
        if locale is SummaryLocale.PT_BR
        else f"Date: {component.start_date.isoformat()}",
    ]
    if component.end_date is not None:
        lines.append(
            ("Até: " if locale is SummaryLocale.PT_BR else "Until: ")
            + component.end_date.isoformat()
        )
    if component.start_time is not None:
        lines.append(("Horário: " if locale is SummaryLocale.PT_BR else "Time: ") + component.start_time)
    lines.append(_party_line(component.party, locale))
    lines.append(_money_line("Valor" if locale is SummaryLocale.PT_BR else "Price", component.total))
    return tuple(lines)
```

Use `Decimal` only. Render every customer field and payment/add-ons. Do not
render `available`, because that would be a future availability claim. End PT
with `Nenhuma reserva foi criada. Confirma este resumo ou deseja ajustar algo?`
and EN with `No booking has been created. Do you confirm this summary or want to adjust it?`.

- [ ] **Step 4: Implement deterministic preparation IDs**

```python
def _artifact_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"
```

`prepare_summary` rejects non-exact `ReadyToSummarizeState`, UTC times before
draft creation and renderer mismatch. It derives `summary_event_id`,
`outbox_message_id`, and domain `event_id` from the same artifact subject, then
returns one `PreparedSummary`.

- [ ] **Step 5: Run GREEN and deterministic rerun**

```bash
python3 -m unittest tests.test_phase4_renderer -v
python3 -m unittest tests.test_phase4_types tests.test_phase2_domain -q
python3 - <<'PY'
from tests.test_phase4_renderer import package_draft
from reservation_confirmation import SummaryLocale, render_summary
first = render_summary(package_draft(), locale=SummaryLocale.PT_BR)
second = render_summary(package_draft(), locale=SummaryLocale.PT_BR)
assert first == second
print(first.content_hash)
PY
```

- [ ] **Step 6: Commit**

```bash
git add reservation_confirmation tests/test_phase4_renderer.py \
  docs/refactor/evidence/phase-04/red-result-renderer.json
git commit -m "feat(phase-4): render deterministic reservation summaries"
```

---

### Task 3: Classifier Protocol e corpus PT/EN

**Files:**
- Create: `tests/fixtures/phase4/confirmation-corpus.json`
- Create: `tests/test_phase4_classifier.py`
- Create: `reservation_confirmation/classifier.py`
- Create: `docs/refactor/evidence/phase-04/red-result-classifier.json`
- Modify: `reservation_confirmation/__init__.py`
- Modify: `reservation_confirmation/README.md`

**Interfaces:**
- Consumes: `ClassificationInput`, `DecisionCandidate`.
- Produces: `ConfirmationClassifier`, `ReferenceConfirmationClassifier`,
  `classify_safely(classifier, item) -> DecisionCandidate`.

- [ ] **Step 1: Write the exact synthetic corpus**

Create at least 24 balanced cases. Required messages include:

```json
[
  {"case_id":"pt-explicit","locale":"pt_BR","category":"explicit","text":"Sim, confirmo exatamente esse resumo.","has_context":true,"decision":"accept"},
  {"case_id":"en-explicit","locale":"en","category":"explicit","text":"I confirm this exact summary.","has_context":true,"decision":"accept"},
  {"case_id":"pt-colloquial","locale":"pt_BR","category":"colloquial","text":"Fechado, pode seguir.","has_context":true,"decision":"accept"},
  {"case_id":"en-colloquial","locale":"en","category":"colloquial","text":"Sounds good, go ahead.","has_context":true,"decision":"accept"},
  {"case_id":"pt-contextual","locale":"pt_BR","category":"contextual","text":"Pode fazer.","has_context":true,"decision":"accept"},
  {"case_id":"pt-context-free","locale":"pt_BR","category":"ambiguous","text":"Pode fazer.","has_context":false,"decision":"ambiguous"},
  {"case_id":"pt-negative","locale":"pt_BR","category":"negative","text":"Não confirme.","has_context":true,"decision":"reject"},
  {"case_id":"en-negative","locale":"en","category":"negative","text":"Do not book it.","has_context":true,"decision":"reject"},
  {"case_id":"pt-adjust","locale":"pt_BR","category":"adjust","text":"Troque para cartão.","has_context":true,"decision":"adjust"},
  {"case_id":"en-adjust","locale":"en","category":"adjust","text":"Change it to card.","has_context":true,"decision":"adjust"},
  {"case_id":"pt-mixed","locale":"pt_BR","category":"ambiguous","text":"Sim, mas não confirme ainda.","has_context":true,"decision":"ambiguous"},
  {"case_id":"en-question","locale":"en","category":"ambiguous","text":"Is this confirmed?","has_context":true,"decision":"ambiguous"}
]
```

Add spelling/punctuation/case variants without real messages or PII.

- [ ] **Step 2: Write RED classifier tests**

Load every corpus row, construct context only when `has_context`, and assert
decision/evidence code. Add hostile candidates, Protocol conformance and a
classifier that raises.

```python
class RaisingClassifier:
    def classify(self, item):
        raise RuntimeError("synthetic classifier failure")

candidate = classify_safely(RaisingClassifier(), input_with_context())
self.assertIs(candidate.decision, ConfirmationDecisionKind.AMBIGUOUS)
self.assertIn("classifier_error", candidate.evidence_codes)
```

- [ ] **Step 3: Run RED and record it**

Expected failure: missing `ReferenceConfirmationClassifier`.

- [ ] **Step 4: Implement precedence and exact matching**

Normalize with NFKC + casefold + collapsed whitespace + peripheral punctuation.
Build closed sets by locale/category. Compute signal set first; never return on
the first positive substring:

```python
if item.context is None:
    return _candidate(AMBIGUOUS, "context_missing", confidence=10_000)
if len(signals) != 1:
    return _candidate(AMBIGUOUS, "mixed_or_unknown", confidence=8_000)
if "adjust" in signals:
    return _candidate(ADJUST, "adjust_explicit", confidence=10_000)
if "reject" in signals:
    return _candidate(REJECT, "reject_explicit", confidence=10_000)
return _candidate(ACCEPT, accept_evidence_code, confidence=10_000)
```

`classify_safely` requires exact `DecisionCandidate`; exceptions/wrong types
become deterministic ambiguous candidates.

- [ ] **Step 5: Run GREEN and corpus determinism**

```bash
python3 -m unittest tests.test_phase4_classifier -v
python3 -m unittest tests.test_phase4_types tests.test_phase4_renderer -q
```

- [ ] **Step 6: Commit**

```bash
git add reservation_confirmation tests/fixtures/phase4 \
  tests/test_phase4_classifier.py \
  docs/refactor/evidence/phase-04/red-result-classifier.json
git commit -m "feat(phase-4): classify confirmation decisions fail closed"
```

---

### Task 4: Desarmar resumo antigo após ajuste

**Files:**
- Create: `tests/test_phase4_adjustment_state.py`
- Create: `docs/refactor/evidence/phase-04/red-result-adjustment.json`
- Modify: `reservation_domain/types.py`
- Modify: `reservation_domain/reducer.py`
- Modify: `reservation_domain/properties.py`
- Modify: `tests/test_phase2_domain.py`
- Modify: `tests/test_phase2_serialization.py`
- Modify: `docs/refactor/domain/phase2-domain-contract.md`
- Regenerate: `docs/refactor/domain/phase2-state-event-matrix.md`
- Regenerate: `docs/refactor/evidence/phase-02/domain-manifest.json`

**Interfaces:**
- Consumes: existing `ConfirmationReceived`, `DraftAdjusted`.
- Produces: `WorkflowPhase.AWAITING_ADJUSTMENT`, `AwaitingAdjustmentState`.

- [ ] **Step 1: Write RED authorization tests**

```python
adjust = reduce(
    awaiting,
    ConfirmationReceived(
        event_id="evt:adjust",
        occurred_at=T0 + timedelta(seconds=7),
        confirmation_event_id="confirm:adjust",
        decision=ConfirmationDecisionKind.ADJUST,
        target_draft_version=awaiting.draft.version,
        subject_signature=awaiting.draft.subject_signature,
    ),
)
self.assertIsInstance(adjust.state, AwaitingAdjustmentState)
self.assertEqual(adjust.commands, ())
old_accept = reduce(adjust.state, valid_accept_for(awaiting, seconds=8))
self.assertEqual(old_accept.commands, ())
```

Also assert `DraftAdjusted` with unchanged customer/terms is rejected and stays
disarmed; a semantic change creates version `old + 1`; stale confirmation after
the new draft/summary creates zero; new summary + posterior accept creates one.

- [ ] **Step 2: Run RED and record it**

Expected failure: import failure for `AwaitingAdjustmentState`.

- [ ] **Step 3: Add the exact new state**

```python
class WorkflowPhase(str, Enum):
    # existing values
    AWAITING_ADJUSTMENT = "awaiting_adjustment"

@dataclass(frozen=True, slots=True)
class AwaitingAdjustmentState(WorkflowState):
    TYPE: ClassVar[str] = "awaiting_adjustment"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.AWAITING_ADJUSTMENT
    meta: StateMeta
    draft: CommercialDraft
    summary: SummaryPresented
    decision: ConfirmationRecord

    def __post_init__(self) -> None:
        _validate_summary_binding(self.draft, self.summary)
        if (
            self.decision.decision is not ConfirmationDecisionKind.ADJUST
            or self.decision.target_draft_version != self.draft.version
            or self.decision.subject_signature != self.draft.subject_signature
            or self.decision.decided_at <= self.summary.presented_at
        ):
            raise ValueError("adjustment decision does not bind to presented draft")
```

Add it to `State`, `STATE_TYPES`, `__all__`, consistency timestamps and serializer
samples. The serializer remains envelope version 1 because existing tag shapes
are unchanged and the new tag is additive in this pre-live repository.

- [ ] **Step 4: Change reducer behavior fail-closed**

`ADJUST` returns `AwaitingAdjustmentState`; `AMBIGUOUS` remains awaiting. Register
`DraftAdjusted` for ready/awaiting/awaiting-adjustment. Build the candidate draft
before returning, and reject if signature did not change:

```python
if draft.subject_signature == state.draft.subject_signature:
    return _Decision(
        state=state,
        status=TransitionStatus.REJECTED,
        reason="adjustment_did_not_change_subject",
    )
```

Do not register `ConfirmationReceived` on `AwaitingAdjustmentState`.

- [ ] **Step 5: Update property generator and matrix**

Guided `AwaitingAdjustmentState` emits a semantic `DraftAdjusted`. Arbitrary
universe remains all 12 events. Regenerate:

```bash
python3 scripts/generate_phase2_matrix.py \
  --write docs/refactor/domain/phase2-state-event-matrix.md \
  --manifest docs/refactor/evidence/phase-02/domain-manifest.json \
  >/tmp/phase4-phase2-matrix.txt
```

Expected: 16 states, 12 events, 192 pairs.

- [ ] **Step 6: Run GREEN and Phase 2 smoke**

```bash
python3 -m unittest tests.test_phase4_adjustment_state -v
python3 -m unittest tests.test_phase2_domain tests.test_phase2_serialization tests.test_phase2_properties -q
python3 scripts/run_phase2_properties.py \
  --sequences 2000 --max-events 20 --seed 20260718 --smoke >/tmp/phase2-phase4-smoke.json
```

- [ ] **Step 7: Commit**

```bash
git add reservation_domain tests docs/refactor/domain \
  docs/refactor/evidence/phase-02/domain-manifest.json \
  docs/refactor/evidence/phase-04/red-result-adjustment.json
git commit -m "fix(phase-4): disarm stale summaries on adjustment"
```

---

### Task 5: Trusted binding e full-flow replays

**Files:**
- Create: `reservation_confirmation/binding.py`
- Create: `tests/test_phase4_replays.py`
- Create: `docs/refactor/evidence/phase-04/red-result-replays.json`
- Modify: `reservation_confirmation/__init__.py`
- Modify: `reservation_confirmation/README.md`

**Interfaces:**
- Consumes: `AwaitingConfirmationState`, classifier Protocol, corpus, Phase 3 adapters.
- Produces: `classification_context`, `classify_and_bind`, `BoundConfirmation`.

- [ ] **Step 1: Write RED binding tests**

Assert the public signature has no target arguments and run a flow from
`new_workflow` through a fixture transport, renderer and reducer. Required
checks:

```python
params = set(inspect.signature(classify_and_bind).parameters)
self.assertNotIn("target_draft_version", params)
self.assertNotIn("subject_signature", params)

bound = classify_and_bind(
    awaiting,
    source_event_id="inbound:confirm",
    received_at=awaiting.summary.presented_at + timedelta(seconds=1),
    text="Pode fazer.",
    locale=SummaryLocale.PT_BR,
    content_hash=prepared.rendered.content_hash,
    classifier=ReferenceConfirmationClassifier(),
)
transition = reduce(awaiting, bound.event)
self.assertEqual(len(transition.commands), 1)
```

Test no state, wrong hash, same timestamp, stale summary, raising classifier,
negative, ambiguous, adjust, duplicate source event and conflicting duplicate.

- [ ] **Step 2: Run RED and record it**

Expected failure: missing `classify_and_bind`.

- [ ] **Step 3: Implement trusted binding**

`classification_context` copies only persisted state fields plus locale/hash.
`classify_and_bind` constructs input, calls `classify_safely`, and only emits a
domain event when exact state/hash/time invariants pass. Event targets are copied
from state:

```python
event = ConfirmationReceived(
    event_id=_decision_id("event", state, source_event_id),
    occurred_at=received_at,
    confirmation_event_id=_decision_id(
        "confirmation", state, source_event_id, candidate.decision.value
    ),
    decision=candidate.decision,
    target_draft_version=state.draft.version,
    subject_signature=state.draft.subject_signature,
)
```

Context failure returns an ambiguous candidate and `event=None`; caller cannot
feed it to reducer as authorization.

- [ ] **Step 4: Implement six replay families**

For each corpus category and both locales, build full state from empty using
in-memory Cloudbeds/Bókun fixture transport. Assert command count and resulting
state. `ADJUST` must go to `AwaitingAdjustmentState`; then provide typed terms,
render version 2 and confirm it.

- [ ] **Step 5: Run GREEN and incident regressions**

```bash
python3 -m unittest tests.test_phase4_replays -v
python3 -m characterization.harness >/tmp/phase4-characterization.json
python3 -m unittest tests.test_phase4_classifier tests.test_phase4_renderer \
  tests.test_phase4_adjustment_state -q
```

F01/F02/F08 accepted violations remain characterized as legacy failures; the new
replays assert no equivalent violation.

- [ ] **Step 6: Commit**

```bash
git add reservation_confirmation tests/test_phase4_replays.py \
  docs/refactor/evidence/phase-04/red-result-replays.json
git commit -m "feat(phase-4): bind natural decisions to current summaries"
```

---

### Task 6: Property gate de 50 mil casos

**Files:**
- Create: `reservation_confirmation/properties.py`
- Create: `tests/test_phase4_properties.py`
- Create: `scripts/run_phase4_properties.py`
- Create: `docs/refactor/evidence/phase-04/red-result-properties.json`
- Generate: `docs/refactor/evidence/phase-04/property-result.json`
- Generate: `docs/refactor/evidence/phase-04/performance-result.json`
- Modify: `reservation_confirmation/__init__.py`

**Interfaces:**
- Produces: `Phase4PropertyReport`, `run_phase4_properties(*, cases, seed)`.

- [ ] **Step 1: Write RED property tests**

```python
report = run_phase4_properties(cases=500, seed=20260719)
self.assertEqual(report.cases, 500)
self.assertEqual(report.false_commands, 0)
self.assertEqual(report.missing_required_commands, 0)
self.assertEqual(report.unexpected_exceptions, 0)
for field in REQUIRED_POSITIVE_COUNTERS:
    self.assertGreater(getattr(report, field), 0, field)
```

Patch classifier/binder/reducer independently to ensure the oracle detects:
missing required command, context-free accept, stale accept, adjustment not
disarmed and duplicate command.

CLI test: `--cases 1` fails in gate mode and passes only with `--smoke`.

- [ ] **Step 2: Run RED and record it**

Expected failure: missing `run_phase4_properties`.

- [ ] **Step 3: Implement an oracle independent of implementation shortcuts**

Each case rotates provider, locale and six category families. Baseline uses the
actual adapters, renderer, presentation, binder and reducer. The oracle computes
expected authorization from category/context/time/version independently and
compares exact command count/state.

Required report fields:

```python
@dataclass(frozen=True, slots=True)
class Phase4PropertyReport:
    cases: int
    seed: int
    cloudbeds_cases: int
    bokun_cases: int
    pt_cases: int
    en_cases: int
    explicit_cases: int
    colloquial_cases: int
    contextual_cases: int
    negative_cases: int
    ambiguous_cases: int
    adjust_cases: int
    deterministic_summaries: int
    private_field_safe_summaries: int
    posterior_accept_commands: int
    same_time_rejections: int
    stale_version_rejections: int
    context_free_rejections: int
    adjustment_disarms: int
    semantic_version_increments: int
    noop_adjustment_rejections: int
    duplicate_zero_additional: int
    classifier_error_rejections: int
    false_commands: int
    missing_required_commands: int
    unexpected_exceptions: int
    violations: tuple[str, ...]
```

- [ ] **Step 4: Implement CLI workload enforcement**

`run_phase4_properties.py` defaults to 50.000/seed `20260719`. It exits 2 for
smaller gate workload, 1 for violations/counter zeros, 0 only for full success.
`--write` uses atomic temp+replace in the script layer, never package production.

- [ ] **Step 5: Run smoke GREEN**

```bash
python3 -m unittest tests.test_phase4_properties -v
python3 scripts/run_phase4_properties.py --cases 2000 --seed 20260719 --smoke \
  >/tmp/phase4-property-smoke.json
```

- [ ] **Step 6: Run the official measured gate**

Run 50.000 cases in a subprocess, measure monotonic elapsed/RSS/exit code and
write `property-result.json` plus `performance-result.json`. Expected exit 0,
zero violations and every required counter positive.

- [ ] **Step 7: Commit**

```bash
git add reservation_confirmation scripts/run_phase4_properties.py \
  tests/test_phase4_properties.py docs/refactor/evidence/phase-04
git commit -m "test(phase-4): add full-flow summary confirmation properties"
```

---

### Task 7: Mutation runner, manifests, validador e CI

**Files:**
- Create: `tests/test_phase4_mutation_runner.py`
- Create: `scripts/run_phase4_mutations.py`
- Create: `scripts/generate_phase4_manifest.py`
- Create: `scripts/validate_phase4.py`
- Create: `.github/workflows/phase4.yml`
- Create: `docs/refactor/evidence/phase-04/README.md`
- Create: `docs/refactor/evidence/phase-04/adversarial-review.md`
- Create: `docs/refactor/evidence/phase-04/source-map.json`
- Generate: `confirmation-manifest.json`, `fixture-manifest.json`,
  `mutation-result.json`, `validation-result.json`, `SHA256SUMS`.
- Modify: `docs/refactor/06-risk-register.md`
- Modify: historical checksums only for files intentionally changed and still
  protected by prior phases.

**Interfaces:**
- Mutation catalog is an immutable tuple of exact source replacement + targeted
  unittest.
- Validador returns one JSON object with `status`, `failures` and gate summaries.

- [ ] **Step 1: Write RED mutation-runner integrity tests**

Assert catalog names unique, targets exist, replacements unique, tests exist,
original repository hash is unchanged after run, survivor/timeout/stale target
fail, and output catalog equals evidence JSON.

- [ ] **Step 2: Implement at least 15 closed mutants**

Include every mutant listed in the spec. Execute each in a fresh temporary copy
with environment cleaned of `GIT_DIR/GIT_WORK_TREE`; timeout 30 seconds per
mutant; non-zero targeted test means killed. Never mutate the source tree.

- [ ] **Step 3: Generate manifests**

`confirmation-manifest.json` covers every `.py`/README in
`reservation_confirmation`; fixture manifest covers exact corpus files and
category/locale counts; SHA256SUMS covers code, tests, scripts, spec, phase page,
source map and evidence except itself/validation bootstrap.

- [ ] **Step 4: Implement validator**

Checks:

- required files tracked/staged;
- AST purity (no network/env/fs/subprocess/provider/legacy/LLM imports in package);
- exact Protocol and constructor ownership;
- no private IDs/claim phrases in every rendered fixture flow;
- corpus exact schema and synthetic marker;
- properties 50.000, seed, counters, zero violations;
- mutation evidence equals executable catalog;
- manifests/checksums current;
- Phase 2 matrix 16×12 and evidence current;
- validators 0–3 `ok`;
- workflow contains unit/property/mutation/manifest/validation gates;
- relative links, secret/PII scans and source-map hashes.

- [ ] **Step 5: Implement CI**

`.github/workflows/phase4.yml` sequence:

```yaml
- run: python3 -m unittest discover -s tests -v
- run: python3 scripts/run_phase4_properties.py --cases 50000 --seed 20260719 --write docs/refactor/evidence/phase-04/property-result.json
- run: git diff --exit-code -- docs/refactor/evidence/phase-04/property-result.json
- run: python3 scripts/run_phase4_mutations.py --write docs/refactor/evidence/phase-04/mutation-result.json
- run: git diff --exit-code -- docs/refactor/evidence/phase-04/mutation-result.json
- run: python3 scripts/generate_phase4_manifest.py
- run: git diff --exit-code -- docs/refactor/evidence/phase-04/confirmation-manifest.json docs/refactor/evidence/phase-04/fixture-manifest.json
- run: python3 scripts/validate_phase4.py
- run: git diff --check
```

Timeout must exceed measured Phase 2 + Phase 4 gates with margin.

- [ ] **Step 6: Run mutation/validation GREEN**

```bash
python3 -m unittest tests.test_phase4_mutation_runner -v
python3 scripts/run_phase4_mutations.py \
  --write docs/refactor/evidence/phase-04/mutation-result.json
python3 scripts/generate_phase4_manifest.py
python3 scripts/validate_phase4.py
```

Expected: all mutants killed, validator `status=ok`.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/phase4.yml scripts tests \
  docs/refactor/evidence docs/refactor/06-risk-register.md
git commit -m "test(phase-4): add reproducible summary confirmation gates"
```

---

### Task 8: Regressão integral, revisão e closeout

**Files:**
- Modify: `README.md`
- Modify: `docs/refactor/README.md`
- Modify: `docs/refactor/evidence/README.md`
- Modify: `docs/refactor/phases/phase-04-single-summary-and-confirmation.md`
- Modify: `docs/refactor/evidence/phase-04/validation-result.json`
- Regenerate: Phase 2 performance/property evidence and affected checksums.

**Interfaces:** None new; this task proves and publishes the phase.

- [ ] **Step 1: Run official Phase 2 regression after final domain edits**

```bash
python3 scripts/run_phase2_properties.py \
  --sequences 100000 --max-events 20 --seed 20260718 \
  --write docs/refactor/evidence/phase-02/property-result.json
```

Measure elapsed/RSS and update Phase 2 performance hashes. Regenerate matrix,
manifest and only checksums of intentionally changed protected files.

- [ ] **Step 2: Run full fresh verification**

```bash
python3 scripts/validate_phase0.py
PHASE1_LEGACY_SOURCE=/path-not-present-in-ci python3 scripts/validate_phase1.py
python3 scripts/validate_phase2.py
python3 scripts/validate_phase3.py
python3 scripts/validate_phase4.py
python3 -m unittest discover -s tests -v
python3 -m compileall -q reservation_domain reservation_lookup reservation_confirmation characterization scripts tests
git diff --check
git diff --cached --check
```

Also AST-scan capabilities, secret/PII-scan fixtures and compare legacy HEAD plus
`git status --short -z` fingerprint.

- [ ] **Step 3: Perform adversarial read-only review**

Review three independent concerns, with no edits by reviewers:

1. renderer/private field/claim safety;
2. classifier/binder/target/version authorization;
3. properties/mutations/manifests/CI false-green resistance.

Reproduce every material finding as RED before fixing. Timeouts/no-summary are not
positive evidence.

- [ ] **Step 4: Publish implementation commit**

After all fresh local gates:

```bash
git add .
git diff --cached --check
git commit -m "feat(phase-4): enforce one summary and confirmation"
git push origin main
git fetch origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/main | cut -f1)"
```

Poll GitHub Actions by exact implementation SHA; all phase-0..phase-4 workflows
must complete `success` before closeout.

- [ ] **Step 5: Close documentation and publish closeout commit**

Mark Fase 4 concluded, no active phase, Fase 5 eligible/not started, rollout
`NO-GO`. Record implementation SHA, run IDs, exact counts/performance/mutants,
limits and no-live claims. Regenerate manifests/checksums/validation after the
last documentation edit.

```bash
git commit -m "docs(phase-4): close summary confirmation delivery"
git push origin main
git fetch origin main
```

Poll all workflows again by closeout SHA. Final proof requires clean tree and:

```text
HEAD == origin/main == ls-remote
```

- [ ] **Step 6: Stop before Fase 5**

Do not create Phase 5 files, stores, workers, schemas, deploys or runtime wiring.
Present the closeout and await explicit direction.
