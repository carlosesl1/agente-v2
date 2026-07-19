# ADR 0007 — Handoff e pagamento são workflows irmãos da reserva

- Status: **aceita para a Fase 6**
- Data: 2026-07-19

## Contexto

A reserva, o atendimento humano e o financeiro possuem gatilhos, falhas,
retries, evidências e efeitos posteriores diferentes. Colocar tudo na FSM da
reserva permitiria que falha de e-mail, comprovante ou settlement reabrisse ou
repetisse um ciclo comercial já concluído.

Um motor genérico de side effects também foi considerado, mas não existe um
terceiro workflow que justifique a abstração nesta fase.

## Decisão

Implementar `HandoffWorkflow` e `PaymentWorkflow` como workflows irmãos,
vinculados à reserva somente por âncoras imutáveis quando aplicável.

- handoff pode nascer de intenção estruturada ou revisão manual;
- payment só nasce de reserva com `effect_confirmed`;
- cada workflow tem estado, command, ledger, claim e outbox próprios;
- nenhum workflow cria ou modifica `ReservationCommand`;
- primitivas de transação, lease, fencing e canonicalização podem ser
  reutilizadas sem compartilhar ownership comercial.

## Consequências

- falha de e-mail não bloqueia acknowledgement/fila;
- troca de método não reabre reserva;
- mudança econômica cria somente versão financeira;
- falha de outbox não repete settlement;
- claims Pix/Wise/Stripe podem ser globais sem contaminar o ledger de reserva;
- há mais tabelas e validators, mas as responsabilidades permanecem prováveis
  isoladamente;
- integração com runner/plugin/executor continua adiada para a Fase 7.
