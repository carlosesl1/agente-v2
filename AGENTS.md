# Regras para agentes e contribuidores

## Escopo

Este repositório é a trilha limpa e auditável da refatoração Agente v2. O sistema legado/live é uma dependência observada, não um local para patches oportunistas durante o planejamento.

## Disciplina obrigatória

- Execute somente a fase declarada como ativa em `docs/refactor/README.md`.
- Antes de editar código funcional, identifique o owner da regra e escreva o teste que falha.
- Não avance de fase sem atualizar: deliverables, evidências, riscos, decisões e critérios de aceite.
- Não esconda falhas intermediárias; registre causa, impacto e substituição da evidência.
- Não trate quantidade de testes como prova E2E.
- Não use estado pré-carregado para certificar a construção de estado canônico.
- Não use nomes públicos como identidade técnica de ofertas.
- Não permita que a LLM autorize side effects.
- Não execute provider write dentro do orçamento restante do turno da LLM.
- Ledger de efeito comercial e outbox de comunicação são mecanismos separados.

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
