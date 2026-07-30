# Maya V2 Bókun Multi-Passenger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir reservas Bókun autenticadas para qualquer party suportada pelo provider, com passageiros individuais, categorias adulto/criança, cotação exata e read-back fail-closed.

**Architecture:** O child produz atualizações fechadas de um manifesto de passageiros, mas o pai persiste e valida o manifesto, expõe ao modelo apenas progresso sem PII e inclui a forma completa no assunto assinado. A leitura Bókun preserva adultos/crianças e categorias privadas; o dispatch v2 e o transport provam a mesma party no carrinho, checkout, submit e read-back.

**Tech Stack:** Python 3.11+, dataclasses imutáveis, JSON canônico, SQLite/state boundary existente, httpx MockTransport, pytest, Ruff, Docker/OCI somente após qualificação local.

## Global Constraints

- Worktree: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`.
- Branch: `maya-v2-operational-readiness`.
- `/home/ubuntu/chapada-leads-hermes` é fonte somente leitura; não importar nem executar como backend V2.
- Não criar nem modificar `uv.lock`.
- Nenhum provider write, ManyChat delivery, payment effect, handoff effect, deploy ou rollout durante as tarefas de código.
- Não registrar dados reais; fixtures usam somente identidades sintéticas `.invalid`.
- Toda regra funcional começa com teste RED causal.
- O manifesto completo integra a assinatura e o payload hash; alteração exige novo comando.
- Party, manifesto, categorias, carrinho, checkout, submit e read-back devem coincidir exatamente.
- Adulto único histórico permanece compatível.
- Cada task funcional termina com `git diff --check`, guard de fronteiras aplicável e commit isolado.
- Comando pytest padrão: `env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest`.

## File Map

- `reservation_domain/types.py`: `PassengerFacts` e manifesto assinado em `CustomerFacts`.
- `reservation_domain/signature.py`: representação canônica dos passageiros.
- `reservation_domain/serialization.py`: retrocompatibilidade de wire sem manifesto.
- `v2_contracts/passengers.py` (novo): updates parciais, status público e validação de manifesto.
- `v2_contracts/model.py`: proposta v6 e marcador de progresso no request.
- `reservation_boundary/types.py`: fato privado persistido `passenger_manifest`.
- `v2_application/passengers.py` (novo): merge canônico, leitura do fato privado e conversão para domínio.
- `v2_adapters/hermes_model.py`: wire/parser v6 sem reinjetar valores pessoais.
- `v2_contracts/providers.py`: read de atividade com composição da party.
- `v2_contracts/private_offers.py`: categorias privadas condicionais.
- `v2_adapters/bokun.py`: read/resolve com adultos, crianças e categorias.
- `v2_adapters/provider_http.py`: orquestração HTTP de quote e booking.
- `v2_adapters/bokun_booking.py` (novo): helpers puros de categorias, bindings, submit e read-back.
- `v2_application/conversation.py`: seleção, coleta e retirada do handoff por quantidade.
- `v2_application/turn_executor.py`: propagação do manifesto entre frames e request sem PII.
- `v2_application/reservations.py`: dispatch v2 e validação do manifesto antes do fence.
- `config/v2_luna_system_prompt.txt`: protocolo v6 e coleta de grupos.
- `tests/test_v2_bokun_multi_passenger.py` (novo): contrato E2E mockado do transport.

---

### Task 1: Ativar a cadeia de autoridade do reparo

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Existing: `docs/superpowers/specs/2026-07-30-maya-v2-bokun-multi-passenger-design.md`
- Existing: `docs/superpowers/plans/2026-07-30-maya-v2-bokun-multi-passenger.md`

**Interfaces:**
- Consumes: spec commit `4381c74dd35c4da5208b112c91894ffa36711fd3`.
- Produces: `ACTIVE.md` apontando para esta spec/plano e `NEXT=Task 2`.

- [ ] **Step 1: Confirmar branch, worktree e ausência de lock**

Run:

```bash
test "$(git branch --show-current)" = maya-v2-operational-readiness
test "$(pwd)" = /home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout
test ! -e uv.lock
git status --short
```

Expected: somente o plano novo antes de seu commit; nenhum código funcional modificado.

- [ ] **Step 2: Atualizar autoridade e NEXT**

Inserir em `ACTIVE.md`:

```markdown
- Estado: `BOKUN_MULTI_PASSENGER_IMPLEMENTATION`
- Especificação ativa: `docs/superpowers/specs/2026-07-30-maya-v2-bokun-multi-passenger-design.md`
- Plano ativo: `docs/superpowers/plans/2026-07-30-maya-v2-bokun-multi-passenger.md`
- NEXT: `Task 2 — domínio assinado de passageiros`
- Provider writes reais: `BLOQUEADOS POR GATES INDEPENDENTES`
```

Preservar o histórico de Tasks 1–9 e adicionar uma seção de reparo, sem reescrever SHAs históricos.

- [ ] **Step 3: Validar e commitar controle**

```bash
git diff --check
git add docs/refactor/ACTIVE.md docs/superpowers/plans/2026-07-30-maya-v2-bokun-multi-passenger.md
git commit -m "docs(v2): plan multi-passenger Bokun delivery"
```

Expected: commit apenas de docs/control.

---

### Task 2: Domínio assinado de passageiros

**Files:**
- Modify: `reservation_domain/types.py:140-198`
- Modify: `reservation_domain/signature.py:65-89`
- Modify: `reservation_domain/serialization.py:47-91`
- Test: `tests/test_v2_customer_collection.py`
- Test: `tests/test_phase2_serialization.py`

**Interfaces:**
- Produces: `PassengerFacts(position: int, participant_type: str, full_name: str, birth_date: date, gender: str, country_code: str)`.
- Produces: `CustomerFacts.passengers: tuple[PassengerFacts, ...] = ()`.
- Produces: `effective_passengers(customer: CustomerFacts, party: Party) -> tuple[PassengerFacts, ...]`.

- [ ] **Step 1: Escrever testes RED de valor, retrocompatibilidade e assinatura**

Adicionar estes casos usando os construtores completos já presentes no arquivo de teste:

```python
def _passenger(position: int, kind: str, name: str) -> PassengerFacts:
    return PassengerFacts(
        position=position,
        participant_type=kind,
        full_name=name,
        birth_date=date(1990 + position, 1, 2),
        gender="f" if position % 2 else "m",
        country_code="BR",
    )


