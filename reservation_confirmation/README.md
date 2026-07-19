# `reservation_confirmation`

Boundary puro da Fase 4 para apresentação e classificação de confirmação.

## Regras

- não executa I/O, rede, provider, LLM ou entrega;
- não persiste mensagem bruta;
- `DecisionCandidate` não contém versão, assinatura, oferta, provider ou operação;
- `RenderedSummary` declara `claim_status="none"` e `private_fields=()`;
- IDs e hashes são identidades determinísticas, não autenticação;
- somente o reducer do domínio pode construir `ReservationCommand`.

`render_summary` projeta deterministicamente PT-BR/EN, calcula totais em
`Decimal` e rejeita qualquer ID privado presente no texto. `prepare_summary`
deriva IDs de resumo/outbox/evento e entrega o texto exato junto ao
`SummaryRecorded`; não envia nem persiste nada.

`ConfirmationClassifier` é model-agnostic. A implementação de referência cobre
um corpus sintético PT/EN com precedência fail-closed; contexto ausente, sinais
mistos, exception ou retorno inválido produzem `AMBIGUOUS`.

O trusted binding será adicionado em ciclo TDD separado. A API pública permanece
fechada e tipada.
