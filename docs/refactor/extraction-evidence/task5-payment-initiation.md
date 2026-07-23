# Task 5 — Proveniência da iniciação financeira

Data da extração: 2026-07-23

As fontes abaixo foram consultadas somente para extrair comportamento técnico. O V2 não as importa nem as executa.

| Método | Fonte somente leitura | SHA-256 |
|---|---|---|
| Stripe | `/home/ubuntu/chapada-leads-hermes/services/stripe_client.py` | `81e47a657b68ed035999c356b7cba221e54866babf14ac2eddabf56cec9b0a5f` |
| Wise | `/home/ubuntu/chapada-leads-hermes/services/wise.py` | `50929d7f3f9bbca8a566e81bd8a1ad569492301efd658aec93acb719450d32a8` |
| Pix/knowledge | `/home/ubuntu/chapada-leads-hermes/services/cerebro.py` | `a4ff4bc7fd6f104293e66ac7291b1f59b2246e2e8ba7b2cc6aac664a5b5796b9` |

## Separação implementada

- Stripe: `PaymentSelection` durável → claim → fence único → link idempotente → receipt ou `manual_review`.
- Wise: instrução vinculada à obrigação; não consulta crédito e não declara settlement.
- Pix: texto vem exclusivamente do knowledge profile autorizado; não declara confirmação bancária.
- Mudança de método preserva anchor e `economic_version`.
- Mudança de valor preserva anchor e incrementa somente `economic_version`.

## Catálogo histórico

Os tools Stripe em `reservation_boundary/dispatch.py` permanecem `BLOCKED_UNMIGRATED`. Eles são a interface histórica de tools e não devem ser reclassificados como settlement. O caminho V2 usa `v2_application.payments` e seu ledger de iniciação; a composição desse caminho ocorrerá no `v2_host`. Manter os tools antigos bloqueados evita ativar uma capability com semântica errada.