def test_customer_passengers_round_trip_and_bind_subject_signature() -> None:
    first = _passenger(1, "adult", "Pessoa Sintética Um")
    second = _passenger(2, "child", "Pessoa Sintética Dois")
    customer = CustomerFacts(
        customer_ref="profile:group",
        full_name="Pessoa Sintética Um",
        email="group@example.invalid",
        phone_e164="+5511999999999",
        country_code="BR",
        passengers=(first, second),
    )
    assert _decode_dataclass(CustomerFacts, _encode(customer)) == customer
    subject = canonical_subject(
        components=(_activity_offer(Party(1, 1)),),
        customer=customer,
        terms=EconomicTerms("stripe"),
    )
    assert subject["customer"]["passengers"][1]["participant_type"] == "child"


def test_legacy_customer_shape_keeps_identical_wire_and_signature() -> None:
    customer = _legacy_customer()
    assert "passengers" not in _encode(customer)
    subject = canonical_subject(
        components=(_activity_offer(Party(1, 0)),),
        customer=customer,
        terms=EconomicTerms(payment_method="stripe"),
    )
    assert subject["customer"].get("passengers") is None


def test_effective_passengers_requires_explicit_manifest_for_group() -> None:
    assert len(effective_passengers(_enriched_customer(), Party(1, 0))) == 1
    with pytest.raises(ValueError, match="passenger manifest"):
        effective_passengers(_enriched_customer(), Party(2, 0))
```

Também testar posições duplicadas/lacunadas, tipo inválido, contagens adulto/criança divergentes e ausência de birth/gender no fallback single.

- [ ] **Step 2: Executar RED**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_customer_collection.py tests/test_phase2_serialization.py
```

