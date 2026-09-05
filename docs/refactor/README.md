# Plano da refatoração Agente v2

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
referência. Consulte `docs/operations/runtime-authority.md` antes da cadeia
histórica abaixo.

## Princípio

A Maya interpreta a conversa. Um kernel determinístico decide a transição comercial. Um comando durável representa o que foi autorizado. Um worker executa o provider uma vez. Uma outbox comunica o resultado.

## Snapshot histórico das fases de refatoração

Esta tabela é um snapshot da trilha de refatoração, não um ponteiro operacional.
As regras de fase desta trilha valem somente para tarefas explicitamente de refatoração; nunca servem para escolher checkout nem para inferir o runtime ativo.

| Fase | Estado | Objetivo |
|---|---|---|
| 0. Baseline e governança | **concluída** | Criar trilha limpa, evidência reproduzível, arquitetura e gates |
| 1. Caracterização | **concluída** | Reproduzir todos os incidentes históricos desde payload/estado vazio |
| 2. Domínio tipado e reducer | **concluída** | Criar a máquina de estados pura, sem integrar produção |
| 3. Consultas e `OfferSnapshot` | **concluída e remediada** | Vincular seleção a `offer_id` e evidência fresca |
| 4. Resumo e confirmação únicos | **concluída** | Uma versão, um resumo, uma confirmação posterior |
| 5. Comando e execução duráveis | **concluída e publicada; seis workflows verdes** | Retirar writes do turno síncrono da LLM |
| 6. Handoff e pagamentos | **concluída e publicada; sete workflows verdes** | Separar workflows e side effects obrigatórios/opcionais |
| 7. Migração das fronteiras | **pre-freeze — implementação focused concluída** | Fazer runner/plugin/executor usarem o mesmo kernel |
| 8. Shadow, canary e rollout | bloqueada | Validar e promover o mesmo digest gradualmente |
| 9. Remoção do legado | bloqueada | Eliminar metadata, aliases e policies duplicadas |

## Documentos canônicos

1. `00-charter.md` — objetivo, escopo e invariantes.
2. `01-baseline.md` — estado de partida e limitações.
3. `02-failure-taxonomy.md` — incidentes e classes causais.
4. `03-target-architecture.md` — componentes, estado e contratos.
5. `04-phased-delivery-plan.md` — entregas e gates fase a fase.
6. `05-validation-and-rollout.md` — pirâmide de testes, canary e rollout.
7. `06-risk-register.md` — riscos, sinais e mitigação.
8. `decisions/` — decisões arquiteturais aceitas.
9. `evidence/` — evidência sanitizada e verificável.
10. `phases/` — execução e encerramento de cada fase.

## Regra histórica de avanço da refatoração

Uma fase só muda para `concluída` quando:

- todos os deliverables existem;
- critérios de aceite foram verificados;
- evidências têm hashes e comandos reproduzíveis;
- riscos novos foram registrados;
- nenhum blocker permanece mascarado;
- o commit da fase foi enviado e conferido no remoto.

Uma fase seguinte não começa automaticamente. Ela exige decisão explícita registrada no documento da fase anterior.
