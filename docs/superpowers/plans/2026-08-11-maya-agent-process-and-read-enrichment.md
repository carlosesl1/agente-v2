# Maya Agent Process and Read Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve Maya's natural answers by completing official hostel knowledge, teaching a direct read-grounded process, and enriching the existing Cloudbeds room-description DTO without adding controller complexity.

**Architecture:** Keep Maya as semantic/text owner and existing parent contracts as effect owner. Reuse `knowledge`, `room_description`, and `activity_description`; change only their content/instructions and one public descriptive field.

**Tech Stack:** Python 3.12, pytest, YAML/JSON knowledge file, existing V2 adapters and prompt.

## Global Constraints

- Starting commit: `496799ed0d8c30f9d966fdea9e9c86b546ac992e`.
- Branch: `maya-v2-agent-process-refinement`.
- Worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-agent-process-refinement`.
- Design: `docs/superpowers/specs/2026-08-11-maya-agent-process-and-read-enrichment-design.md`.
- No new tool, state field, worker, controller gate, semantic parser, keyword router, or skill.
- No provider write, outbound message, payment link, checkout, deploy, restart, or promotion.
- Bókun product descriptions remain provider-owned; no duplicated tour catalog.
- Room public names remain presentation only and never become technical identity.

---

### Task 1: Close the hostel knowledge gap

**Files:**
- Modify: `config/cerebro_faq.yaml`
- Modify: `tests/test_v2_commercial_surface.py`

**Interfaces:**
- Consumes: `FileKnowledgeTransport(Path)` and `KnowledgeReadAdapter`.
- Produces: focused sources `hostel_quarto_compartilhado`, `hostel_quarto_privativo`, and `hostel_chegada_fora_recepcao`.

- [x] **Step 1: Add failing retrieval tests**

Add a helper that executes the productive knowledge adapter and assert:

```python
shared = read_knowledge("Nunca fiquei em hostel. Como funciona esse negócio de quarto compartilhado?")
assert shared.public_payload["sources"][0] == "hostel_quarto_compartilhado"
assert "4, 6 ou 8 camas" in shared.public_payload["answer"]
assert "armários individuais" in shared.public_payload["answer"]

private = read_knowledge("Quero um quarto só para o casal. Vocês têm quarto privativo?")
assert private.public_payload["sources"][0] == "hostel_quarto_privativo"
assert "banheiro privativo" in private.public_payload["answer"]
assert "silêncio" in private.public_payload["answer"]
```

- [x] **Step 2: Witness RED**

Run:

```bash
venv/bin/python -m pytest -q tests/test_v2_commercial_surface.py -k 'shared_dorm or private_room'
```

Expected: FAIL because the new source IDs/facts do not exist.

- [x] **Step 3: Add only official factual entries**

Add the three entries under topic `hostel`, preserving the existing JSON-compatible YAML shape. State that room descriptions do not guarantee silence unless a specific provider description confirms it.

- [x] **Step 4: Run focused GREEN**

```bash
venv/bin/python -m pytest -q tests/test_v2_commercial_surface.py
```

Expected: PASS.

---

### Task 2: Teach the existing prompt the minimal atendimento process

**Files:**
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `tests/test_v2_luna_prompt.py`

**Interfaces:**
- Consumes: existing V8 read kinds `knowledge`, `room_description`, and `activity_description`.
- Produces: model-owned process instructions only; no runtime API.

- [x] **Step 1: Add failing prompt contract tests**

Assert exact high-salience requirements:

```python
assert "Responda primeiro à pergunta direta do lead" in PROMPT
assert "não peça permissão para fazer uma consulta" in PROMPT
assert "Nunca deduza privacidade, banheiro, silêncio" in PROMPT
assert "preserve cada duração, distância, quantidade e etapa" in PROMPT
assert "não some trechos consecutivos" in PROMPT
assert "Só prometa verificar depois" in PROMPT
assert "acomodação restrita por gênero" in PROMPT
```

- [x] **Step 2: Witness RED**

```bash
venv/bin/python -m pytest -q tests/test_v2_luna_prompt.py -k 'direct_question or room_characteristics or quantitative_segments or future_verification'
```

Expected: FAIL because the instructions are absent.

- [x] **Step 3: Add compact process instructions**

Extend `ATENDIMENTO E PROGRESSÃO` and `CONSULTAS` without changing schemas, enums, controller behavior, or effect language. Route generic hostel operation to knowledge, room-specific claims to `room_description`, and product detail claims to `activity_description`.

- [x] **Step 4: Run focused GREEN**

```bash
venv/bin/python -m pytest -q tests/test_v2_luna_prompt.py
```

Expected: PASS.

---

### Task 3: Enrich the existing Cloudbeds room description

**Files:**
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_adapters/cloudbeds.py`
- Modify: `tests/test_v2_provider_http_transports.py`
- Modify: `tests/test_v2_hermes_model_adapter.py` only if the public observation projection requires an explicit assertion.

**Interfaces:**
- Consumes: the selected `/api/v1.3/getRoomTypes` record bound to `offer_id`.
- Produces: `{"room_public_name": str, "description": str, "amenities": list[str]}` from transport and the same public fields plus `offer_id` from `CloudbedsReadAdapter`.

- [x] **Step 1: Add failing transport/adapter tests**

Add a `getRoomTypes` fixture with `roomTypeName="Suite Casal"`, a description, and amenities. Assert the transport returns `room_public_name` and the adapter exposes it.

- [x] **Step 2: Witness RED**

```bash
venv/bin/python -m pytest -q tests/test_v2_provider_http_transports.py -k room_description
```

Expected: FAIL because `room_public_name` is missing.

- [x] **Step 3: Implement exact provider projection**

Read `roomTypeName|roomName|room_type_name|name` from the already selected record. Validate it through the existing `text()` boundary. Do not derive room type, privacy, gender, quietness, or technical identity.

- [x] **Step 4: Run focused GREEN**

```bash
venv/bin/python -m pytest -q tests/test_v2_provider_http_transports.py tests/test_v2_hermes_model_adapter.py
```

Expected: PASS.

---

### Task 4: Freeze and verify the bounded candidate

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Verify all changed files.

**Interfaces:**
- Produces: one locally verified successor candidate; rollout remains blocked.

- [x] **Step 1: Run the affected gate**

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_luna_prompt.py \
  tests/test_v2_commercial_surface.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_provider_http_transports.py \
  tests/test_v2_hermes_model_adapter.py
```

- [x] **Step 2: Run static gates**

```bash
venv/bin/python -m compileall -q v2_adapters tests
venv/bin/python -m ruff check v2_adapters tests

git diff --check
```

If Ruff is not installed by project dependencies, run the repository's documented Ruff executable or install it only into the isolated venv.

- [x] **Step 3: Run the canonical full test gate once**

```bash
venv/bin/python -m pytest -q
```

- [x] **Step 4: Re-audit zero effects**

Confirm no worker or provider-write command was run, no new laboratory effect directory exists, and the historical curated root remains blocked.

- [x] **Step 5: Record exact outcome in `ACTIVE.md`**

Record commit/tree, commands, counts, open risks, and explicit NO-GO for deploy/provider effects.