Expected: FAIL por ausência de `PassengerFacts`/`passengers`.

- [ ] **Step 3: Implementar tipos e canonicalização**

Implementar a forma fechada:

```python
@dataclass(frozen=True, slots=True)
class PassengerFacts:
    position: int
    participant_type: str
    full_name: str
    birth_date: date
    gender: str
    country_code: str

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 1:
            raise ValueError("passenger.position must be >= 1")
        if self.participant_type not in ("adult", "child"):
            raise ValueError("passenger.participant_type is invalid")
        name = " ".join(str(self.full_name or "").split())
        if not name or len(name) > 200:
            raise ValueError("passenger.full_name is invalid")
        _require_date(self.birth_date, "passenger.birth_date")
        if self.gender not in ("m", "f"):
            raise ValueError("passenger.gender must be m or f")
        country = str(self.country_code or "").strip().upper()
        if not _COUNTRY_RE.fullmatch(country):
            raise ValueError("passenger.country_code must be ISO alpha-2")
        object.__setattr__(self, "full_name", name)
        object.__setattr__(self, "country_code", country)
```

Adicionar `passengers=()` a `CustomerFacts`, validar ordem/posições, omitir campo vazio em `_encode`, aceitar omissão em `_decode_dataclass`, e incluir passageiros completos em `canonical_subject` somente quando não vazios.

`effective_passengers` deriva uma entrada adulta single dos dados do titular; fora disso exige manifesto explícito e valida contagem/tipos contra `Party`.

- [ ] **Step 4: Executar GREEN e regressão de domínio**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_customer_collection.py tests/test_phase2_serialization.py tests/test_phase2_domain.py tests/test_phase4_renderer.py
python -m compileall -q reservation_domain
```

Expected: todos passam.

- [ ] **Step 5: Guard e commit**

```bash
git diff --check
python scripts/check_fasttrack_boundaries.py
git add reservation_domain tests/test_v2_customer_collection.py tests/test_phase2_serialization.py
git commit -m "feat(v2): bind passenger manifests to reservation subjects"
```

---

### Task 3: Protocolo v6 e manifesto parcial privado

**Files:**
- Create: `v2_contracts/passengers.py`
- Create: `v2_application/passengers.py`
- Modify: `v2_contracts/model.py`
- Modify: `reservation_boundary/types.py`
- Modify: `v2_adapters/hermes_model.py`
- Test: `tests/test_v2_hermes_model_adapter.py`
- Test: `tests/test_v2_turn_executor.py`
- Test: `tests/test_phase8_boundary_roundtrip.py`

**Interfaces:**
- Produces: `PassengerInput` com campos opcionais e posição/tipo obrigatórios.
- Produces: `PassengerManifestStatus(required_adults, required_children, complete_positions, missing_by_position)`.
- Produces: `merge_manifest(existing_json, updates, party) -> str`.
- Produces: `complete_manifest(manifest_json, party) -> tuple[PassengerFacts, ...] | None`.
- Extends: `ModelProposal.passengers: tuple[PassengerInput, ...] = ()`.
- Extends: `ModelRequest.passenger_manifest_status: PassengerManifestStatus | None = None`.

- [ ] **Step 1: Escrever RED para parser v6, merge e privacidade**

Cobrir:

```python
def test_v6_parser_accepts_partial_passenger_updates() -> None:
    payload = _proposal_payload(schema="v2-model-proposal-v6")
    payload["passengers"] = [{
        "position": 2,
        "participant_type": "child",
        "full_name": "Pessoa Dois",
        "birth_date": None,
        "gender": None,
        "country_code": "BR",
    }]
    proposal = _proposal(json.dumps(payload).encode(), payload["source_event_id"])
    assert proposal.passengers[0].position == 2


