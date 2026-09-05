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

- Em uma tarefa explicitamente de refatoração, execute somente a fase registrada no snapshot histórico de `docs/refactor/README.md` e no handoff atual da tarefa.
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
