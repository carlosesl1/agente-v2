# Fase 3 — Lookup adapters e OfferSnapshot — Design

**Data:** 2026-07-18
**Status:** aprovado para implementação
**Fase:** `phase-03-lookups-and-offer-snapshots`

## Objetivo

Implementar consultas read-only Cloudbeds e Bókun que transformam respostas
HTTP sanitizadas em `LookupEvidence` e `OfferSnapshot` canônicos. A identidade
técnica passa a ser um `offer_id` opaco; labels públicas nunca autorizam
seleção.

## Decisão de boundary

A alternativa aprovada é:

```text
request tipado
→ adapter read-only
→ ReadTransport obrigatório e injetado
→ response sanitizada
→ normalizador estrito
→ LookupResult
```

O pacote não terá transporte de rede padrão. Testes usam transporte de fixtures
que registra cada request e devolve snapshots sintéticos. Isso prova método,
path, query, ordem e parsing sem credencial ou rede real.

Alternativas rejeitadas:

- normalizadores sem request builder: não provam o boundary;
- wrappers do legado: preservariam aliases e acoplamento que a refatoração deve
  remover.

## Componentes

### `reservation_lookup/types.py`

Tipos imutáveis:

- `ProviderKind`: `cloudbeds | bokun`;
- `ReadRequest(method, path, query)`;
- `ReadResponse(status_code, body)`;
- `LookupFailure(code, detail)`;
- `LookupProvenance(provider, request_fingerprints, response_hashes)`;
- `LookupResult(query, evidence, provenance, offers, failures)`;
- `CloudbedsLookupRequest(property_id, query)`;
- `BokunLookupRequest(product_id, query)`;
- `SelectionErrorCode` e `SelectionRejected`.

`ReadTransport` é um `Protocol` com:

```python
def send(self, request: ReadRequest) -> ReadResponse: ...
```

`ReadRequest` aceita somente `GET`, path absoluto relativo (`/...`) e query
ordenável sem headers, auth ou body. Autenticação pertencerá à futura fronteira
de runtime, não ao contrato desta fase.

### `reservation_lookup/identity.py`

Funções canônicas:

```python
def offer_id_for(*, provider: ProviderKind, offer: OfferSnapshot) -> str: ...
def lookup_id_for(*, provider: ProviderKind, query: SearchQuery,
                  observed_at: datetime, response_hashes: tuple[str, ...]) -> str: ...
def snapshot_hash_for(responses: tuple[ReadResponse, ...]) -> str: ...
```

Formato:

```text
offer:<sha256 hex>
lookup:<sha256 hex>
```

Campos de identidade do offer:

- provider namespace;
- provider ref privado;
- service;
- datas/horário;
- party;
- preço/moeda;
- disponibilidade.

Excluídos:

- `public_label`;
- `lookup_id`;
- ordem de entrada;
- timestamps/provenance.

Consequência: variação de label preserva o `offer_id`; qualquer mudança
executável gera outro ID.

### `reservation_lookup/cloudbeds.py`

Interface:

```python
class CloudbedsReadAdapter:
    def __init__(self, transport: ReadTransport): ...
    def lookup(self, request: CloudbedsLookupRequest, *,
               observed_at: datetime, ttl: timedelta) -> LookupResult: ...
```

Ordem de requests:

1. `GET /api/v1.3/getAvailableRoomTypes`;
2. `GET /api/v1.2/getRatePlans`.

Query exata:

```text
propertyID
startDate
endDate
adults
children
detailedRates=true
```

Contrato de resposta sanitizada:

- envelope objeto com `data` array;
- quarto exige `roomTypeID`, `roomTypeName`, `roomsAvailable`, `ratePlanID`,
  `roomRateDetailed` e moeda;
- rate plan referenciado precisa existir na segunda resposta;
- cada noite solicitada precisa de linha diária disponível e preço finito;
- total é soma determinística das noites;
- `provider_ref` é `cloudbeds.room.<room>.rate.<rate>`;
- somente opções completas, com preço e disponibilidade positivas, entram no
  resultado.

### `reservation_lookup/bokun.py`

Interface:

```python
class BokunReadAdapter:
    def __init__(self, transport: ReadTransport): ...
    def lookup(self, request: BokunLookupRequest, *,
               observed_at: datetime, ttl: timedelta) -> LookupResult: ...
```

Ordem de requests:

1. `GET /activity.json/<product_id>` com `lang=pt_BR,currency=BRL`;
2. `GET /activity.json/<product_id>/availabilities` com
   `start,end,currency=BRL`.

O `product_id` é canônico e interno. Nome do passeio não faz parte do request e
nunca é fallback.

Contrato de resposta sanitizada:

- metadata exige ID igual ao `product_id` e título público;
- availability exige date, `startTimeId`, horário, disponibilidade, total e
  moeda;
