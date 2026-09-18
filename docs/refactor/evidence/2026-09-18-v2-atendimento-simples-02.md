# V2 atendimento simples — evidência do incremento 2

## Decisão e limite

Implementação local de contexto reutilizável e papel de titular concluída no worktree `atendimento-simples-727d3625`, a partir do incremento 1 (`247ec4ee27150e3b6ef861b8aab9675210794133`). Não é promoção, qualificação de conversa com modelo real ou autorização de escrita externa. A simplificação integral segue incompleta: resultados por componente, continuidade assíncrona e remoção dos protocolos de revisão restantes pertencem aos incrementos seguintes.

Autoridade do runtime verificada antes das alterações e novamente após as verificações: `runtime authority: OK`. Não houve deploy, envio ManyChat/WhatsApp, chamada real de reserva/pagamento, alteração de banco ativo ou alteração de V3/legado. A reserva anteriormente reconciliada não foi repetida nem cancelada.

## Mudanças e remoções reais

| Antes | Agora | Proprietário |
|---|---|---|
| Request com nomes de campos e status de completude, proibindo valores privados em `state_facts` | Valores efetivos do titular/contato em `state_facts` e pessoas completas/parciais em `passengers` | Resolver compartilhado com `resolve_effective_customer`; sem segundo banco de contexto |
| Associação por igualdade de nome | Maya declara `is_holder` na posição existente; controlador valida tipo, unicidade e alterações materiais | Manifesto existente; nenhuma cópia da pessoa para um registro de titular |
| Telefone digitado descartado como dado reutilizável | Telefone de contato persiste no armazenamento existente e volta ao contexto/reserva | Não substitui a identidade/destinatário autenticado ManyChat |
| Limites de quatro trocas no DTO/leitura e DELETE durante gravação | Histórico durável sem esse corte; janela de entrada com orçamento agregado de 64 KiB UTF-8 | Armazenamento de diálogo e contrato de entrada |
| Serializador específico da confirmação com contexto reduzido | Mesmo serializador de contexto, mantendo o contrato de autorização da confirmação | Adaptador do modelo |
| Instruções de presença e coleta apenas da última mensagem | Reutilização de valores, associação semântica explícita e correção da pessoa vinculada | Prompt da Maya e sufixo existente, sem novo revisor |

Os frames normal, pós-consulta e de revisão recebem a mesma projeção de dados; a revisão pós-consulta atualiza a projeção após alterações do turno. Foram removidos `private_customer_fact_names` e `passenger_manifest_status` do request, o helper de nomes de presença, a identificação de titular por nome e os limites de quatro trocas.

O manifesto novo admite leitura de v1 sem inventar titular. O formato de passageiro antigo continua decodificável; a escrita nova declara o papel. A migração de telefone foi exercitada apenas em SQLite temporário, preservando valores e hashes do journal anterior. `v2_ops/serialization.py` teve somente adaptação mecânica ao DTO retirado, mantendo o contrato de telemetria existente; não houve mudança funcional de painel, permissão ou deploy Ops.

## Testes executados

| Evidência | Resultado real |
|---|---|
| Arquivo final de 12 regressões no código anterior exportado por `git archive` | **12 falharam** pelos contratos/comportamentos substituídos; sem falha de coleta do pytest |
| Suítes focais finais no candidato | **272 passed in 5.86s** |
| Suíte integral final, sem deselection | **7 failed, 2216 passed, 2 warnings, 2958 subtests passed in 260.21s** |
| Comparação dos node IDs reprovados com a base histórica previamente verificada | Exatamente os mesmos 7; nenhum ID novo |
| Ruff 0.15.10 nos arquivos Python alterados + teste novo | `All checks passed!` |
| `compileall`, `git diff --check`, fronteiras | Sucesso; `fasttrack-boundaries: OK` |
| Verificação de autoridade | `runtime authority: OK` |

Não declarar a suíte integral verde. As sete incompatibilidades históricas de metadados/artefatos Phase 7 e índice Phase 8 não foram removidas, ocultadas ou reparadas fora do escopo. Seus IDs constam no JSON de evidência.

