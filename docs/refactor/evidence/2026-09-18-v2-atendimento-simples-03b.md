# V2 atendimento simples — incremento 3B (local)

## Escopo e autorização

Carlos autorizou a continuação local com “Siga” após o 3A. Base:
`d4caf0a1d790dcf0bcae25965bc50b2186533a0c`. Worktree:
`/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
Nenhum deploy, chamada real de modelo/provider/ManyChat, reserva, pagamento,
edição de SQLite ativo, alteração de V3/legado/Ops ou reabertura de gates.
A autoridade do runtime foi lida e verificada antes e depois do desenvolvimento.
Este documento não autoriza rollout nem altera a avaliação NO-GO de GA.

## Mudança implementada

- O projector deriva eventos de conclusão dos ledgers existentes, sem redigir
  mensagens. A identidade de lead vem do dono durável; não de telefone ou
  posição na allowlist. Fontes sem dono são recusadas.
- `CompletionTurn` é um evento interno explícito. O payload da mesma Maya usa
  `trigger=operation_result`, sem inventar uma mensagem do cliente. Recebe
  resultados por componente, referências, estado financeiro, fatos conhecidos
  e mensagens anteriores com seus estados reais de comunicação.
- A continuação permite apenas autoria informativa. A resposta é preservada pelo
  helper canônico `_model_owned_reply`; propostas de efeitos ou coleta não são
  executadas neste caminho. Não há crítico, revisor, filtro de idioma ou texto
  substituto escrito pelo controlador.
- O recibo de turno existente consome as identidades dos eventos atomicamente
  com a resposta e o outbox. Não há novo banco, tabela, migração ou fila.
- Se o cliente chega antes da geração, seu turno pode incorporar os resultados.
  Se a resposta assíncrona já foi gerada mas nenhum chunk iniciou envio, o novo
  commit vincula o recibo anterior e cancela os chunks antigos na mesma transação.
  Os registros cancelados permanecem disponíveis como evidência e contexto.
- Uma mensagem apenas reclamada, ainda sem fence, pode ser consolidada; envio
  iniciado, aceito ou incerto não pode. A disputa é revalidada no commit.
- Dados coletados no turno usam a identidade estável da mensagem original;
  mudanças no conjunto de notificações durante um retry não alteram essa identidade.
- Falha de geração deixa o evento pendente. Falha comprovadamente antes da chamada
  ao canal reutiliza a mensagem salva; envio incerto fica em revisão manual,
  sem regenerar resposta e sem repetir efeito comercial.
- Releases históricos do outbox antigo continuam legíveis e deduplicam a geração.
  Aceite pela API ManyChat não é apresentado como prova de leitura/entrega ao cliente.

## Mapa de remoção

| Antes | Agora |
|---|---|
| Prosa de conclusão em helpers `_confirmation_text`/`_terminal_failure_text`/`_payment_text` | Contexto autenticado → mesma Maya → helper único de autoria |
| Projector enfileira mensagem no outbox legado | Recibo/outbox canônicos do turno, com prova dos eventos consumidos |
| Destinatário fixo de configuração em controlled-write | Resolução pelo comando/pagamento durável |
| Notificação desconectada de resposta nova do cliente | Consolidação transacional antes do dispatch |

O leitor/transporte do outbox antigo é preservado por compatibilidade. Não existe
um segundo gerador ativo de prosa para estes eventos. Protocolos redundantes de
revisão de turnos normais continuam como escopo separado do incremento 4.

## Verificação

Artefatos reais: `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`.

- `39-autoria-final-focused.txt`: **380 passed**, incluindo o gate AST de autoria.
- `38-autoria-causal-base.txt`: **22 falhas** na base anterior, sem erro de coleta.
  A base não possui o novo contrato de continuação. Esses mesmos cenários passam
  no candidato; os helpers de teste não foram usados para alterar o código base.
- `36-autoria-race-red.txt`: reproduz o conflito de identidade durante coleta de
  dado + avanço do fence; o cenário corrigido passa no lote final.
- `37-autoria-full.txt`: rodada intermediária encontrou uma violação do helper de
  autoria além das sete falhas históricas. Foi corrigida, não ignorada.
- `40-autoria-final-full.txt`: **2.261 passed, 7 failed, 2.958 subtests passed**.
  As sete identidades de falha são exatamente as de `29-results-final-full.txt`;
  o comparador executado falha se qualquer ID novo aparecer. Suíte integral NÃO verde.
- `42-autoria-evidence.json`: hashes dos arquivos e logs; fingerprint do candidato
  `a0e68aee380d692317880e6b224f09dbcef33be890b22328d0b26240381ee13b`.
  Selagem reproduzível por `verify_authorship_evidence.py`; causalidade por
  `verify_authorship_causal.py` no diretório de artefatos.
- Ruff **0.15.10**, seleção `E9,F,I` nos arquivos Python alterados: OK.
  Compilação dos 19 arquivos Python alterados, fronteiras e `git diff --check`: OK.
  Não se declara o Ruff global sem findings: há dívida pré-existente fora do diff.
- Reabertura real de SQLite, hashes/recibos, replay de inbound e de conclusão,
  crash no commit, prioridade de inbox, disputa geração/cliente e disputa
  cliente/dispatch foram exercitados. Aceite, not-called e resultado desconhecido
  foram percorridos pelo worker de entrega real com transporte simulado.
- O teste de composição reconstrói a linhagem por recibo/relay real, fecha e reabre
  os bancos, fecha o gate de writes e conclui a comunicação sem nova reserva.

## Limites

São testes offline com modelo e transportes simulados. Demonstram fluxo de
contexto, persistência, contratos, recuperação e ausência de novos comandos nos
cenários exercitados; não certificam qualidade de uma conversa natural com a
Maya real nem sucesso de provider/WhatsApp ao vivo. A suíte integral mantém sua
avaliação própria; falhas históricas não equivalem a autorização de publicação.
Revisão de diff foi feita inline, sem subagentes. Não houve push, merge ou deploy.
