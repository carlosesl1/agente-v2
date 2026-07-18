# Fase 3 — Consultas e seleção por OfferSnapshot

## Status

`em execução`

Aberta em `2026-07-18T23:24:58Z`, a partir do commit-base
`e318a2f1cad6fbeda3db11a0368f7b762ae84cdf`.

## Objetivo

Remover labels e payloads produzidos pela LLM da identidade técnica de
consultas e seleção. Adapters read-only Cloudbeds/Bókun devem produzir
`LookupEvidence` e `OfferSnapshot` canônicos, selecionáveis somente por
`offer_id` opaco e evidência fresca.

## Design aprovado

- [spec da Fase 3](../../superpowers/specs/2026-07-18-phase-3-lookup-adapters-design.md)

Boundary:

```text
request tipado
→ adapter read-only
→ transporte obrigatório injetado
→ response sanitizada
→ normalizador estrito
→ LookupResult
```

Não existe transporte de rede padrão nesta fase.

## Owners

- request DTO: forma e IDs técnicos internos necessários ao lookup;
- adapter: request provider + normalização de schema;
- identity: `offer_id`, `lookup_id` e hashes canônicos;
- provenance: TTL, request fingerprints e response hashes;
- selection: frescor, status positivo e match exato único;
- fixture transport: prova boundary sem rede.

## Escopo autorizado

- package `reservation_lookup` sem I/O real;
- Cloudbeds GET availability + rate plans;
- Bókun GET metadata + availability;
- transport `Protocol` injetado;
- fixtures HTTP sintéticas e sanitizadas;
- IDs opacos e deterministicamente derivados;
- revalidação por mudança executável;
- testes RED, contract, metamórficos, property e mutation;
- validador/CI/evidências da Fase 3.

## Fora do escopo

- editar ou importar o legado;
- provider/network live, auth ou credenciais;
- writes Cloudbeds/Bókun;
- resolver nome público para product ID;
- categorias Bókun além de adults/children;
- renderer, confirmação, persistência, worker, ledger, outbox;
- runner/plugin/executor, deploy ou rollout;
- iniciar a Fase 4.

## Invariantes

1. somente GET pode atravessar o transport;
2. nenhum adapter tem transporte real/default;
3. product/property IDs vêm de contexto interno, nunca de label do lead;
4. `offer_id` exclui label e provenance;
5. todo campo técnico/econômico executável participa da identidade;
6. status positivo exige offers completos e bookable;
7. zero/múltiplos matches, TTL vencido e status não positivo falham fechados;
8. label-only preserva seleção; mudança executável invalida;
9. falha HTTP/schema/transport produz `UNCERTAIN` e zero offers;
10. raw payload, auth, headers e PII não saem no resultado;
11. fixtures são exclusivamente sintéticas;
12. legado permanece somente leitura.

## Entregáveis

- [ ] spec e plano detalhado;
- [ ] tipos e transport protocol;
- [ ] identidade opaca canônica;
- [ ] adapter Cloudbeds;
- [ ] adapter Bókun;
- [ ] seleção e revalidação fail-closed;
- [ ] fixtures sanitizadas e manifest;
- [ ] tests RED/contract/metamórficos/property;
- [ ] mutation testing;
- [ ] source map, riscos, evidências e hashes;
- [ ] validador local e CI;
- [ ] revisão adversarial;
- [ ] commits de entrada, implementação e closeout remotos verificados.

## Gate

1. paths/query/método e ordem dos requests são exatos;
2. ID/label divergente não autoriza;
3. label tipograficamente equivalente não quebra seleção;
4. provider ref/data/hora/party/preço/moeda/availability alterados invalidam;
5. zero/múltiplos matches falham fechados;
6. TTL/provenance/status são validados;
7. malformed/partial/provider error não gera offer autorizável;
8. property workload e mutantes obrigatórios passam;
9. package não importa rede, filesystem write, env, provider SDK ou legado;
10. validadores 0–3, scans, compileall, diff, hashes e CI passam;
11. `HEAD == origin/main == remote` no SHA final.

## Baseline legado somente leitura

- HEAD: `57408d8b2040399bc25ee7957505208079458884`;
- status entries preexistentes: `80`;
- status SHA-256: `77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b`.

## Rollback

Reverter somente os commits da Fase 3 no repositório novo. Não há ação live.

## Decisão de avanço

Pendente. A Fase 4 permanece bloqueada até closeout formal e direção explícita.
