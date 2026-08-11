# Regras para agentes e contribuidores

## Escopo

Este repositório é a trilha limpa e auditável da refatoração Agente v2. O sistema legado/live é uma dependência observada, não um local para patches oportunistas durante o planejamento.

## Disciplina obrigatória

- Leia primeiro `docs/refactor/ACTIVE.md` e execute somente sua tarefa `NEXT`. Se `NEXT` não existir ou estiver bloqueada, pare sem escolher trabalho por conta própria.
- A cadeia de autoridade é: `AGENTS.md` → `docs/refactor/ACTIVE.md` → especificação ativa → plano ativo. Documentos antigos são somente referência e não podem substituir essa cadeia.
- Continue pelas tarefas do plano na ordem, atualizando `ACTIVE.md` após cada commit verde. Pare somente diante de blocker material, gate que falhou, decisão comercial ausente ou autorização de efeito real.
- Use sempre a worktree/branch declarada em `ACTIVE.md`; não implemente o fast-track em `main` nem em `/home/ubuntu/chapada-leads-hermes`.
- `/home/ubuntu/chapada-leads-hermes` é fonte somente leitura para extração. É proibido importá-lo, editar seu código, usar seu agente/planner/LeadState ou executá-lo como backend do V2.
- Antes de cada commit, execute o guard de fronteiras indicado pelo plano; imports proibidos bloqueiam avanço.
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
