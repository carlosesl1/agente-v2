# V2 atendimento simples — Incremento 1: contrato Bókun

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline, without subagents, to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Corrigir a seleção da opção de pagamento externo do checkout e distinguir ausência de submit de rejeição do fornecedor, sem alterar a segurança transacional.

**Architecture:** Corrigir o seletor existente, não introduzir fallback para o fixture errado. Usar `normalized_status` já persistido no ledger para o motivo local conhecido, sem novo banco, nova coluna ou protocolo de erro. Manter a certeza de efeito final separada do motivo.

**Tech Stack:** Python 3.12, pytest, httpx.MockTransport, SQLite existente; Graphify AST local como apoio.

## Global Constraints

- Especificação: `docs/superpowers/specs/2026-09-18-v2-atendimento-simples-design.md`, aprovada por Carlos com “Siga” nesta conversa.
- Branch: `refactor/v2-atendimento-simples-727d3625`; worktree `/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
- Base documental `ee4dfaac875069d23cf298c9cb1267da838c315b`; fonte funcional base `8980646d5615db9ecb32c97f4579ba09080dc02a`.
- Produção, deploy, mensagens, reservas/pagamentos reais, V3, legado e alterações Maya Ops fora do escopo.
- Sem subagentes, fiscal de idioma, filtro PII, regex de intenção ou segunda autoridade semântica.
- Dados de teste sintéticos. HTTP transportado por MockTransport, não endpoints reais.
- Só avançar ao incremento de contexto depois de fechar este incremento e apresentar sua evidência. O roadmap abaixo não equivale à implementação dos incrementos seguintes.

## Mapa confirmado pelo Graphify e conferido na fonte

Grafo gerado exclusivamente dos 69 arquivos `.py` rastreados de `v2_contracts`, `v2_application`, `v2_adapters` e `v2_host`, na base documental acima. Nenhum código V3/legado, banco ou documento privado foi indexado. Custo de modelo da extração: zero.

Artefatos locais: `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/graphify-out/` (`graph.json`, `graph.html`, `GRAPH_REPORT.md`, `source-binding.json`, `HEALTH.json`). Snapshot auxiliar em `source-map/`, sem papel no runtime.

Caminho EXTRACTED:

```text
_book_activity_v2 (provider_http.py:2008)
  → _checkout_base_amount (provider_http.py:2957)
    → _external_checkout_option
_submit_body_v2 (provider_http.py:2167)
  → _external_checkout_option
