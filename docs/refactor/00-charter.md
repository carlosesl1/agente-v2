# Charter da refatoração

## Problema

O fluxo atual possui proteções importantes, mas seleção, confirmação, tool order, orçamento, execução e evidência são decididos em mais de uma camada. O resultado é fail-closed contra muitos writes errados, porém comportamento comercial imprevisível e falsos verdes nos testes.

## Objetivo de negócio

Concluir atendimentos de hostel, passeios e pagamentos de forma natural e previsível, com rastreabilidade técnica suficiente para provar:

- qual opção foi mostrada;
- qual versão o cliente confirmou;
- qual comando foi criado;
- se o provider foi chamado;
- qual efeito foi confirmado;
- qual mensagem foi entregue.

## Contrato central

```text
lookup positivo e fresco
→ seleção canônica por offer_id
→ draft comercial versionado
→ resumo determinístico persistido/enfileirado
→ confirmação natural em evento posterior
→ um comando durável e idempotente
→ no máximo uma execução no provider
→ resultado tipado
→ mensagem via outbox
```

## Invariantes não negociáveis

1. A LLM interpreta linguagem, mas não autoriza efeito comercial.
2. Nomes públicos não são identidade técnica.
3. O resumo não executa provider nem cria claim comercial.
4. A confirmação vale somente para a mesma `draft_version` e assinatura.
5. Alteração econômica cria nova versão e exige novo resumo.
6. Webhook/mensagem duplicada não cria outro comando.
7. Falha comprovada antes do provider pode ser repetida com a mesma idempotência.
8. Provider possivelmente chamado produz `uncertain/manual_review`; nunca retry cego.
9. Ledger e outbox são separados.
10. Nenhuma promessa futura existe sem continuação durável.
11. Configuração impossível falha no startup/readiness.
12. O mesmo digest OCI validado é o digest promovido.
13. Produção nunca é implantada de working tree sujo.

## Escopo

Inclui:

- hospedagem Cloudbeds;
- passeios Bókun;
- fluxo combinado;
- confirmação;
- reserva/booking;
- pagamentos posteriores;
- handoff;
- ManyChat/outbox;
- idempotência, ledger e observabilidade.

## Fora do escopo inicial

- reescrever SDKs/providers que já funcionam;
- trocar ManyChat;
- trocar Hermes como agente conversacional;
- adotar Temporal/Celery antes de provar necessidade;
- mudar regras comerciais sem decisão específica;
- abrir rollout público durante a refatoração.

## Estratégia

Migração incremental por strangler:

1. caracterizar o legado;
2. construir kernel puro em paralelo;
3. comparar decisões em shadow;
4. migrar uma fronteira por vez;
5. manter fallback operacional explícito;
6. remover o legado somente após cutover comprovado.