def test_model_request_exposes_only_manifest_progress() -> None:
    status = PassengerManifestStatus(2, 1, (1,), ((2, ("birth_date",)), (3, ("full_name",))))
    request = ModelRequest(
        request_id="model-request:manifest-status",
        lead_id="lead:synthetic",
        source_event_id="event:synthetic",
        message="Dados enviados.",
        locale="pt-BR",
        state_version=3,
        passenger_manifest_status=status,
    )
    wire = json.loads(_request_wire(request, "prompt"))
    serialized = wire["messages"][0][1]
    assert "Pessoa Privada" not in serialized
    assert json.loads(serialized)["passenger_manifest_status"]["required_children"] == 1


def test_manifest_merge_is_canonical_and_conflicts_fail_closed() -> None:
    original = PassengerInput(
        position=1,
        participant_type="adult",
        full_name="Pessoa Um",
        birth_date=date(1991, 1, 2),
        gender="f",
        country_code="BR",
    )
    first = merge_manifest(None, (original,), Party(2, 0))
    assert merge_manifest(first, (), Party(2, 0)) == first
    with pytest.raises(PassengerManifestConflict):
        merge_manifest(
            first,
            (
                PassengerInput(
                    position=1,
                    participant_type="adult",
                    full_name="Outra Pessoa",
                    birth_date=None,
                    gender=None,
                    country_code=None,
                ),
            ),
            Party(2, 0),
        )
```

Testar também que `passenger_manifest` faz round-trip na boundary, mas `_state_model_facts()` o omite.

- [ ] **Step 2: Executar RED**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_hermes_model_adapter.py tests/test_v2_turn_executor.py tests/test_phase8_boundary_roundtrip.py
```

Expected: FAIL por schema/tipos ausentes.

- [ ] **Step 3: Implementar contratos fechados**

Em `v2_contracts/passengers.py`, usar dataclasses `frozen=True, slots=True`; normalizar datas ISO no parser e emitir JSON canônico com chaves exatas. `merge_manifest` deve preservar valores existentes, preencher apenas `None`, rejeitar conflito e descartar posições fora da party nova.

Adicionar `passenger_manifest` como `StringSlot` fechado na boundary. Em `v2_application/passengers.py`, encapsular parse/merge/status para que `conversation.py` não manipule JSON diretamente.

No adapter:

```python
_RESPONSE_FIELDS_V6 = frozenset((*_RESPONSE_FIELDS_V5, "passengers"))
```

Schemas v1–v5 produzem `passengers=()`. v6 exige a chave. `_request_wire` serializa somente `PassengerManifestStatus`, nunca o fato `passenger_manifest`.

- [ ] **Step 4: GREEN e regressão de wire**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_hermes_model_adapter.py tests/test_v2_turn_executor.py tests/test_phase8_boundary_roundtrip.py tests/test_v2_customer_collection.py
python -m compileall -q v2_contracts v2_application reservation_boundary v2_adapters/hermes_model.py
```

- [ ] **Step 5: Guard e commit**

```bash
git diff --check
python scripts/check_fasttrack_boundaries.py
git add v2_contracts/passengers.py v2_application/passengers.py v2_contracts/model.py reservation_boundary/types.py v2_adapters/hermes_model.py tests
git commit -m "feat(v2): persist closed passenger manifest updates"
```

---

### Task 4: Reads Bókun por composição adulto/criança

**Files:**
- Modify: `v2_contracts/providers.py:64-217`
- Modify: `v2_contracts/private_offers.py`
- Modify: `v2_adapters/bokun.py`
- Modify: `v2_adapters/provider_http.py:620-845,1072-1110,1340-1369`
- Test: `tests/test_v2_model_contracts.py`
- Test: `tests/test_v2_reads.py`
- Test: `tests/test_v2_provider_http_transports.py`

**Interfaces:**
- New activity read invariant: `participants == adults + children` when party fields are present.
- Produces private fields: `adult_pricing_category_id`; conditional `child_pricing_category_id`.
- Produces public activity payload with `adults`, `children`, `participants`.

- [ ] **Step 1: RED para hash da party, preço por categoria e categoria ausente**

Adicionar testes que provem:

```python
adult_child = ReadRequest(
    request_id="read:mixed",
    kind=ReadKind.ACTIVITY,
    product_id="product:tour-4ps",
    activity_date=date(2026, 11, 18),
    adults=2,
    children=1,
    participants=3,
)
assert adult_child.query_hash() != replace(adult_child, adults=3, children=0).query_hash()
```

Mockar metadata com categorias `ADULT` e `CHILD`, rate com valores `300` e `150`, e verificar total base `750.00`, três entradas no quote cart e binding privado com ambos os IDs. Um mix com criança sem categoria CHILD deve retornar `available=False` e não criar quote cart.

- [ ] **Step 2: Executar RED**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_model_contracts.py tests/test_v2_reads.py tests/test_v2_provider_http_transports.py
```