```

O grafo contém 1.837 nós e 5.294 arestas após construção. Seu diagnóstico acusa 367 arestas com endpoint não extraído, 415 relações de mesmos endpoints colapsadas e 7 auto-relações. Portanto é mapa de navegação, não certificação de ausência de consumidores; fonte e testes são a prova. O subset deliberadamente não inclui os pacotes internos do kernel nem testes.

## Roadmap e remoções por incremento

A especificação atravessa subsistemas independentes. Cada incremento tem um plano causal próprio para não antecipar interfaces ainda não verificadas.

| Incremento | Arquivos/donos | Substituição e remoções | Evidência de aceite |
|---|---|---|---|
| 1 — este plano | `v2_adapters/provider_http.py`, `_provider_common.py` | Retirar leitura de `options[].allowedMethods` e colapso de `no_effect` em `rejected`. | Transporte completo e port preservam contrato/motivo. |
| 2 — contexto | `v2_contracts/model.py`, `model_wire.py`, `passengers.py`; `v2_application/private_customer_facts.py`, `passengers.py`, `turn_executor.py`; `v2_adapters/hermes_model.py` | Valores/pessoas e papéis explícitos substituem `private_customer_fact_names` e presença-only; remover instrução de usar só mensagem corrente; eliminar inferência de titular por nome em `_book_activity_v2`; não fundir pessoas por igualdade textual. | Valores atravessam request real e retomada; dado corrigido prevalece; passageiro→titular sem recolher ou copiar de outro. |
| 3 — resultados/histórico | `v2_application/active_execution.py`, `completion_projector.py`, `completion.py`, `turn_executor.py`, `private_customer_facts.py`; `v2_host/production.py`; contratos e adapter Maya | Componentes completos substituem status agregado exclusivo; retirar bloqueio por terminal; substituir `_confirmation_text`, `_terminal_failure_text`, `_payment_text` por evento para a mesma Maya e comunicação deduplicada. | Reabertura e corrida lead/conclusão preservam referência e certeza; falha de comunicação não repete operação; envio pendente não é entrega comprovada. |
| 4 — protocolos | `v2_application/turn_executor.py`, `turn_plan.py`; `v2_contracts/model.py`, `model_wire.py`, `confirmation_review.py`; `v2_adapters/hermes_model.py`; `config/v2_terra_system_prompt.txt` | Retirar flags/revisões de confirmação, seleção, progresso, recapitulação e correção de prosa; substituir `_structured_selection_review_required`, `_post_read_package_selection_review_required`, `_request_public_reply_correction`, `_maybe_progress_review` e só então retirar helpers exclusivos. | Conversas variadas com modelo real em harness de efeitos fechados; uma autoridade semântica; conteúdo de lead preservado; remoção efetiva, não flags mortas. |

A identidade de canal, confirmação, binding, idempotência, pagamentos e ledger/outbox separados ficam preservados em todos os incrementos. As nove remoções da especificação estão distribuídas nos incrementos 2–4; não são entregues pelo conserto Bókun.

## Comando de testes

Todos os comandos abaixo partem da worktree declarada. Prefixo `TEST` neste documento significa executar exatamente:

```bash
env -i HOME=/home/ubuntu PATH=/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH=/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null \
  UV_CACHE_DIR=/home/ubuntu/.cache/uv \
  uv run --no-project --python 3.12 --with pytest --with pydantic --with PyYAML \
  --with cryptography --with httpx --with fastapi python -m pytest
```

## Task 1 — Contrato nested em toda a suíte de transporte

**Files:** `tests/test_v2_bokun_write_transport.py` e `v2_adapters/provider_http.py:2906–2922`.

**Interfaces:** Mantém `_external_checkout_option(payload: object) -> Mapping[str, object] | None`; consumidores continuam `_checkout_base_amount` e `_submit_body_v2`.

- [x] Trocar os três fixtures positivos `allowedMethods` na raiz por `paymentMethods: {allowedMethods: [...]}`. Não modificar os asserts comerciais. O teste de seleção deve continuar usando primeira opção incompatível e segunda válida, com valores diferentes.
- [x] Executar RED: `TEST tests/test_v2_bokun_write_transport.py::test_bokun_v2_submit_booking_id_confirms_without_readback tests/test_v2_bokun_write_transport.py::test_submit_selects_option_that_explicitly_allows_external_payment -q`. Esperado: falha porque a opção nested não é encontrada, não erro de import.
- [x] Acrescentar regressão adversarial do seletor:

```python
@pytest.mark.parametrize('payment_methods', [None, [], {}, {'allowedMethods': None}, {'allowedMethods': 'RESERVE_FOR_EXTERNAL_PAYMENT'}, {'allowedMethods': []}, {'allowedMethods': ['CUSTOMER_FULL_PAYMENT']}])
def test_checkout_rejects_invalid_nested_methods_even_with_top_level_decoy(payment_methods):
    option = {
        'allowedMethods': ['RESERVE_FOR_EXTERNAL_PAYMENT'],
        'paymentMethods': payment_methods,
        'formattedAmount': '300.00',
    }
    assert BokunHTTPTransport._external_checkout_option({'options': [option]}) is None
```

- [x] Implementar apenas a leitura correta (sem fallback de raiz):

```python
for option in options:
    if not isinstance(option, Mapping):
        continue
    methods = option.get('paymentMethods')
    allowed = methods.get('allowedMethods') if isinstance(methods, Mapping) else None
    if isinstance(allowed, list) and 'RESERVE_FOR_EXTERNAL_PAYMENT' in allowed:
        return option
