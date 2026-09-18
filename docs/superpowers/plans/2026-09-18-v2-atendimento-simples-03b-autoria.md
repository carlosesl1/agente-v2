# V2 3B — autoria assíncrona e consolidação

> **For agentic workers:** usar executing-plans inline; sem subagentes.

**Goal:** resultados duráveis chamam a mesma Maya uma vez por comunicação persistida; recuperação não repete efeito comercial.
**Architecture:** derivar eventos do execution/payment ledger. Consumir suas identidades em `boundary_event_sources`, atomicamente com receipt e outbox já existentes. Sem banco, schema, fila ou modelo revisor novo. O projector deixa de escrever prosa. Um `CompletionTurn` interno distingue evento operacional de mensagem do cliente.
**Tech Stack:** Python 3.12, SQLite WAL, pytest isolado.

## Limites

Base `d4caf0a`; worktree/branch indicadas em ACTIVE.md. Especificação aprovada `2026-09-18-v2-atendimento-simples-design.md`, seções 107–115. Autorização atual: “Siga” após 3A. Somente código/testes locais; sem rede de providers/modelo, deploy, ManyChat real, V3, legado ou Ops UI. Falhas históricas comparadas, não ocultadas.

## Donos e interfaces

- `v2_contracts/completion.py`: `CompletionEvent` (identidade/hash, lead, tipo, comandos/pagamento, instante) e `CompletionTurn` interno. `ModelRequest.trigger` distingue `customer_message`/`operation_result`; `completion_events` contém apenas referências a fatos nos componentes.
- `v2_application/completion_projector.py`: `events()` deriva fatos novos (e ignora releases históricos já existentes); `context(lead_id)` consulta consumo e mensagens assíncronas ainda consolidáveis; `run_once` delega um atendimento ao executor. Remover `_confirmation_text`, `_terminal_failure_text`, `_payment_text`, seleção de idioma por telefone e cópia direta ao outbox.
- `reservation_boundary/completion.py`: leituras de cobertura e mensagens, cancelamento exclusivamente de todos os chunks ainda sem dispatch; IDs de consolidação vinculam o receipt anterior. Usar conexão/transação do boundary, nunca outro banco.
- `SQLiteBoundaryStore.commit_turn_v8(..., superseded_completion_turns=())`: o cancelamento faz parte da mesma transação de receipt/outbox. Qualquer chunk fenced/delivered/manual_review cancela a consolidação e gera conflito, não replay de envio.
- `V2TurnExecutor`: contexto comum completo; continuação interna só aceita resposta `inform` sem efeitos, consultas, fatos, seleção ou alteração de pessoas. Persiste a autoria via a mesma finalização/autoridade de turno. Eventos novos entram em source identities; replay compara a parte inbound original e valida o receipt completo. Eventos assíncronos não viram fala do cliente no histórico.
- `v2_adapters/hermes_model.py`: transporta trigger/eventos sem protocolo de revisão adicional. `config/v2_terra_system_prompt.txt`: explica resultados/evento e consolidação; a prosa permanece byte a byte.
- `v2_host/production.py`: instancia projector e compartilha com executor; worker existente POST_PAYMENT faz a continuação. Prioridade ao inbox pendente do mesmo lead. Nenhuma rota alternativa com texto fixo.

## Ciclos causais

### 1. Evento e autoria
- [x] RED `tests/test_v2_completion_continuation.py`: resultado parcial vira evento, sem texto; chamada recebe dois componentes, referências, pagamentos, valores e histórico; `trigger=operation_result`, um frame, prosa exata e zero command/relay. Repetição e reabertura não geram outro turno.
- [x] Implementar derivação, contrato interno, contexto e extração mecânica da finalização comum. Testar normalmente pelo `execute` e pela composição, não somente helpers.
- [x] Preservar IDs de releases antigos como evidência de já comunicado; pagamentos novos têm identidade por versão/result hash.

### 2. Recuperação/consolidação
- [x] RED: falha de modelo antes do commit mantém evento derivável; crash no commit reverte tudo; retry bem-sucedido não altera execution/payment ledger.
- [x] RED: inbound anterior à geração consome eventos junto da resposta. Inbound anterior ao dispatch cancela todos chunks pendentes da continuação e consolida no próprio turno, sem apagar receipt anterior.
- [x] RED: após fence/aceite/uncertain ou chunk parcialmente enviado, não cancelar nem marcar como não enviado. Conflito concorrente entre geração/turno/envio preserva uma autoridade de commit; processo reaberto preserva cobertura.
- [x] Implementar consumo via source identities e cancelamento CAS dentro de commit_turn_v8, usando estados/fences existentes. Contexto mostra proveniência e status do envio.

### 3. Integração/remoção e evidência
- [x] Substituir testes de prosa fixa por eventos, contexto e entrega com transporte simulado. Verificar modelos/autoridade de produção aceitam somente os dois contratos exatos.
- [x] Suítes focais, Ruff, compileall, `scripts/check_fasttrack_boundaries.py`, `git diff --check`; integral comparada a `29-results-final-full.txt` e causalidade na base imutável.
- [x] Revisão inline; selar fonte/logs; atualizar ACTIVE e evidência; commit local e autoridade verificada antes/depois. Suíte simulada não equivale a LLM real ou reservas reais.

Runner: `env -i HOME=/home/ubuntu PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH="$PWD" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null UV_CACHE_DIR=/home/ubuntu/.cache/uv uv run --no-project --python 3.12 --with pytest --with pydantic --with PyYAML --with cryptography --with httpx --with fastapi python -m pytest -q --tb=short`.

## Revisão do plano

Sem alteração de schema: cobertura usa source IDs e mensagens usam boundary public outbox. A derivação consulta snapshots canônicos; não cria store duplicado de resultados. Retry de geração anterior ao commit é permitido; após commit usa receipt e nunca chama modelo novamente. Cancelamento não deduz aceitação por timeout, não inspeciona semântica da prosa, e falha atomicamente se o envio avançou. Protocolos antigos de revisão dos turnos do cliente continuam sendo incremento 4, não são adicionados à conclusão assíncrona.