- [ ] **Step 3: Implementar leitura party-aware**

No `ReadRequest`, aceitar a forma histórica `participants`-only para replay; novos builders fornecem os três campos. Quando `adults`/`children` aparecerem, ambos são obrigatórios e a soma precisa coincidir.

No HTTP transport, substituir `_participant_total(item, participants)` por:

```python
def _party_total(item, *, category_ids: dict[str, str], adults: int, children: int) -> Decimal | None:
    amounts = _amount_by_category(item)
    adult = amounts.get(category_ids["adult"])
    child = amounts.get(category_ids.get("child", ""))
    if adult is None or (children and child is None):
        return None
    return adult * adults + (child or Decimal("0")) * children
```

Derivar categorias cruzando metadata/rate e falhar fechado para criança sem categoria. Quote cart repete ID adulto e infantil conforme a party; `_validate_quote_cart` compara o multiconjunto.

No resolver privado, usar `query.adults + query.children`, não `query.adults`, e exigir campos condicionais conforme `query.children`.

- [ ] **Step 4: GREEN e regressão de reads**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_model_contracts.py tests/test_v2_reads.py tests/test_v2_provider_http_transports.py tests/test_v2_private_offer_bindings.py
```

- [ ] **Step 5: Guard e commit**

```bash
git diff --check
python scripts/check_fasttrack_boundaries.py
git add v2_contracts/providers.py v2_contracts/private_offers.py v2_adapters/bokun.py v2_adapters/provider_http.py tests
git commit -m "feat(v2): quote Bokun parties by pricing category"
```

---

### Task 5: Coleta e seleção de grupos no reducer/turn executor

**Files:**
- Modify: `v2_application/conversation.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `config/v2_luna_system_prompt.txt`
- Test: `tests/test_v2_conversation_reducer.py`
- Test: `tests/test_v2_turn_executor.py`
- Test: `tests/test_v2_luna_prompt.py`

**Interfaces:**
- Consumes: helpers de `v2_application.passengers`.
- Produces: `_customer(projection, profile, service).passengers` completo para activity/package.
- Removes: handoff automático exclusivamente por `party > 1`.

- [ ] **Step 1: RED para grupo completo, incompleto e handoff independente**

Casos obrigatórios:

```python
def test_group_party_with_complete_manifest_can_prepare_summary() -> None:
    decision = _reduce_group_case(
        party=Party(2, 0),
        manifest=_complete_synthetic_manifest(Party(2, 0)),
        intent="select",
    )
    assert decision.public_reply.kind == "summary"
    assert decision.handoff_request is None
    assert decision.next_state.workflow.draft.customer.passengers[1].position == 2


def test_group_party_with_incomplete_manifest_emits_no_command_or_handoff() -> None:
    decision = _reduce_group_case(
        party=Party(2, 0),
        manifest=_partial_synthetic_manifest(Party(2, 0)),
        intent="select",
    )
    assert decision.commands == ()
    assert decision.handoff_request is None
    assert decision.public_reply.kind == "profile_completion"


def test_discount_still_routes_complete_group_to_handoff() -> None:
    decision = _reduce_group_case(
        party=Party(2, 0),
        manifest=_complete_synthetic_manifest(Party(2, 0)),
        intent="request_handoff",
        handoff_reason=HandoffReasonCode.DISCOUNT_REQUESTED,
    )
    assert decision.handoff_request.reason_code is HandoffReasonCode.DISCOUNT_REQUESTED
```

Adicionar package misto e mudança de party que invalida manifesto/proposta.