return None
```

- [x] GREEN: suíte completa `TEST tests/test_v2_bokun_write_transport.py -q`. Confirmar transporte `POST cart → GET checkout → POST submit`, uma única chamada de submit, lote multipassageiro existente preservado. Testes de limite de preço e de status ambíguo não são afrouxados.

## Task 2 — Causa anterior ao submit sem novo protocolo

**Files:** `v2_adapters/provider_http.py:2008–2010`, `v2_adapters/_provider_common.py:133–139`; `tests/test_v2_bokun_checkout_contract.py` (novo, reutiliza helpers de `test_v2_bokun_write_transport.py`); `tests/test_v2_reservations.py`.

**Interfaces:** Retorno técnico do transporte continua dict `status`; no caso delimitado de ausência de opção, acrescenta `reason='checkout_external_payment_unavailable'`. `ProviderExecutionResult` e `ExecutionOutcome` não mudam de schema. `normalized_status` recebe esse motivo fechado; `certainty` permanece `CALLED_NO_EFFECT` para booking final. Isso não significa ausência do carrinho auxiliar.

- [x] Criar teste do transporte completo que responde cart válido, checkout sem pagamento externo e falha imediatamente se receber submit. O resultado exigido é:

```python
assert result == {
    'status': 'no_effect',
    'reason': 'checkout_external_payment_unavailable',
}
assert [(r.method, r.url.path) for r in seen][-1][0] == 'GET'
assert len(seen) == 2
```

Reutilizar `_dispatch_payload`, `_checkout` e `_transport`; o cart sintético tem a mesma atividade/tarifa/data/categoria desses helpers. Incluir segunda execução de cenário válido com opções de valores diferentes: a primeira não permite pagamento externo e não pode fornecer o valor usado na autorização do submit. No cenário inválido, a presença do método somente no topo é um decoy e não autoriza submit.

- [x] Criar matriz de port com `_provider_port_permit` e `BokunReservationPort` existentes:

```python
@pytest.mark.parametrize(('response', 'expected'), [
    ({'status':'no_effect'}, 'no_effect'),
    ({'status':'rejected'}, 'rejected'),
    ({'status':'no_effect','reason':'checkout_external_payment_unavailable'}, 'checkout_external_payment_unavailable'),
    ({'status':'no_effect','reason':'untrusted-provider-text'}, 'no_effect'),
])
def test_bokun_port_preserves_no_submit_cause(response, expected):
    permit = _provider_port_permit(provider='bokun', operation='book_activity')
    def transport(operation, payload, *, idempotency_key):
        return response
    result = BokunReservationPort(transport).execute(permit)
    assert result.certainty is ProviderCertainty.CALLED_NO_EFFECT
    assert result.normalized_status == expected
    outcome = ExecutionOutcome(
        command_id=permit.command_id,
        certainty=ExecutionCertainty.CALLED_NO_EFFECT,
        normalized_status=result.normalized_status,
        provider_reference=None,
        evidence=result.evidence,
    )
    assert outcome.normalized_status == expected
```

- [x] RED: executar os novos testes; motivo ausente e classificação indevida `rejected` devem falhar.
- [x] Antes de obter `_checkout_base_amount`, quando `_external_checkout_option` é `None`, retornar o dict delimitado acima. Não adicionar retries nem reenviar carrinho.
- [x] Na normalização existente, preservar a distinção:

```python
if status in ('rejected', 'no_effect'):
    normalized_status = status
    if (
        provider == 'bokun'
        and status == 'no_effect'
        and response.get('reason') == 'checkout_external_payment_unavailable'
    ):
        normalized_status = 'checkout_external_payment_unavailable'
    return ProviderExecutionResult(
        ProviderCertainty.CALLED_NO_EFFECT,
        normalized_status,
        None,
        (evidence,),
    )
