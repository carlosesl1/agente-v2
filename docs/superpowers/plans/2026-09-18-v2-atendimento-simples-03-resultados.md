# V2 incremento 3 — resultados e comunicação

> **For agentic workers:** Use executing-plans inline, sem subagentes.

**Goal:** fornecer contexto derivado de resultados por componente, pagamentos e comunicação assíncrona sem replay de negócio.
**Architecture:** fontes atuais continuam donas: execution ledger, payment initiation, followup e public outbox. DTOs imutáveis de leitura entram em todos os frames de ModelRequest. Não criar banco/event bus paralelo.
**Tech Stack:** Python 3.12, SQLite, pytest; runner isolado.

## Limites e decomposição

Worktree `atendimento-simples-727d3625`, branch `refactor/v2-atendimento-simples-727d3625`, entrada `ab22e62`. Execução local aprovada por “Siga”. Runtime verificado; nenhum deploy, reserva, pagamento, ManyChat, estado ativo, V3, legado ou painel Ops.

3A é uma entrega funcional independente: leitura de resultados + outbox existente, transporte até o request e liberação de conversa/consultas sem nova seleção/efeito. 3B substituirá textos terminais por evento para a Maya, com geração persistida, recuperação e consolidação com follow-up. 3A não declara autoria/deduplicação de geração 3B concluída; mantém o projector atual até haver substituto completo. Revisões específicas do coordenador são incremento 4.

## 3A — ciclo causal

Owners:
- `v2_contracts/execution_context.py`: contextos imutáveis de componente, iniciação de pagamento e mensagem operacional.
- `v2_application/active_execution.py`: snapshot único do ledger, dono por command→lead; resumo atual continua derivado, jamais substitui componentes históricos.
- `v2_application/payments.py`: leitura verificada das iniciações e ofertas por payment_id; `followup.payments_for_reservation` lê a liquidação pelo comando na âncora, sem pressupor igualdade com o ID de iniciação, não um link criado.
- `v2_application/completion.py`: leitura por lead de chunks persistidos, IDs estáveis e estado de aceite; não copiar para outro armazenamento nem reenviar.
- `reservation_followup/sqlite_store.py`: leitura canônica dos workflows financeiros pela âncora do comando de reserva, preservando as identidades independentes.
- `v2_contracts/model.py`, `v2_adapters/hermes_model.py`, `v2_application/turn_executor.py`: transportar contexto nos frames normal, pós-read e confirmação.
- `v2_host/production.py`: injetar os stores existentes e resolução de dono.
- `tests/test_v2_execution_context.py` e suítes afetadas: causalidade, integração local e contratos.

- [x] RED: pacote parcial mantém referência confirmada e no_effect separado; queued/fenced/unknown não viram confirmação; iniciação ausente vs queued/completed/unknown e liquidação independente; leituras por lead sem vazamento entre identidades; outbox pendente/aceito/manual_review preserva texto e IDs após reabertura; todos frames recebem o contexto.
- [x] Implementar DTOs e projeção somente leitura. Sem heurística de texto/nome. Não inferir ausência de chamada de um fence ou ausência de pagamento de um link faltante.
- [x] Reduzir guard à mutação de workflow/efeitos: select, confirm, adjust, target e effect permanecem bloqueados no workflow já comandado; inform com fatos/passageiros/reads não é reexecução. Remover supressão de read baseada só no status, preservar fronteiras de confirmação/idempotência. Testar nenhuma nova command/relay e workflow imutável.
- [x] GREEN focal + guard de fronteiras + Ruff/compileall/diff. Diagnóstico integral comparado às sete falhas históricas, sem ocultá-las; contraprova na fonte anterior.
- [x] Revisão inline e evidência com hashes, commit local; atualizar ACTIVE com limites e próximo passo 3B.

### Runner

```bash
env -i HOME=/home/ubuntu PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH="$PWD" \
 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null \
 UV_CACHE_DIR=/home/ubuntu/.cache/uv uv run --no-project --python 3.12 \
 --with pytest --with pydantic --with PyYAML --with cryptography --with httpx \
 --with fastapi python -m pytest -q --tb=short tests/test_v2_execution_context.py
python3 scripts/check_fasttrack_boundaries.py
git diff --check
```

Aceite local não prova LLM real, entrega WhatsApp, reserva real nem GO de promoção. A ausência de linhas em uma fonte disponível significa `not_recorded` nela; fonte ausente significa `unavailable`, nunca “pago” ou “não chamado” por inferência.
