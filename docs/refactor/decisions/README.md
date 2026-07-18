# Decisões arquiteturais

As ADRs deste diretório registram decisões aceitas da refatoração. Mudanças incompatíveis exigem nova ADR que substitua explicitamente a anterior; não se edita o passado para ocultar mudança de direção.

| ADR | Decisão | Estado |
|---|---|---|
| 0001 | Migração incremental por strangler | aceita |
| 0002 | Kernel determinístico como owner único | aceita |
| 0003 | Oferta canônica identificada por `offer_id` | aceita |
| 0004 | Writes por comando durável fora do turno da LLM | aceita |
| 0005 | Ledger e outbox separados | aceita |
| 0006 | Mesmo digest OCI da canary ao rollout | aceita |

Formato de novas ADRs:

```text
# ADR NNNN — Título
Status
Data
Contexto
Decisão
Consequências
Alternativas rejeitadas
```
