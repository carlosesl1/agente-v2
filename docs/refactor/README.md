# Plano da refatoração Agente v2

## Princípio

A Maya interpreta a conversa. Um kernel determinístico decide a transição comercial. Um comando durável representa o que foi autorizado. Um worker executa o provider uma vez. Uma outbox comunica o resultado.

## Estado das fases

| Fase | Estado | Objetivo |
|---|---|---|
| 0. Baseline e governança | **concluída** | Criar trilha limpa, evidência reproduzível, arquitetura e gates |
| 1. Caracterização | **concluída** | Reproduzir todos os incidentes históricos desde payload/estado vazio |
| 2. Domínio tipado e reducer | **concluída** | Criar a máquina de estados pura, sem integrar produção |
| 3. Consultas e `OfferSnapshot` | **concluída e remediada** | Vincular seleção a `offer_id` e evidência fresca |
| 4. Resumo e confirmação únicos | **concluída** | Uma versão, um resumo, uma confirmação posterior |
| 5. Comando e execução duráveis | **concluída e publicada; seis workflows verdes** | Retirar writes do turno síncrono da LLM |
| 6. Handoff e pagamentos | **concluída e publicada; sete workflows verdes** | Separar workflows e side effects obrigatórios/opcionais |
| 7. Migração das fronteiras | bloqueada | Fazer runner/plugin/executor usarem o mesmo kernel |
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

## Regra de avanço

Uma fase só muda para `concluída` quando:

- todos os deliverables existem;
- critérios de aceite foram verificados;
- evidências têm hashes e comandos reproduzíveis;
- riscos novos foram registrados;
- nenhum blocker permanece mascarado;
- o commit da fase foi enviado e conferido no remoto.

Uma fase seguinte não começa automaticamente. Ela exige decisão explícita registrada no documento da fase anterior.
