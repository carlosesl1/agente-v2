# Plano TDD — pacote V2 e conclusão terminal

**Base:** `bf8efedaec10c269e5df1246924384e5dfcd2d8d`

## 1. Perfil por componente

- RED em `tests/test_phase2_domain.py`: filho Cloudbeds não contém campos específicos de atividade; filho Bókun preserva-os; assinaturas/identidades divergem e validam.
- GREEN em `reservation_domain/signature.py` com função pura de projeção de `CustomerFacts` por serviço.
- Regressão: testes de domínio, package confirmation, reservations e adapter Cloudbeds.

## 2. Titular + multipassageiro Bókun

- RED em testes de conversation/private profile: grupo cujo titular não é passageiro permanece incompleto sem nascimento/gênero do titular; titular que coincide com qualquer passageiro deriva somente os próprios dados.
- RED em `tests/test_v2_bokun_write_transport.py`: opção externa é escolhida por capability; body omite `activityId`/`pricingCategoryId`; titular pode corresponder à posição 2.
- GREEN em `v2_application/conversation.py`, prompt Terra e `v2_adapters/provider_http.py`.
- Regressão: passenger manifests, package confirmation, turn executor e transport Bókun.

## 3. Status terminal e entrega

- RED em novo `tests/test_v2_execution_status.py`: agregação single/package para pending, confirmed, no-effect, partial e unknown.
- RED em `tests/test_v2_turn_executor.py`: ledger terminal impede correção falsa para “em processamento” e entrega status estruturado ao modelo.
- RED em `tests/test_v2_completion_projector.py`: falha sem efeito, parcial e incerta geram um release; replay não duplica; pagamento não é criado.
- GREEN em `v2_application/active_execution.py`, `v2_application/turn_executor.py`, `v2_contracts/model.py`, `v2_adapters/hermes_model.py`, `v2_host/production.py` e `v2_application/completion_projector.py`.

## 4. Qualificação

Executar com ambiente limpo e config explícita:

```bash
env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/agente-v2/config/leads-agent.yaml \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q <suítes focais>
```

Depois: suíte canônica, Ruff, compile, diff review, `runtime_authority.py verify`.

## 5. Rollout seguro

- commit candidato limpo;
- imagem local imutável nomeada pelo SHA;
- promover somente contato isolado, preservando mounts/rollback;
- validar `/readyz`, heartbeat e sem novos efeitos;
- E2E com reserva real somente após autorização explícita nova;
- GA somente após E2E completo e reconciliação dos providers/pagamentos/entrega.
