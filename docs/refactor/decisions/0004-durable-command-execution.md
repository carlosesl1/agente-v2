# ADR 0004 — Writes por comando durável, fora do turno da LLM

- Status: **aceita**
- Data: 2026-07-18

## Contexto

O write atual depende do tempo restante do agente e do request público. A configuração observada exige 302 segundos de budget dentro de um turno de 120 segundos.

## Decisão

Uma confirmação válida persiste um `ReservationCommand` imutável e idempotente. Um worker separado reivindica e executa o comando. O turno da LLM não chama o provider write.

## Consequências

- timeout do provider deixa de competir com a LLM;
- promessa futura pode ser vinculada a continuação real;
- exige store, lease, outcome e reconciliação;
- thread/background sem persistência não satisfaz a decisão.