- [ ] **Step 2: Executar RED**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_conversation_reducer.py tests/test_v2_turn_executor.py tests/test_v2_luna_prompt.py
```

- [ ] **Step 3: Implementar merge antes da redução e readiness do manifesto**

No executor, mesclar `first_proposal.passengers` antes de decidir selection review; carregar o status no primeiro e segundo `ModelRequest`; propagar updates do primeiro frame ao segundo sem pedir que o child repita PII.

Substituir checks `adults + children == 1` por party positiva + `complete_manifest(manifest_json, party) is not None`. Builders de read passam `adults`, `children`, `participants`.

Retirar `_unsupported_activity_party` e a reply de grupo. Preservar precedência de `request_handoff` e safety. Para `select` incompleto, emitir resposta determinística de campos faltantes com zero comandos.

Atualizar prompt para v6, chave `passengers`, coleta por posição e remoção da regra “transporte suporta exatamente uma pessoa”. Manter criança/restrição de segurança como regra independente, sem handoff só pela quantidade.

- [ ] **Step 4: GREEN e regressões anti-duplicação**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_conversation_reducer.py tests/test_v2_turn_executor.py tests/test_v2_luna_prompt.py tests/test_v2_critical_actions.py tests/test_v2_customer_collection.py
```

- [ ] **Step 5: Guard e commit**

```bash
git diff --check
python scripts/check_fasttrack_boundaries.py
git add v2_application/conversation.py v2_application/turn_executor.py config/v2_luna_system_prompt.txt tests
git commit -m "feat(v2): collect and authorize activity groups"
```

---

### Task 6: Dispatch Bókun v2 e fence ligado ao manifesto

**Files:**
- Modify: `v2_application/reservations.py`
- Modify: `v2_contracts/private_offers.py`
- Test: `tests/test_v2_reservations.py`
- Test: `tests/test_v2_private_offer_bindings.py`

**Interfaces:**
- Produces: `v2-reservation-dispatch-v2` para grupo/criança.
- Preserves: dispatch v1 para adulto único histórico.
- Requires: `effective_passengers(customer, component.party)` antes do fence.

- [ ] **Step 1: RED para dispatch, hash e binding condicional**

Testar que dois adultos produzem:

```python
payload = json.loads(_provider_payload(command, "bokun", private_binding))
assert payload["schema"] == "v2-reservation-dispatch-v2"
assert [p["position"] for p in payload["passengers"]] == [1, 2]
assert payload["offer"]["party"] == {"adults": 2, "children": 0}
```

Alterar nascimento do passageiro 2 deve mudar `subject_signature`, `command_id`, `idempotency_key` e `payload_hash`. Manifesto incompleto deve gerar `PreparationFailure("booking_profile_incomplete", False, ())` antes do resolver/provider.

- [ ] **Step 2: Executar RED**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_reservations.py tests/test_v2_private_offer_bindings.py
```

- [ ] **Step 3: Implementar dispatch fechado**

Em `prepare`, substituir a contagem canary por `effective_passengers`. Validar private fields dinamicamente:

```python
def _expected_bokun_private_fields(*, children: int) -> frozenset[str]:
    fields = {"bokun_product_id", "start_time_id", "rate_id", "adult_pricing_category_id"}
    if children:
        fields.add("child_pricing_category_id")
    return frozenset(fields)
```

Emitir v1 somente quando party `1+0`, manifesto explícito vazio e binding histórico `pricing_category_id`; emitir v2 em todos os demais casos. O array de passageiros fica no nível raiz do dispatch e usa datas ISO.

- [ ] **Step 4: GREEN e package allocator**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_reservations.py tests/test_v2_private_offer_bindings.py tests/test_phase7_package.py -k 'not wheel'
```

Expected: testes ativos passam; baseline histórica documentada permanece excluída.

- [ ] **Step 5: Guard e commit**

```bash
git diff --check
python scripts/check_fasttrack_boundaries.py
git add v2_application/reservations.py v2_contracts/private_offers.py tests
git commit -m "feat(v2): fence multi-passenger Bokun dispatches"
```

---