```

Não propagar texto arbitrário como status. O contrato antigo sem motivo continua legível; o histórico não é reescrito. A definição de rejeição do submit e os casos incertos já existentes permanecem.

- [x] GREEN: `TEST tests/test_v2_bokun_write_transport.py tests/test_v2_reservations.py -q`.
- [x] Provar persistência e não repetição: em `test_bokun_local_checkout_cause_survives_reopen_without_replay` de `test_v2_reservations.py`, incluir o motivo novo em um resultado `CALLED_NO_EFFECT`, fechar/reabrir SQLite temporário e verificar que o estado `FailedNoEffectState.outcome.normalized_status` preserva o código e a próxima execução não faz outra chamada. Usar a fixture/worker existente, sem banco real.

## Task 3 — Revisão e entrega do incremento

- [x] Rodar conjunto afetado:

```text
TEST tests/test_v2_bokun_checkout_contract.py tests/test_v2_bokun_write_transport.py tests/test_v2_reservations.py tests/test_v2_provider_http_transports.py tests/test_v2_active_execution.py tests/test_v2_completion_projector.py tests/test_fasttrack_boundaries.py -q
```

- [x] Rodar `python3 scripts/check_fasttrack_boundaries.py`, `git diff --check`, compileall dos dois módulos alterados e Ruff fixado `0.15.10` nos dois módulos e testes alterados. Distinguir achado preexistente de regressão, sem limpeza fora de escopo.
- [x] Revisar diff inline: não há fallback de formato inventado, retry após escrita, mudança de certeza ou política financeira. Confirmar que os testes atravessam HTTPTransport e port, não apenas helper.
- [x] Atualizar este checklist e `docs/refactor/ACTIVE.md` com comandos/resultados reais e pendências. Commit local dos arquivos nominados; não promover nem alegar validação E2E real.
- [x] Verificar worktree limpa e autoridade runtime OK, preservar artefatos locais de RED/GREEN e hashes. Relatar explicitamente que contexto completo e protocolos ainda pertencem aos incrementos seguintes.

## Execução e revisão do incremento

Revisão inline, sem subagentes. Os testes HTTP da tarefa 2 foram isolados em `tests/test_v2_bokun_checkout_contract.py`, reaproveitando os helpers existentes; a persistência usa um teste dedicado do worker Bókun, com SQLite temporário reaberto. Não houve acréscimo de arquitetura de produção.

- RED do parser: 9 falhas esperadas; GREEN do transporte: 65 aprovados.
- RED do motivo: 5 falhas esperadas; GREEN transporte/port: 119 aprovados.
- Evidência final suportada: Python 3.12.14, 152 testes afetados aprovados; Ruff 0.15.10/compileall/guard/diff check aprovados.
- Suíte inteira: 2.205 aprovados, 7 falhas históricas, 2.958 subtests aprovados; exatamente os mesmos sete node IDs reproduzidos na base anterior. A suíte inteira não é verde.
- Falhas de harness preservadas: runner inicial usou Python 3.11 por não fixar `--python`; archive de baseline sem histórico Git teve uma falha adicional. Ambos foram corrigidos e reexecutados sem alterar produto para esconder falhas.
- Registro: `docs/refactor/evidence/2026-09-18-v2-atendimento-simples-01.md`. O commit final/estado limpo e hashes ficam vinculados por `INCREMENT-1-EVIDENCE.json` na pasta externa dos logs.
- Sem push, CI remoto, E2E de modelo real ou promoção. Os incrementos 2–4 continuam pendentes.

## Cobertura e limites

Este plano cobre o item Bókun da especificação, usando o formato nested comprovado na auditoria read-only anterior. Não transforma a suíte simulada em prova de booking real e não determina novos campos da API sem evidência. O tipo de checkout enviado permanece o contrato existente `CUSTOMER_FULL_PAYMENT`; não se introduz modalidade comercial nova nesta fase.

Os cenários de contexto, ligação pessoa/papel, resultados por componente, histórico e conclusão pela Maya estão alocados no roadmap, não implementados por este incremento. Seu detalhamento causal ocorrerá sobre a fonte resultante do incremento anterior. Não é autorizado esconder essas pendências em um status global “simplificação concluída”.
