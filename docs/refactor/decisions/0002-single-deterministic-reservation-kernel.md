# ADR 0002 — Kernel determinístico como owner único

- Status: **aceita**
- Data: 2026-07-18

## Contexto

Runner, plugin e executor implementam versões diferentes de seleção, confirmação, budget e tool order. Defesa em profundidade virou duplicação de política.

## Decisão

Criar `ReservationKernel` puro como owner único de seleção, assinatura, FSM e decisão de comando. Runner/plugin/executor podem bloquear por capability/boundary, mas chamam o mesmo primitive e não mantêm máquinas paralelas.

## Consequências

- decisões ficam testáveis sem LLM/provider;
- plugin pode permanecer durante migração;
- policies duplicadas serão removidas progressivamente;
- kernel não faz I/O nem renderiza texto livre.

## Alternativas rejeitadas

- manter guards independentes sincronizados por convenção;
- confiar na LLM para escolher ferramenta/autorizar efeito.
