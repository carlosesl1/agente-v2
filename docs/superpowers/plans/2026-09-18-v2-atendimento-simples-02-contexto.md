# V2 atendimento simples — incremento 2: contexto e titular

> **For agentic workers:** Use executing-plans inline, sem subagentes. Este plano executa a seção 2 da especificação aprovada; nenhuma operação real/deploy.

**Objetivo:** valores persistidos chegam à Maya; passageiro/titular usa vínculo explícito, sem igualdade de nome nem recópia do passageiro para um cadastro independente.

**Base local:** 247ec4e, worktree/branch já declaradas em ACTIVE.md. Autoridade live verificada OK, independente desta fonte. Graphify pré-correção orientou os owners; fonte atual prevalece.

## Contratos e owners

- `v2_contracts/model.py`: `state_facts` passa a aceitar valores do atendimento; remover `private_customer_fact_names` e `passenger_manifest_status` do request e substituir por `passengers: tuple[PassengerInput, ...]`. Remover limite de quatro trocas; manter orçamento de bytes de contexto.
- `v2_contracts/passengers.py`: atualização tipada `is_holder: bool | None = None`: None não altera o papel, True associa, False desassocia esse passageiro. Posição é referência canônica dentro do grupo do atendimento, nunca nome. Não introduzir cadastro global de pessoas/pagador não existente.
- `v2_application/passengers.py`: manifesto v2 conserva v1 legível e adiciona is_holder. Um titular no máximo. Reatribuição retira papel anterior; correções usam adjust/revoke existente; mudanças de grupo não herdam vínculo de uma pessoa descartada. `projection_passenger_inputs` fornece valores parciais, inclusive campos ausentes, para contexto.
- `v2_application/conversation.py`: uma projeção de valores parciais serve tanto ao modelo quanto à resolução do cliente para ferramentas. Valores conversacionais prevalecem sobre perfil; pessoa explicitamente vinculada prevalece nos quatro campos do passageiro (inclusive ausência, sem completar com outra pessoa). Remover casamento por nome.
- `v2_application/private_customer_facts.py`: telefone de contato aceito como fato, sem alterar destinatário ManyChat. Migração transacional do CHECK da tabela existente, hashes antigos preservados quando telefone não existe. Histórico selecionado por orçamento de bytes, não LIMIT 4.
- `v2_application/turn_executor.py`: todos os frames recebem valores completos e passageiros atuais; telefone não é mais descartado. Mesma persistência e confirmação de efeitos. Correções de dados não podem executar confirmação antiga.
- `v2_adapters/hermes_model.py`, `v2_contracts/model_wire.py`, `config/v2_terra_system_prompt.txt`: valores e vínculo explícito no wire/prompt; remove instruções de presença e proibição de reutilização. Envelope existente, sem novo protocolo revisor. Leitura de passageiros históricos sem is_holder continua possível; schema ativo inclui o campo.

## Execução causal

- [x] Testes RED: contexto com valores persistidos/reabertura além de quatro turnos; telefone conversacional sem mudança de lead/destinatário; associação tardia do passageiro 2, nomes iguais não associam; correção preserva associação; troca/desassociação não empresta dados da pessoa anterior; contrato v1 continua legível.
- [x] Implementar manifesto/valores compartilhados, armazenamento e request/wire; remover interfaces/instruções substituídas. Testes de montagem via executor e wire, não apenas helpers.
- [x] GREEN focal, regressões relacionadas, Ruff/compileall/fronteiras; diagnóstico integral comparado às sete falhas históricas já verificadas.
- [x] Revisar diff e registrar evidência/commit local. Quantidade de testes não prova conversa de modelo real. Resultados/completion/protocolos de revisão continuam nos próximos incrementos e não são implicitamente declarados concluídos.

## Runner reproduzível

```bash
env -i HOME=/home/ubuntu PATH=/usr/local/bin:/usr/bin:/bin \
 PYTHONPATH="$PWD" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null UV_CACHE_DIR=/home/ubuntu/.cache/uv \
 uv run --no-project --python 3.12 --with pytest --with pydantic --with PyYAML \
 --with cryptography --with httpx --with fastapi python -m pytest \
 tests/test_v2_service_context.py tests/test_v2_private_customer_facts.py \
 tests/test_v2_passenger_manifest.py tests/test_v2_conversation_context.py \
 tests/test_v2_turn_executor.py tests/test_v2_hermes_model_adapter.py -q --tb=short
python3 scripts/check_fasttrack_boundaries.py
git diff --check
```

## Limites

Nenhuma edição SQLite live, ManyChat/WhatsApp, Cloudbeds/Bókun/pagamentos, deploy ou push. Sem V3, legado, deploy Ops ou mudança de UI. Única compatibilidade em `v2_ops/serialization.py`: derivar a chave factual existente do novo ModelRequest, pois remover a interface antiga sem atualizar esse consumidor quebraria a telemetria do executor. Não criar filtro de PII/idioma, regex semântica ou segundo modelo. Reservas históricas conservam seus snapshots. Não alterar a regra financeira de pacote parcial.
