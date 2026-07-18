# ADR 0005 — Ledger e outbox permanecem separados

- Status: **aceita**
- Data: 2026-07-18

## Contexto

Reserva e comunicação têm garantias diferentes. Falha de mensagem após reserva não pode repetir provider; falha de provider não pode ser mascarada por entrega de mensagem.

## Decisão

Manter dois mecanismos duráveis:

- ledger para exactly-once/certeza do efeito comercial;
- outbox para entrega eventual da mensagem já decidida.

Cada um tem claim, status, lease/recovery e observabilidade próprios.

## Consequências

- mais tabelas/estados, porém sem ambiguidade de responsabilidade;
- fault injection precisa cobrir a janela entre ledger, estado e outbox;
- replay da outbox nunca replana Maya nem repete provider.