### Task 7: Transport Bókun multi-passageiro e read-back exato

**Files:**
- Create: `v2_adapters/bokun_booking.py`
- Modify: `v2_adapters/provider_http.py:847-1326`
- Modify: `v2_adapters/bokun.py`
- Create: `tests/test_v2_bokun_multi_passenger.py`
- Modify: `tests/test_v2_bokun_write_transport.py`

**Interfaces:**
- Produces: `bind_cart_passengers(payload, expected) -> tuple[BoundPassenger, ...]`.
- Produces: `build_submit_body(checkout, *, main_contact, passengers) -> dict[str, object]`.
- Produces: `validate_booking_readback(payload, expected) -> str`.
- Preserves: callable transport contract `{status, booking_id}`.

- [ ] **Step 1: RED E2E com MockTransport para 2 adultos e 1 criança**

O handler deve afirmar:

```python
assert cart_body["pricingCategoryBookings"] == [
    {"pricingCategoryId": "adult-cat"},
    {"pricingCategoryId": "adult-cat"},
    {"pricingCategoryId": "child-cat"},
]
assert [p["bookingId"] for p in submit_body["shoppingCart"]["bookingAnswers"]["activityBookings"][0]["passengers"]] == [
    "passenger-1", "passenger-2", "passenger-3"
]
```

Retornar checkout com três grupos de perguntas e read-back com mesmo produto/data/três category bookings. Verificar `status=confirmed` e uma única chamada submit.

Adicionar REDs para: booking ID duplicado, categoria divergente, grupos de perguntas a menos, pergunta obrigatória desconhecida, submit ambíguo e read-back com duas pessoas.

- [ ] **Step 2: Executar RED**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_bokun_multi_passenger.py tests/test_v2_bokun_write_transport.py
```

- [ ] **Step 3: Implementar helpers puros e orquestração HTTP**

Definir:

```python
@dataclass(frozen=True, slots=True)
class ExpectedPassenger:
    position: int
    category_id: str
    first_name: str
    last_name: str
    birth_date: str
    gender: str
    country_code: str

@dataclass(frozen=True, slots=True)
class BoundPassenger:
    expected: ExpectedPassenger
    booking_id: str
```

`bind_cart_passengers` valida activity única, multiconjunto de categorias e IDs distintos; liga por categoria e posição estável dentro da categoria.

`build_submit_body` percorre todos os `BoundPassenger`, responde somente IDs publicados e rejeita required desconhecido. O main contact permanece separado.

`validate_booking_readback` exige booking reference, produto, data e cardinalidade; quando categorias aparecem, compara multiconjunto.

No HTTP transport, aceitar dispatch v1 single e v2 multi. Construir cart por lista; chamar submit uma vez; executar read-back e só então retornar confirmed. Erros locais antes de HTTP são `NOT_CALLED`; divergência após cart/checkout vira resposta `rejected`/`CALLED_NO_EFFECT`; resultado de submit/read-back ambíguo propaga unknown pelo adapter existente.

- [ ] **Step 4: GREEN, certeza e idempotência**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_bokun_multi_passenger.py tests/test_v2_bokun_write_transport.py tests/test_v2_reservations.py tests/test_v2_provider_http_transports.py
```

Verificar nos testes que retry com mesma chave reproduz mesma session ID e não muda a ordem dos passageiros.

- [ ] **Step 5: Guard e commit**

```bash
git diff --check
python scripts/check_fasttrack_boundaries.py
git add v2_adapters/bokun_booking.py v2_adapters/provider_http.py v2_adapters/bokun.py tests
git commit -m "feat(v2): submit and verify Bokun passenger groups"
```

---

### Task 8: Fechamento de regressões e controle ativo

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Modify: `tests/test_v2_conversation_reducer.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_bokun_multi_passenger.py`

**Interfaces:**
- Produces: candidata local congelada com `NEXT=final review/CI`.

- [ ] **Step 1: Adicionar matriz contratual final**

Parametrizar:

