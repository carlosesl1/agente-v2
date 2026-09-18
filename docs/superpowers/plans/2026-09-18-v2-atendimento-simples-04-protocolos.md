# Incremento 4 — remoção dos protocolos redundantes

> **For agentic workers:** executar inline com executing-plans e TDD; sem subagentes.

**Goal:** interpretar cada mensagem uma vez, com continuação apenas por resultado de ferramenta; sem revisão semântica ou promoção de intenção pelo controlador.
**Architecture:** preservar ModelProposal V8, contexto completo e kernel de efeitos. Remover flags e prompts de revisão, adapter especial de confirmação, correção pública e seleção inferida. Contratos inválidos permanecem erros explícitos, nunca autorização nem prosa substituta. Binding mecânico de escolhas, refresh de uma seleção tipada e confirmação continuam no dono atual.
**Tech Stack:** Python 3.12, SQLite real de testes, pytest isolado.

## Escopo autorizado

Carlos disse “Siga” após o resumo do incremento 4. Especificação aprovada: `docs/superpowers/specs/2026-09-18-v2-atendimento-simples-design.md`, especialmente 117–133. Base local `dec167115278fdc1764fb5380accdcec3f5f028e`; branch/worktree de ACTIVE.md. Sem deploy, LLM/provider/WhatsApp real, banco ativo, V3, legado ou Ops UI. Teste de conversa real e dívida histórica são gates posteriores separados.

## Mapa de remoção e donos

| Retirar | Substituto / invariante |
|---|---|
| `confirmation_review_required`, adapter/prompt restrito de confirmação | `pending_action` completo no request normal; V8 faz binding exato; reducer verifica prazo/escopo |
| `selection_review_required` e gates pré/pós-read | decisão normal `select` explícita com escolha ligada a observação |
| `progress_review_required`/`proposal_requires_progress_review` | resposta informativa válida aceita sem inferir insuficiência pelo formato |
| `recap_reuse_required` e sufixo morto | consultation_history já presente; Maya decide recapitular ou consultar |
| `public_reply_correction_reasons`, orçamento e helper | nenhuma chamada de reescrita; erro de contrato/efeito não transforma a intenção nem cria resposta |
| `_repair_requested_activity_selection` | Maya seleciona explicitamente no pós-tool; inform não vira select |
| normalização que descarta seleção sem consulta | derivar refresh de seleção tipada sem alterar intenção; seleção inválida falha na fronteira |
| preservação forçada da intenção anterior após tool | proposta final é da Maya; parâmetros de efeitos permanecem validados |

Compatibilidade histórica de receipts/traces continua legível; tipos sem consumidores históricos saem. Guardas mecânicas de fatos inválidos/alteração de perfil e do workflow já comandado não são removidas cegamente. Se uma seleção não é válida, não publicar confirmação inventada para mascarar erro.

## Ciclos

- [x] Baseline focal: executor, adapter, contexto, autoria e conclusão; comparar com integral 3B conhecida, sem declarar suíte global verde.
- [x] RED: adapter aceita inform/noop em uma chamada; request/wire não têm as cinco flags; pending_action usa mesmo prompt/contexto; normalização não promove inform em select; executor não revisa resposta normal quando há resumo pendente ou dados completos.
- [x] Remover helpers, ramos, campos e prompts em `v2_application/turn_executor.py`, `v2_adapters/hermes_model.py`, `v2_contracts/model.py`, `config/v2_terra_system_prompt.txt`. Continuar permitindo reparo único estritamente estrutural do V8, não revisão de prosa válida.
- [x] Atualizar testes antigos que exigiam protocolos excluídos, substituindo por invariantes equivalentes: zero efeitos não autorizados, texto exato, uma interpretação, contexto completo e leituras vinculadas. Não excluir testes de concorrência, replay ou efeitos para fazer a suíte passar.
- [x] GREEN: focais novos + suites afetadas. Revisar inline diff e imports; Ruff E9,F,I nos alterados, compile, fronteiras e diff-check.
- [x] Integral sem exclusões; comparar IDs com `40-autoria-final-full.txt`; causalidade contra base imutável; selar logs/hash e registrar limites.
- [x] Atualizar ACTIVE/evidência, commit local, verificar código comprometido/árvore limpa e autoridade do runtime.

## Testes e comandos

Runner de todos os ciclos:
`env -i HOME=/home/ubuntu PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH="$PWD" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null UV_CACHE_DIR=/home/ubuntu/.cache/uv uv run --no-project --python 3.12 --with pytest --with pydantic --with PyYAML --with cryptography --with httpx --with fastapi python -m pytest -q --tb=short`.

Novo `tests/test_v2_single_interpretation.py` deve observar `len(calls)==1`, bytes de `reply_chunks` idênticos, flags ausentes no payload, contexto persistido presente e nenhuma linha de command/relay. Os cenários pós-tool devem observar apenas request inicial + request com observação real. `scripts/check_fasttrack_boundaries.py` é obrigatório antes do commit. Não alterar scripts/gates para esconder incompatibilidades históricas.

## Revisão do plano

A remoção é local ao coordenador/contrato/adapter, não troca de framework. A continuação 3B continua usando o caminho comum. Prosa não é examinada. A segurança depende dos contratos/receipts existentes, não de modelo extra. Invalidar efeitos é diferente de editar intenção e pedir que a Maya justifique a edição. Nenhuma liberação operacional é implícita no encerramento deste plano.
