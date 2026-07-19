# Matriz completa estado/evento — Fase 2

Gerada deterministicamente de `reservation_domain.reducer`.

- `evaluate`: existe handler; o evento ainda pode ser aplicado ou rejeitado pelas invariantes.
- `ignore`: não existe transição semântica nesse estado; o reducer registra o evento e não emite comando.
- duplicatas são no-op antes da matriz; eventos fora de ordem são rejeitados antes da matriz.

| Estado | start_search | lookup_recorded | offer_chosen | draft_requested | draft_adjusted | summary_recorded | confirmation_received | execution_started | execution_finished | manual_review_requested | workflow_cancelled | workflow_expired |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `collecting` | evaluate | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | evaluate |
| `searching` | evaluate | evaluate | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | evaluate |
| `offered` | evaluate | ignore | evaluate | ignore | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | evaluate |
| `selected` | evaluate | ignore | ignore | evaluate | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | evaluate |
| `ready_to_summarize` | evaluate | ignore | ignore | ignore | evaluate | evaluate | ignore | ignore | ignore | ignore | evaluate | evaluate |
| `awaiting_confirmation` | evaluate | ignore | ignore | ignore | evaluate | ignore | evaluate | ignore | ignore | ignore | evaluate | evaluate |
| `awaiting_adjustment` | evaluate | ignore | ignore | ignore | evaluate | ignore | ignore | ignore | ignore | ignore | evaluate | evaluate |
| `execution_queued` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | ignore | ignore | ignore | ignore |
| `executing` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | ignore | ignore | ignore |
| `succeeded` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore |
| `failed_before_provider` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore |
| `failed_no_effect` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore |
| `uncertain` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | evaluate | ignore | ignore |
| `manual_review` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore |
| `cancelled` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore |
| `expired` | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore | ignore |

Estados discriminados: **16**.
Eventos discriminados: **12**.
Pares com política explícita: **192**.