```python
@pytest.mark.parametrize("adults,children", [(1, 0), (2, 0), (1, 1), (4, 2)])
def test_supported_parties_preserve_count_end_to_end(adults: int, children: int) -> None:
    result = run_mocked_group_flow(adults=adults, children=children)
    assert result.command.payload.components[0].party == Party(adults, children)
    assert len(result.dispatch["passengers"]) == adults + children
    assert result.provider_result.certainty is ProviderCertainty.EFFECT_CONFIRMED
```

Adicionar caso de alteração pós-resumo que produz zero comando e nova autoridade, e `inform`/`request_handoff` livres em estado pós-comando.

- [ ] **Step 2: Executar suíte focada cumulativa**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_customer_collection.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_model_contracts.py \
  tests/test_v2_reads.py \
  tests/test_v2_private_offer_bindings.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_reservations.py \
  tests/test_v2_bokun_write_transport.py \
  tests/test_v2_bokun_multi_passenger.py
```

Expected: todos passam, zero chamadas de rede real.

- [ ] **Step 3: Executar suíte V2 e gates estáticos uma vez**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_*.py
/home/ubuntu/chapada-leads-hermes/venv/bin/ruff check reservation_domain reservation_boundary v2_contracts v2_application v2_adapters v2_host tests
python -m compileall -q reservation_domain reservation_boundary v2_contracts v2_application v2_adapters v2_host
python scripts/check_fasttrack_boundaries.py
git diff --check
test ! -e uv.lock
```

Expected: todos exit `0`.

- [ ] **Step 4: Atualizar ACTIVE e commitar evidência de controle**

Registrar commits funcionais, comandos/exits, riscos fechados e:

```markdown
- Estado: `BOKUN_MULTI_PASSENGER_LOCAL_QUALIFIED`
- NEXT: `revisão independente no SHA exato, push, CI e nova imagem OCI por digest`
- Provider writes reais: `BLOQUEADOS POR GATES INDEPENDENTES`
```

```bash
git add docs/refactor/ACTIVE.md tests/test_v2_conversation_reducer.py tests/test_v2_turn_executor.py tests/test_v2_bokun_multi_passenger.py
git commit -m "test(v2): qualify multi-passenger Bokun contract"
```

- [ ] **Step 5: Verificar árvore final**

```bash
git status --short
git log --oneline -12
test ! -e uv.lock
```

Expected: árvore limpa; nenhum artifact/run versionado.

---

### Task 9: Revisão terminal e preparação da nova candidata

**Files:**
- Read-only review of all files changed since `4381c74`.
- Modify only if a concrete finding is reproduced with RED test.

**Interfaces:**
- Produces: `APPROVE` ou `BLOCK` para o SHA exato.
- Produces after APPROVE: pushed SHA and CI run; image/deploy remain a later operational gate.

- [ ] **Step 1: Revisar diff e invariantes**

```bash
git diff --check 4381c74..HEAD
git diff --stat 4381c74..HEAD
git status --short
test ! -e uv.lock
```

Examinar bypass de manifesto, categoria, assinatura, idempotência, read-back e handoff independente.

- [ ] **Step 2: Rodar revisão independente somente leitura**

O reviewer recebe SHA/base exatos, spec, arquivos críticos, veredito `APPROVE|BLOCK`, achados por severidade e testes realmente executados. Timeout sem resumo é inconclusivo, nunca aprovação.

- [ ] **Step 3: Tratar findings causalmente**

Para cada finding válido: teste RED no HEAD, correção mínima, suíte focada e novo commit. Repetir a revisão apenas no novo SHA. Finding histórico não bloqueia descendente sem witness causal reproduzível.

- [ ] **Step 4: Push e CI somente após APPROVE**

```bash
git push origin maya-v2-operational-readiness
```

Confirmar remoto no mesmo SHA e acompanhar jobs `test`, `image`, `gate`. Não promover imagem nem abrir gates nesta task.

- [ ] **Step 5: Relatório terminal**

Registrar SHA, run CI, testes, ausência de efeitos reais e decisão: `LOCAL/CI QUALIFIED`, nunca “100% liberada”. Nova imagem por digest, dark canary e canário real multi-passageiro exigem sequência operacional separada.
