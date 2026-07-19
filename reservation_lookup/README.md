# `reservation_lookup`

Boundary read-only da Fase 3 para consultas Cloudbeds/Bókun e seleção exata de
`OfferSnapshot`.

## Contrato

```text
request tipado
→ adapter
→ ReadTransport injetado
→ responses sanitizadas
→ LookupResult
→ select_offer(offer_id exato, instante explícito)
```

O package:

- não implementa transporte de rede;
- não lê env, filesystem, banco ou configuração global;
- não possui auth, headers ou credenciais;
- não importa provider SDK nem o runtime legado;
- não executa writes;
- recebe relógio e TTL explicitamente;
- retorna somente DTOs imutáveis, hashes e falhas sanitizadas.

## API pública

- `CloudbedsReadAdapter`;
- `BokunReadAdapter`;
- `ReadTransport`, `ReadRequest`, `ReadResponse`;
- `LookupResult`, `LookupProvenance`, `LookupFailure`;
- `offer_id_for`, `lookup_id_for`, `canonical_exchanges`;
- `select_offer`, `revalidate_offer`;
- `run_lookup_properties`.

## Semântica

`offer_id` é um digest semântico opaco, não autenticação criptográfica. Label
pública e provenance não participam da identidade. Provider namespace/ref,
serviço, data/hora, party, preço/moeda e disponibilidade participam.

No Cloudbeds, o `provider_ref` inclui `property_id`, room e rate plan. No Bókun,
inclui product, start time e rate. `lookup_id` é recomputado no contrato público
a partir de provider, query, `observed_at` e pares canônicos
`(request_fingerprint, response_hash)`; hashes de response nunca são tratados
como conjunto desvinculado dos endpoints.

Um lookup é selecionável somente quando positivo, fresco no intervalo
`[observed_at, expires_at)` e com exatamente um
match por `offer_id`. Negative, uncertain, vencido, zero match, múltiplos
matches, label, índice e provider ref falham fechados.

## Limites

- fixtures são sintéticas/sanitizadas;
- Bókun suporta `adults` e `children`, conforme `Party` da Fase 2;
- catálogo, categorias adicionais, runtime transport, auth, writes,
  persistência e rollout pertencem a fases posteriores.
