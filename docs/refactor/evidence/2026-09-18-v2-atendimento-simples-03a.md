# V2 atendimento simples — incremento 3A: resultados e comunicação no contexto

## Limite desta entrega

Implementação **local e offline**, baseada em `ab22e62e03c4dcbfed0db3329fe7260316387b1b`, na branch `refactor/v2-atendimento-simples-727d3625`. Não é deploy, qualificação conversacional com LLM real nem autorização para reservas. Runtime authority verificada antes e após o trabalho: **OK**.

Plano: `docs/superpowers/plans/2026-09-18-v2-atendimento-simples-03-resultados.md`.

## O que mudou

- O resolvedor deixa de entregar somente um resumo como `partial_failure`. O contexto contém os componentes canônicos, oferta/datas/pessoas/valor, status do ledger, certeza do outcome e referência do provider.
- O snapshot é projetado em contratos independentes do domínio, preservando a fronteira `v2_contracts`. Não existe banco de contexto adicional.
- Iniciações financeiras expõem obrigação/versão, valor, método, estado e resultado autenticado já existente. Link/instrução criada não significa liquidação. O leitor reutiliza a decodificação canônica do store, sem criar nem reconciliar pagamentos.
- Liquidação é lida pelo comando de reserva na âncora canônica do follow-up. O ID financeiro do follow-up **não é presumido igual** ao ID de iniciação; todas as obrigações correspondentes são preservadas.
- Fonte indisponível, ausência de registro e registros existentes são estados distintos. `not_called`, `called_no_effect`, `effect_confirmed` e `called_unknown` permanecem distintos; um componente enfileirado sem outcome não inventa uma certeza.
- Mensagens assíncronas persistidas chegam ao modelo com texto, autoria original, identidades estáveis, ordem de enqueue e estado do outbox. O status interno `delivered` é exposto como `accepted_by_manychat` somente com receipt canônico e hash válido: **não é confirmação de entrega ao aparelho/leitura pelo cliente**.
- Normal, pós-consulta e revisão de confirmação usam os mesmos contratos de contexto. O histórico de resultados continua acessível após o workflow corrente deixar de existir, pela identidade autenticada do comando/lead.
- Um estado de execução existente não bloqueia mais `inform`, coleta de fatos ou leituras independentes. Selecionar/confirmar/ajustar o workflow já comandado continua protegido. Alterar fatos conversacionais não reescreve o comando imutável.
- O contexto não reivindica claims, reenvia mensagens ou repete efeitos de reserva/pagamento. Estados incertos continuam incertos.

## Validação executada

Runner limpo: `env -i`, Python 3.12, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null`; dependências explícitas via `uv run --no-project`.

| Evidência | Resultado observado |
|---|---|
| Suítes focais de contexto, executor, adapters, projectors, pagamentos e composição | **271 passed** |
| Novos testes no código anterior exportado de `ab22e62` | **18 failed**, sem erro de collection; causalidade dos novos contratos/fluxos |
| Mesmos sete IDs históricos executados na base anterior | **7 failed** |
| Integral final | **2234 passed, 7 failed, 2958 subtests passed** |
| Testes de encerramento da fase 6 após regeneração | **14 passed, 33 subtests passed** |
| Verificação adicional do frame de revisão de confirmação | Um frame com dois componentes e uma mensagem pendente; replay inbound sem nova chamada de modelo e sem mudança no ledger de execução |
| Ruff 0.15.10, compilação, diff whitespace e fronteiras | **OK** |
| Selador da evidência | **OK**, arquivos-fonte vinculados por SHA-256 e logs comparados à base |
| Runtime authority | **OK**, sem deploy |

A primeira integral teve **8 failed / 2233 passed**: as sete históricas e o manifesto de pacote fase 6 desatualizado pela adição do leitor no `reservation_followup`. A falha nova **não foi ignorada**: `scripts/generate_phase6_manifest.py --write` regenerou somente o manifesto e os checksums afetados; `--check` e os testes fase 6 passaram. A segunda integral ficou apenas com as sete históricas. A rodada intermediária foi preservada.

### Falhas históricas restantes — suíte integral não verde

- `test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only`
- `test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts`
- `test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch`
- `test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path`
- `test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution`
- `test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing`
- `test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed`

## Evidência reproduzível

Diretório: `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`.

- `INCREMENT-3A-SOURCE.json`: fonte selada, incluindo manifests fase 6.
- `INCREMENT-3A-EVIDENCE.json`: SHA-256, summaries e IDs das falhas.
- `24-results-focused.txt`, `25-results-full.txt` (intermediário), `29-results-final-full.txt` (final).
- `26-results-baseline-history.txt`, `27-results-causal-base.txt`, `28-results-static.txt`.
- `30-results-phase6-manifest.json`, `31-results-phase6-focused.txt`.
- `verify_results_confirmation.py`, `32-results-confirmation.json`: cenário adicional offline, com modelo e porta de identidade simulados; a suíte de integração usa o resolver durável real com SQLite.
- `verify_results_evidence.py`: revalida os hashes atuais e resultados sem abrir estado ativo.

## Revisão inline e fronteiras

Revisão sem subagentes. Inspecionados vínculo por comando/lead, construção do DTO e wire, normal/pós-read/revisão de confirmação, resultados pendentes/incertos, hash do aceite e do resultado financeiro, composição do host e preservação do comando depois de novos fatos. Nenhum acesso real a provider/canal foi usado nos testes. Não se alteraram schema/migração, transportes de reserva, reconciliação financeira, rollout ou arquivos de runtime ativo.

## Ainda não entregue

**3B:** conclusão assíncrona iniciada pelo evento e redigida pela própria Maya, recuperação de geração/comunicação sem replay comercial e deduplicação entre notificação pendente e resposta de acompanhamento. O completion projector ainda escreve seu texto atual; agora esse texto e seu status entram no contexto da Maya, com autoria correta. Isso não deve ser anunciado como autoria assíncrona nova.

**Incremento 4:** retirada dos protocolos redundantes de revisão e qualificação conversacional. A presente evidência usa modelo/provider simulados e SQLite real, não LLM real nem reservas reais. Não há liberação GA, push, merge ou deploy implícito.