### Causalidade e revisão

- Teste inicial RED corrigido antes da implementação: a primeira tentativa tinha fixture com enum/assinatura incorretos; esse erro de construção não é evidência de produto. A execução corrigida identificou contratos ausentes. A contraprova final `20-context-causal-base.txt` usa o arquivo final inteiro sobre a fonte pré-mudança e distingue ausência dos novos contratos, inferência por nome, telefone rejeitado e corte de histórico.
- As regressões incluem dois passageiros com o mesmo nome sem associação automática; vínculo tardio ao segundo; correção preservando o papel; troca/desvinculação com ajuste explícito; rejeição de múltiplos titulares; manifesto histórico; valores no payload real; história além de quatro trocas e limite por bytes; migração de telefone; confirmação com contexto completo.
- Jornada de **oito turnos** atravessa o executor e armazenamento reais com requests tipados, reabrindo os bancos entre turnos. No último request, o passageiro 2 continua titular, seus dados são reutilizados e a primeira mensagem permanece no histórico. O fake do modelo é explícito. Zero comandos/relays de negócio foram criados; não prova comportamento de LLM real.
- O diagnóstico integral intermediário teve 8 falhas: as 7 históricas e um teste que exigia associação por nome. Este foi atualizado para exigir o papel explícito; a suíte integral foi reexecutada no código final, sem alterações funcionais durante essa execução.
- Revisão inline, sem subagentes: montagem de cada frame, identidade de canal separada de contato, histórico de escrita e leitura, rejeição de papéis ambíguos, compatibilidade de armazenamento e diff de fronteiras. Kernel transacional, execução de reservas e HTTP dos providers não foram alterados.

## Reproduzir

No worktree:

```bash
env -i HOME=/home/ubuntu PATH=/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH="$PWD" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null UV_CACHE_DIR=/home/ubuntu/.cache/uv \
  uv run --no-project --python 3.12 --with pytest --with pydantic \
  --with PyYAML --with cryptography --with httpx --with fastapi \
  python -m pytest -q --tb=short
```

Suítes focais: `tests/test_v2_service_context.py`, `test_v2_private_customer_facts.py`, `test_v2_passenger_manifest.py`, `test_v2_conversation_context.py`, `test_v2_turn_executor.py`, `test_v2_hermes_model_adapter.py`, `test_v2_customer_collection.py`, `test_v2_terra_prompt.py`, `test_v2_ops_serialization.py`, `test_v2_effective_customer_profile.py`.

Artefatos locais em `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`:

- `18-context-final-focused.txt`, `19-context-final-full.txt`, `20-context-causal-base.txt`, `21-context-static.txt`;
- `INCREMENT-2-SOURCE.json`: hash dos arquivos funcionais/testes antes do encerramento da rodada integral;
- `INCREMENT-2-EVIDENCE.json`: hashes dos logs, resultados e IDs históricos;
- `verify_context_evidence.py`: confere os hashes de fonte antes/depois do commit e igualdade dos IDs de falha, sem tocar estado ativo;
- `context-base-source/`: exportação local do commit anterior com apenas o arquivo de regressões copiado para a contraprova.

## O que isto não resolve

- Mensagens apagadas pelo mecanismo antigo não são recriadas por esta mudança.
- A referência posicional de pessoa vale dentro do grupo existente; não é um cadastro global de identidade. A Maya ainda deve interpretar quem é o titular.
- Não foi avaliada qualidade conversacional com LLM real nem realizado E2E ManyChat/provider. Os testes de jornada são locais e simulados.
- Ainda faltam resultados/pagamentos por componente, ingestão de conclusões assíncronas e retirada das revisões/correções redundantes do coordenador. O serializador foi unificado; **os protocolos de revisão não foram todos removidos**.
- NO-GO de promoção permanece. Nenhuma autorização nova de rollout, escrita externa ou liquidação financeira está implícita nesta entrega.
