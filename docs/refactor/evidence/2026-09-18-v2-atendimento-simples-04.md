# V2 atendimento simples — incremento 4 (local)

## Escopo e autorização

Carlos autorizou a continuação com “Siga” após o resumo das pendências do 3B.
Base local `dec167115278fdc1764fb5380accdcec3f5f028e`; worktree
`/home/ubuntu/agente-v2/.worktrees/atendimento-simples-727d3625`.
Especificação e plano em `docs/superpowers/`. Implementação e revisão inline,
sem subagentes. Nenhum deploy, push, merge, LLM/provider/WhatsApp real,
escrita em banco ativo, mudança em V3/legado ou reabertura de gates.
A alteração local do serializer de requests em `v2_ops` apenas retira campos
extintos; não muda permissões, operações ou UI de Maya Ops.

## Mudança implementada

O caminho normal deixa de pedir uma segunda interpretação de uma proposta
conversacional válida. Resposta informativa sem ferramenta usa uma chamada;
consulta real usa interpretação inicial e continuação com os resultados.
O reparo limitado de saída estruturalmente inválida permanece — não é revisor
de prosa válida. Retries por concorrência continuam seguindo o protocolo de
commit existente; as contagens acima se referem ao turno sem conflito.

| Caminho anterior | Dono atual / remoção |
|---|---|
| `confirmation_review_required` e prompt/decoder especial | `pending_action` completo no request normal; parser V8 faz binding, kernel valida prazo e escopo |
| `selection_review_required` antes/depois de consulta | Maya emite `select` explícito na continuação comum com escolha vinculada à observação |
| `progress_review_required` / `_maybe_progress_review` | Proposta informativa válida aceita, sem inferir insuficiência pela estrutura |
| `recap_reuse_required` e sufixo | Histórico já disponível; Maya decide se precisa consultar novamente |
| `public_reply_correction_reasons`, orçamento e helper | Sem reescrita compensatória; contratos inválidos permanecem erros explícitos |
| `_repair_requested_activity_selection` | Nenhuma promoção automática de `inform` para `select` |
| Normalização que apagava a intenção selecionada | `derive_selection_reads` apenas deriva refresh de seleção explicitamente tipada |
| `preserve_initial_adjustment` forçava intenção após consulta | Contrato impede restaurar escopo revogado, sem reescrever a intenção final |
| `is_regressive_post_command_reply` e `apply_positive_grounding` sem consumidor | Removidos, sem substituição por classificador de texto |
| Tipos de confirmação restrita e testes exclusivos desses protocolos | Removidos; testes de contexto, autoria, binding, efeitos e replay mantidos/migrados |

Flags também saíram do contrato, wire, prompt versionado, serializer Ops e
validador offline. Tipos históricos de trace continuam legíveis; nenhum ramo
normal novo emite revisão semântica. Não há banco, tabela, fila ou IA adicional.

## Invariantes e correções verificadas

- Inform não vira confirmação porque existe um resumo pendente. Confirmação
  válida recebe o mesmo contexto normal e é vinculada ao resumo exato.
- Resultado de ferramenta não é autorização nova. Continuação não pode criar
  `confirm` sem confirmação inicial vinculada, nem restaurar um escopo revogado.
  Os dois cenários falharam antes da guarda e passam depois (`46-...`).
- Fatos comerciais extraídos no próprio turno chegam à continuação, além dos
  fatos previamente persistidos. A incorporação precede o vínculo do manifesto
  de passageiros, eliminando a perda de composição conhecida do grupo.
- Seleção inválida, conflito com workflow já comandado e autorização expirada
  não ganham prosa substituta nem nova chamada corretora: falham explicitamente,
  sem commit público/comercial nos cenários de rejeição exercitados. Isso não
  equivale a afirmar que o cliente recebeu uma resposta nesses casos; o caminho
  de erro/recuperação existente permanece. Qualificação conversacional é separada.
- Validação de fornecedor, frescor, confirmação, idempotência, command/relay,
  replay e continuação assíncrona do 3B permanecem nos donos existentes.
  Não há regex/gatilho de mensagem nem fiscal de idioma/prosa.

## Verificação real

Artefatos em `/home/ubuntu/workspace/v2-simplificacao-atendimento-727d3625/`:

- `55-protocolos-final-focused.txt`: **363 passed**. Inclui executor, adapter,
  contexto, prompt, produção, autoria AST, conclusão e contratos de ferramentas.
- `54-protocolos-causal-base.txt`: os **15 testes novos falham na base exata**;
  no candidato passam. Sem erros de import/coleta. Falhas: flags presentes,
  chamadas extras, ausência de rejeição da elevação de autoridade e fatos do
  turno ausentes no pós-tool. Não se atribui causalidade independente a cada
  assert posterior quando um cenário para em um assert anterior.
- `46-protocolos-authority-red.txt`: **2 failed, 11 passed** antes da proteção
  de autoridade; reproduções comportamentais, não erro do harness.
- `49-protocolos-full.txt`: rodada intermediária com 7 falhas históricas e 3
  testes ainda referenciando o contrato antigo; estes foram migrados.
- `56-protocolos-final-full.txt`: **2247 passed, 7 failed, 2958 subtests passed**,
  2 avisos de dependências. IDs de falha comparados automaticamente com
  `40-autoria-final-full.txt`: exatamente os mesmos sete, nenhuma falha nova.
- A quantidade total muda porque testes exclusivos de contratos/helpers
  retirados foram removidos; não foram excluídos testes de efeitos, concorrência
  ou replay para produzir aprovação.
- Ruff 0.15.10 (`E9,F,I` nos Python alterados), compilação de 21 Python alterados,
  `check_fasttrack_boundaries.py`, `git diff --check`: OK. Não é alegação de
  lint global sem dívida. Busca nos módulos executáveis não encontrou as flags
  e helpers de revisão retirados.
- `57-protocolos-tested-source.json` fixa hashes dos arquivos testados;
  `59-protocolos-evidence.json` sela fonte/logs. Fingerprint:
  `850b28f72db1855df7ac5f8a25ddfa9a8f4dbb581a39a6e74883b17aa226d8cb`.
  Reprodução: `verify_protocols_causal.py` e `verify_protocols_evidence.py`.
- Autoridade lida e verificada antes/depois: `runtime authority: OK`.

## Limites e próximo gate

**Suíte integral não verde.** As sete falhas históricas continuam sendo dívida
real, não aprovação de integração/publicação. Incrementos 1, 2, 3A, 3B e 4
estão implementados localmente; isso não muda o runtime declarado pelo manifest.
Todos os modelos e transportes desta qualificação foram simulados, com SQLite
real temporário. Não houve medição de latência de LLM real ou certificação de
qualidade natural da conversa. A redução de chamadas está demonstrada nas
trilhas exercitadas, não em atendimento ao vivo.

Faltam qualificação com a Maya real em isolamento e tratamento das falhas
históricas. Promoção/reteste com efeitos reais requer autorização separada,
reconciliação do estado existente e os gates de rollout. GA permanece NO-GO.
