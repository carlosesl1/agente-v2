# Conversational Country Profile Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que `country_code` informado na conversa complete o perfil para reserva, mantendo nome, e-mail e telefone obrigatoriamente autenticados pelo binding ManyChat fresco.

**Architecture:** Um predicado parent-owned combina o binding privado fresco com fatos tipados já persistidos no estado. Nome, e-mail e telefone são lidos exclusivamente do binding; país pode vir do binding ou do `country_code` conversacional persistido. O mesmo predicado alimenta o wire booleano do modelo, a revisão de seleção e o repair após a leitura; o reducer mantém a última defesa antes do resumo/comando.

**Tech Stack:** Python 3.11+, dataclasses imutáveis, pytest, Ruff, SQLite boundary store.

## Global Constraints

- Não derivar país por telefone, locale, texto lexical ou default.
- Não permitir que full_name, email ou phone_e164 conversacionais substituam o binding ManyChat na reserva.
- O binding deve estar fresco (`observed_at <= now < expires_at`).
- O país deve ser um `country_code` canônico já validado pelo contrato `ModelFact`.
- Nenhuma mudança de provider, payload Cloudbeds, idempotência, confirmação contextual ou capability.
- Nenhum provider real, ManyChat write, deploy ou canário durante TDD/revisão local.

---

### Task 1: Parent-owned effective profile readiness

**Files:**
- Modify: `v2_application/conversation.py`
- Modify: `v2_application/turn_executor.py`
- Test: `tests/test_v2_conversation_reducer.py`
- Test: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Consumes: `PrivateCustomerBinding`, `ConversationProjection`, UTC `datetime`.
- Produces: `reservation_profile_ready(...) -> bool`; `CustomerFacts` com contato exclusivamente do ManyChat e país do binding ou fatos tipados.

- [x] **Step 1: Write failing reducer tests**

Adicionar casos que provem: (a) binding fresco com nome/e-mail/telefone e país conversacional permite `select`; (b) fatos conversacionais completos não substituem nome/e-mail/telefone ausentes no binding.

- [x] **Step 2: Run reducer RED**

Run: `/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_conversation_reducer.py -k 'conversation_country or authenticated_manychat_contact'`
Expected: FAIL porque o helper/política ainda não existe ou porque `_customer` ainda aceita fallback conversacional para os três campos autenticados.

- [x] **Step 3: Write failing executor test**

Executar dois turnos no store real em memória: primeiro persiste `country_code`; segundo exige `private_profile_complete is True` com binding ManyChat contact-only. Adicionar contraponto com binding sem e-mail/telefone mantendo o booleano falso.

- [x] **Step 4: Run executor RED**

Run: `/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_turn_executor.py -k 'conversation_country or authenticated_manychat_contact'`
Expected: FAIL porque o executor ainda usa `profile.complete` diretamente.

- [x] **Step 5: Implement minimal policy**

Em `conversation.py`, tornar full_name/email/phone_e164 exclusivos do binding em `_customer` e expor um predicado fechado de readiness. Em `turn_executor.py`, substituir os quatro usos de `profile.complete` pelo predicado calculado a partir do binding e da projeção persistida no início do turno.

- [x] **Step 6: Run focused GREEN**

Run: `/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_conversation_reducer.py tests/test_v2_turn_executor.py tests/test_v2_customer_collection.py tests/test_v2_hermes_model_adapter.py`
Expected: PASS.

- [x] **Step 7: Run static and blast-radius checks**

Run Ruff nos arquivos alterados, `git diff --check`, compile dos módulos e testes de confirmação/reserva/perfil relacionados.

- [x] **Step 7b: Align model prompt with the effective boundary**

Registrar explicitamente que nome/e-mail/telefone são autenticados pelo ManyChat e que o país canônico é validado do binding privado ou de fato tipado persistido; adicionar regressão que rejeita a afirmação anterior de que o país sempre veio autenticado do perfil. Exigir também que país inequívoco informado no turno atual seja emitido como `country_code` mesmo com `intent=inform`, sem inferência por idioma, locale ou telefone.

- [ ] **Step 8: Freeze and qualify**

Commitar o candidato, executar a suíte final econômica uma vez, obter revisão read-only no SHA exato, publicar a branch, aguardar CI e construir/inspecionar nova OCI imutável antes de qualquer novo canário real.
