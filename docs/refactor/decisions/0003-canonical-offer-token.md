# ADR 0003 — Oferta canônica identificada por `offer_id`

- Status: **aceita**
- Data: 2026-07-18

## Contexto

Labels como `n°2` e `nº 2` participaram do matching e impediram a promoção de uma opção válida. Provider IDs não devem ser aceitos da LLM como autoridade.

## Decisão

Cada lookup positivo cria `OfferSnapshot` e um `offer_id` interno opaco. A seleção usa esse token e a evidência fresca correspondente. Labels são somente apresentação/desambiguação.

## Consequências

- variantes tipográficas não controlam autorização;
- IDs técnicos permanecem privados;
- zero/múltiplos matches falham fechados;
- lookup precisa de provenance, TTL e query signature.
