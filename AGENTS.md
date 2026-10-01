# Regras para agentes e contribuidores

## Runtime ativo (obrigatório)

Antes de escolher checkout, branch ou artefato para qualquer tarefa do Agente V2:

1. Leia a autoridade canônica, host-local e sem segredos:

   ```bash
   python3 -m json.tool /home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json
   ```

2. A partir da raiz deste repositório, execute o verificador read-only:

   ```bash
   python3 scripts/runtime_authority.py verify --manifest /home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json
   ```

Só prossiga com exit code `0`. Qualquer ausência, erro ou divergência é **DRIFT** e
exige parada; não ajuste o manifesto para encobrir o runtime observado.

GA, teste isolado e Ops são componentes separados. Depois da verificação, use
`production/ga` para GA ou teste isolado e `production/ops` para Ops. Não inferir
a produção por `main`, nome de worktree, tag genérica, Compose solto ou
repo legado. Os valores ativos mutáveis, inclusive commit e digest, pertencem
somente ao manifesto verificado, nunca a este documento de entrada.

**V3 fora de escopo.** Não ler, editar, testar, reiniciar nem usar V3 como
referência. O procedimento completo está em
`docs/operations/runtime-authority.md`.

## Trilha histórica de refatoração

Esta seção registra a trilha de refatoração; não descreve o runtime corrente.
As regras de fase desta trilha valem somente para tarefas explicitamente de refatoração; nunca servem para escolher checkout nem para inferir o runtime ativo.

### Escopo histórico

Este repositório é a trilha limpa e auditável da refatoração Agente v2. O sistema legado/live é uma dependência observada, não um local para patches oportunistas durante o planejamento.

### Disciplina da refatoração

- A tarefa autorizada no chat atual e seu plano delimitam a implementação. `docs/refactor/ACTIVE.md` orienta a navegação; entradas históricas não autorizam efeitos nem substituem a autoridade de runtime.
- Trabalhe em branch/cópia isolada. A `main` é a base integrada de desenvolvimento, não a fonte da identidade de produção.
- Antes de cada commit funcional execute `python3 scripts/check_fasttrack_boundaries.py`.
- Não importar nem executar o backend legado neste repositório.
- Antes de editar código funcional, identifique o owner da regra e escreva o teste que falha.
- Não avance de fase sem atualizar: deliverables, evidências, riscos, decisões e critérios de aceite.
- Não esconda falhas intermediárias; registre causa, impacto e substituição da evidência.
- Não trate quantidade de testes como prova E2E.
- Não use estado pré-carregado para certificar a construção de estado canônico.
- Não use nomes públicos como identidade técnica de ofertas.
- Não permita que a LLM autorize side effects.
- Não execute provider write dentro do orçamento restante do turno da LLM.
- Ledger de efeito comercial e outbox de comunicação são mecanismos separados.

## Regra geral de privacidade/PII e autoria da Maya

- É proibido iniciar, prolongar ou recomendar trabalho de privacidade/PII sem pedido explícito de Carlos no chat atual. Um pedido explícito deve ter escopo fechado e término verificável; investigação indeterminada é proibida.
- Dados enviados voluntariamente pelo lead podem ser repetidos pela Maya. Nome, partes de nome, e-mail, telefone, país, nascimento, gênero ou dados de passageiros nunca podem, por motivo de privacidade/PII, alterar, mascarar, redigir, substituir, corrigir, bloquear, atrasar ou provocar retry/fail-closed da resposta.
- O controlador nunca inspeciona texto da Maya para detectar PII e nunca cria corpus, parser, regex, lista de partículas, extrator ou gate de exposição. `reply_chunks` e `clarification_question` aceitos permanecem byte a byte como a Maya escreveu.
- Armazenamento tipado, contratos de efeitos e autoridade transacional podem continuar separados da conversa, mas não autorizam reescrever a Maya.
- Trabalho deve avançar em rodadas curtas: um invariante causal por rodada, teste focal antes da próxima e nenhum ciclo aberto de revisão/correção sem limite explícito.

## Segurança

Nunca versionar:

- `.env`, credenciais, auth, tokens e connection strings;
- subscriber IDs, telefones, e-mails ou mensagens reais;
- payloads brutos de ManyChat/Cloudbeds/Bókun/Stripe/Wise;
- bancos, Redis dumps, logs brutos, comprovantes ou screenshots com PII;
- diretórios de runs gerados.

## Evidência mínima por fase

- commit de entrada e commit de saída;
- comandos executados e exit codes;
- testes e relatórios relevantes;
- hashes de artefatos;
- riscos abertos/fechados;
- decisão GO/NO-GO explícita.