- opções fora da query, indisponíveis, sem ID/hora ou sem preço são rejeitadas;
- `provider_ref` é
  `bokun.product.<product>.start.<start_time_id>.rate.<rate_id>`;
- esta fase suporta somente `Party(adults, children)` já fechado na Fase 2;
  categorias adicionais não são colapsadas nem inferidas.

### `reservation_lookup/selection.py`

```python
def select_offer(result: LookupResult, *, offer_id: str,
                 at: datetime) -> OfferSnapshot: ...

def revalidate_offer(previous: OfferSnapshot, fresh: LookupResult, *,
                     at: datetime) -> OfferSnapshot: ...
```

`select_offer` exige:

1. `LookupStatus.POSITIVE`;
2. evidência fresca no instante explícito;
3. string no formato de `offer_id`;
4. exatamente um match.

Falhas estáveis:

- `lookup_not_positive`;
- `lookup_expired`;
- `offer_id_not_found`;
- `offer_id_not_unique`;
- `offer_changed` na revalidação.

Label, índice (`nº 2`) e provider ref não são aceitos como `offer_id`.

## Status e falhas de lookup

- `POSITIVE`: pelo menos um offer válido e zero falhas;
- `NEGATIVE`: responses válidas, nenhuma opção bookable e zero falhas;
- `UNCERTAIN`: HTTP não-2xx, transport exception, schema inválido, mismatch de
  provider ou resposta parcial.

Não existe resultado parcialmente autorizável. Qualquer falha de uma das
respostas zera offers e produz `UNCERTAIN`.

## TTL e provenance

- `observed_at` é fornecido pelo caller; não há relógio global;
- TTL explícito deve ser maior que zero e no máximo 15 minutos;
- `expires_at = observed_at + ttl`;
- `snapshot_hash` cobre as duas responses sanitizadas em JSON canônico;
- request fingerprints cobrem método/path/query;
- response hashes ficam na provenance;
- body bruto, headers e credenciais não entram em `LookupResult`.

## Fixtures

Diretório:

```text
tests/fixtures/phase3/cloudbeds/
tests/fixtures/phase3/bokun/
```

Somente dados sintéticos:

- Cloudbeds: quartos, rate plans, zero availability, rate ausente, payload
  inválido, label variante e preço alterado;
- Bókun: metadata, availability, sold out, ID divergente, múltiplos horários,
  label variante e preço alterado.

Cada fixture terá SHA-256 no manifesto da fase. Nenhuma fixture virá de captura
live.

## Invariantes e testes

1. adapters fazem somente GET;
2. requests têm paths/query exatos e ordem determinística;
3. nenhum raw body aparece no resultado;
4. oferta só é selecionável pelo `offer_id` exato;
5. label tipograficamente equivalente não altera identidade;
6. provider ref, data, hora, party, preço, moeda ou availability alterados mudam
   identidade;
7. zero/múltiplos matches falham fechados;
8. lookup vencido, negativo ou incerto não autoriza;
9. mudança executável invalida seleção; label-only não;
10. malformed/partial/provider error retorna `UNCERTAIN` e zero offers;
11. resultado determinístico independe da ordem das opções/respostas internas;
12. imports de rede, provider SDK, filesystem write, env e legado são proibidos
    no package.

Mutation tests mínimos:

- incluir label no hash do offer;
- excluir preço do hash;
- aceitar provider ref como seleção;
- aceitar lookup expirado;
- escolher primeiro match quando há duplicata;
- tolerar rate plan ausente;
- transformar schema error em negativo;
- permitir método diferente de GET.

Todos devem ser mortos.

## Validação

- testes unitários e contract tests;
- pelo menos 50 mil casos metamórficos/property de identidade e seleção;
- fixture manifest e source map;
- validador da Fase 3 exige arquivos tracked/staged, pure imports, hashes e gates
  das Fases 0–2;
- GitHub Actions próprio;
- revisão adversarial sobre identidade, TTL, parsing e falso verde.

## Fonte somente leitura

O design foi informado, sem importação ou modificação, por:

- `services/cloudbeds.py::_normalize_availability_response` e
  `_normalize_room_option`;
- `services/bokun.py::_normalize_activity_availability` e
  `_normalize_activity_option`;
- `tools/cloudbeds_v2_tools.py`;
- `tools/bokun_v2_tools.py`;
- testes v2 correspondentes.

A origem permanece no HEAD
`57408d8b2040399bc25ee7957505208079458884`. O novo package não importa o
legado.

## Fora do escopo

- rede real, credenciais, providers live ou autenticação;
- writes, carrinho, reserva, pagamento ou side effects;
- catálogo conversacional/nome → product ID;
- renderer, confirmação ou classificação LLM;
- persistência, worker, ledger ou outbox;
- integração com runner/plugin/executor;
- deploy, canary ou rollout.

## Rollback

A fase adiciona package read-only, fixtures, testes, documentos e CI no
repositório novo. Rollback é reversão dos commits; não há ação live.
